"""
Adaptive online speaker clustering — Option #3 from the design discussion.

Why this exists
---------------
The pre-enrolled MFCC fingerprint approach (core/voiceprint.py) struggles
when the customer is physically in the same room as the agent: their voices
get captured through the same mic/room/codec, MFCC similarities cluster too
tightly, and same-room speakers cannot be separated reliably.

This module takes a different angle. Instead of comparing every chunk to a
pre-recorded fingerprint, it learns the speakers DURING the live call:

  1. Calibration (first N seconds): collect embeddings of every speech chunk.
  2. Cluster (K=2): split the collected embeddings into two groups.
  3. Label: the cluster with MORE chunks during calibration = AGENT
     (because the agent typically talks more in the opening — greeting,
     introduction, walking through products).
  4. Lock & serve: for each subsequent chunk, assign it to the nearest
     cluster centroid. Chunks assigned to the agent cluster are flagged
     so the frontend can drop them.

Centroids drift slowly via running mean so the system tracks small voice
changes within a call.

Compared to the pre-enrolled approach:
  +  No enrolment UX
  +  Adapts to this call's mic + room + speakers
  -  Mislabels if the customer talks more than the agent in the first 30s
  -  Same fundamental MFCC limitations for very-similar voices

This module is parallel to core/voiceprint.py — it does NOT replace or
modify it. The legacy fingerprint flow keeps working as-is; this is wired
behind separate /api/voice/adaptive/* endpoints for side-by-side testing.

Public API:
  session = adaptive_engine.start_session()                 -> session_id
  result  = adaptive_engine.process(session_id, audio_bytes,
                                     content_type="audio/webm")
            -> {is_agent, phase, ...}
  adaptive_engine.reset(session_id)
  adaptive_engine.stats(session_id)
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# Reuse the existing MFCC pipeline so we don't ship two embedding stacks.
# _MFCCBackend produces a 120-dim L2-normalised vector; _decode_audio_to_pcm
# handles webm/opus/wav from the browser.
from core.voiceprint import (
    _MFCCBackend,
    _decode_audio_to_pcm,
    _has_speech,
)

log = logging.getLogger("hdfc.adaptive")


# ── Tunables ─────────────────────────────────────────────────────────────────
# How many speech chunks we need before we attempt to split into two clusters.
# At ~3 sec per chunk this lands around 30 sec of calibration if both
# speakers are active. Too low = unstable clusters; too high = long warm-up.
CALIB_MIN_CHUNKS = int(os.environ.get("ADAPTIVE_CALIB_MIN_CHUNKS", "8"))

# Once we have at least CALIB_MIN_CHUNKS embeddings, we also require the two
# clusters to be visually separable before we lock them. If the inter-centroid
# cosine distance is below this floor it means both clusters are basically
# the same speaker — we keep collecting rather than locking onto noise.
MIN_CLUSTER_SEPARATION = float(os.environ.get("ADAPTIVE_MIN_SEPARATION", "0.05"))

# After lock, each assignment also nudges its centroid by this learning rate
# so we track slow drift (cold/warming voice, mic position changes).
# 0.0 disables drift; ~0.05 = gentle online update.
CENTROID_LR = float(os.environ.get("ADAPTIVE_CENTROID_LR", "0.05"))

# Distance margin used at serve time. Even after lock, a chunk whose distance
# to the nearest centroid exceeds (other_distance - MARGIN) is considered
# ambiguous — we default to "not agent" (let it through to the LLM) rather
# than guess. Better to over-include than to silently drop a customer turn.
AMBIGUITY_MARGIN = float(os.environ.get("ADAPTIVE_AMBIGUITY_MARGIN", "0.02"))

# Cross-talk (overlap) detection: if the cosine similarities to BOTH centroids
# are within this margin of each other AND both exceed the floor, the chunk is
# likely a mix of two simultaneous speakers.
OVERLAP_SIM_MARGIN = float(os.environ.get("ADAPTIVE_OVERLAP_MARGIN", "0.15"))
OVERLAP_SIM_FLOOR  = float(os.environ.get("ADAPTIVE_OVERLAP_FLOOR",  "0.40"))

# After this many confirmed (non-overlap) turns post-lock, centroids stop
# recomputing from scratch and only update via EMA.  Prevents sudden label
# flips after the model has stabilised.
LOCK_STABLE_TURNS = int(os.environ.get("ADAPTIVE_LOCK_STABLE_TURNS", "5"))

# EMA alpha for post-lock centroid update: new_centroid = alpha*emb + (1-alpha)*old
# 0.10 = 10% new, 90% old — slow drift to track voice change without flipping.
EMA_ALPHA = float(os.environ.get("ADAPTIVE_EMA_ALPHA", "0.10"))

# Session lifetime in seconds. Stale sessions get garbage-collected so we
# don't leak memory when the frontend forgets to call /reset.
SESSION_TTL_S = int(os.environ.get("ADAPTIVE_SESSION_TTL_S", "3600"))


# ── Per-session state ────────────────────────────────────────────────────────

@dataclass
class _Session:
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    # Calibration buffer (list of L2-normalised 120-dim vectors)
    embeddings: list[np.ndarray] = field(default_factory=list)

    # Locked clusters (set after calibration completes)
    agent_centroid: Optional[np.ndarray] = None
    customer_centroid: Optional[np.ndarray] = None
    locked: bool = False

    # Bookkeeping
    chunks_seen: int = 0
    chunks_flagged_agent: int = 0
    chunks_flagged_customer: int = 0
    chunks_skipped_silent: int = 0

    # Per-chunk cluster assignment counts during calibration (used to pick
    # which cluster is "agent" once we run k-means)
    last_calib_labels: Optional[np.ndarray] = None

    # ── Post-lock stability counter ───────────────────────────────────────
    # Counts confirmed (non-overlap) turns after lock.  Once >= LOCK_STABLE_TURNS
    # the centroids are considered stable and only drift via EMA.
    confirmed_post_lock_turns: int = 0

    # ── Person-label state (new June 2026 two-speaker path) ───────────────
    # first_embedding anchors "Person 1" for the whole session.
    # second_embedding is the first embedding that differs enough from Person 1;
    # once we have both we compare new utterances to BOTH prototypes (nearest wins).
    first_embedding:  Optional[np.ndarray] = None
    second_embedding: Optional[np.ndarray] = None   # Person 2 prototype
    # Running means so prototypes improve over the session (not just the first chunk)
    p1_sum: Optional[np.ndarray] = None
    p1_count: int = 0
    p2_sum: Optional[np.ndarray] = None
    p2_count: int = 0
    # After lock, which K-means cluster (0=agent_centroid, 1=customer_centroid)
    # corresponds to Person 1?  Set on first label_utterance() call post-lock.
    person1_cluster: Optional[int] = None


# ── Tiny K-means (k=2, cosine-distance, deterministic-ish) ───────────────────

def _kmeans2_cosine(X: np.ndarray, max_iter: int = 50) -> tuple[np.ndarray, np.ndarray]:
    """
    Two-cluster K-means on L2-normalised vectors using cosine distance
    (which on the unit sphere is equivalent to Euclidean up to a constant).

    Returns:
        labels    — (N,) int array of {0, 1}
        centroids — (2, D) float32 array, each row L2-normalised
    """
    n = X.shape[0]
    if n < 2:
        raise ValueError(f"K-means needs at least 2 samples, got {n}")

    # Init via "farthest-point" — pick the pair of samples with the largest
    # cosine distance as initial centroids. This is deterministic given the
    # input order, and avoids the random-seed instability of plain K-means++.
    sims = X @ X.T  # (N, N) cosine sims (X is L2-normalised)
    i, j = np.unravel_index(np.argmin(sims), sims.shape)
    centroids = np.vstack([X[i], X[j]]).astype(np.float32)

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(max_iter):
        # Assign each point to the nearest centroid (= max cosine sim)
        sims = X @ centroids.T  # (N, 2)
        new_labels = np.argmax(sims, axis=1).astype(np.int32)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels

        # Recompute centroids (mean of cluster members, then re-normalise)
        new_centroids = np.zeros_like(centroids)
        for k in range(2):
            members = X[labels == k]
            if members.shape[0] == 0:
                # Degenerate empty cluster — keep the old centroid
                new_centroids[k] = centroids[k]
            else:
                m = members.mean(axis=0)
                norm = np.linalg.norm(m)
                new_centroids[k] = (m / norm) if norm > 0 else centroids[k]
        centroids = new_centroids.astype(np.float32)

    return labels, centroids


# ── Engine ───────────────────────────────────────────────────────────────────

class AdaptiveSpeakerEngine:
    """
    Thread-safe per-session online clustering speaker filter.

    Singleton — created once at module load via get_engine().
    """

    def __init__(self):
        self._backend = _MFCCBackend()
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        log.info("[ADAPTIVE] engine ready (MFCC backend, parallel to voiceprint)")

    # ── Session lifecycle ────────────────────────────────────────────────────

    def start_session(self) -> str:
        """Create a new session and return its id."""
        sid = uuid.uuid4().hex[:12]
        with self._lock:
            self._gc_locked()
            self._sessions[sid] = _Session(session_id=sid)
        log.info(f"[ADAPTIVE] start session={sid}")
        return sid

    def reset(self, session_id: str) -> bool:
        """Drop a session (used when the agent re-starts the live call)."""
        with self._lock:
            existed = self._sessions.pop(session_id, None) is not None
        if existed:
            log.info(f"[ADAPTIVE] reset session={session_id}")
        return existed

    def stats(self, session_id: str) -> Optional[dict]:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                return None
            return {
                "session_id": s.session_id,
                "phase": "locked" if s.locked else "calibrating",
                "chunks_seen": s.chunks_seen,
                "chunks_flagged_agent": s.chunks_flagged_agent,
                "chunks_flagged_customer": s.chunks_flagged_customer,
                "chunks_skipped_silent": s.chunks_skipped_silent,
                "calib_buffer_size": len(s.embeddings),
                "calib_min_chunks": CALIB_MIN_CHUNKS,
                "age_s": round(time.time() - s.created_at, 1),
            }

    # ── Main entry point ─────────────────────────────────────────────────────

    def process(
        self,
        session_id: str,
        audio_bytes: bytes,
        content_type: str = "audio/webm",
    ) -> dict:
        """
        Score a live chunk inside an ongoing session.

        Returns a dict with at least:
            is_agent     bool  — True iff this chunk belongs to the locked agent cluster
            phase        str   — "calibrating" | "locked"
            reason       str   — short tag (silent | too_short | calibrating | assigned)
            chunks_seen  int   — total speech chunks processed so far
        """
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                raise KeyError(f"unknown session_id={session_id}")
            s.last_seen = time.time()

        # ── Decode + speech gate ────────────────────────────────────────────
        pcm = _decode_audio_to_pcm(audio_bytes, target_sr=16000)
        duration_s = pcm.size / 16000.0
        if duration_s < 0.8:
            return {
                "is_agent": False,
                "phase": "locked" if s.locked else "calibrating",
                "reason": "too_short",
                "duration_s": round(duration_s, 2),
                "chunks_seen": s.chunks_seen,
            }
        if not _has_speech(pcm, sr=16000, min_rms=0.005):
            with self._lock:
                s.chunks_skipped_silent += 1
            return {
                "is_agent": False,
                "phase": "locked" if s.locked else "calibrating",
                "reason": "silent",
                "duration_s": round(duration_s, 2),
                "chunks_seen": s.chunks_seen,
            }

        # ── Embed ───────────────────────────────────────────────────────────
        try:
            emb = self._backend.embed(pcm, sr=16000)
        except ValueError as e:
            return {
                "is_agent": False,
                "phase": "locked" if s.locked else "calibrating",
                "reason": f"embed_failed: {e}",
                "duration_s": round(duration_s, 2),
                "chunks_seen": s.chunks_seen,
            }

        with self._lock:
            s.chunks_seen += 1

            if not s.locked:
                # ── Calibration phase ──────────────────────────────────────
                s.embeddings.append(emb.astype(np.float32))

                # Try to lock once we have enough samples.
                if len(s.embeddings) >= CALIB_MIN_CHUNKS:
                    locked = self._try_lock_locked(s)
                    if locked:
                        cls_result = self._classify_locked(s, emb)
                        is_agent   = cls_result["is_agent"]
                        if not cls_result.get("is_overlap"):
                            s.confirmed_post_lock_turns += 1
                        speaker_label = self._resolve_speaker_label(s, is_agent)
                        return {
                            "is_agent":      is_agent,
                            "is_overlap":    cls_result.get("is_overlap", False),
                            "speaker_label": speaker_label,
                            "phase":         "locked",
                            "reason":        "just_locked",
                            "duration_s":    round(duration_s, 2),
                            "chunks_seen":   s.chunks_seen,
                            "sim_agent":     cls_result["sim_agent"],
                            "sim_customer":  cls_result["sim_customer"],
                            "agent_chunks_in_calib": int(
                                (s.last_calib_labels == 0).sum()
                                if s.last_calib_labels is not None else 0
                            ),
                            "customer_chunks_in_calib": int(
                                (s.last_calib_labels == 1).sum()
                                if s.last_calib_labels is not None else 0
                            ),
                        }

                # Still calibrating — let everything through so the LLM gets
                # all the audio it needs during the warm-up.
                return {
                    "is_agent": False,
                    "phase": "calibrating",
                    "reason": "calibrating",
                    "duration_s": round(duration_s, 2),
                    "chunks_seen": s.chunks_seen,
                    "calib_buffer_size": len(s.embeddings),
                    "calib_min_chunks": CALIB_MIN_CHUNKS,
                }

            # ── Locked phase ───────────────────────────────────────────────
            result = self._classify_locked(s, emb)
            is_agent   = result["is_agent"]
            is_overlap = result.get("is_overlap", False)

            if is_overlap:
                # Cross-talk chunk — discard from stats, return immediately.
                return {
                    "is_agent":     False,
                    "is_overlap":   True,
                    "speaker_label": "overlap",
                    "phase":        "locked",
                    "reason":       "overlap",
                    "duration_s":   round(duration_s, 2),
                    "chunks_seen":  s.chunks_seen,
                    "sim_agent":    result["sim_agent"],
                    "sim_customer": result["sim_customer"],
                }

            if is_agent:
                s.chunks_flagged_agent += 1
            else:
                s.chunks_flagged_customer += 1

            # Track stable turns post-lock
            s.confirmed_post_lock_turns += 1

            # Resolve speaker_label using person1_cluster mapping
            speaker_label = self._resolve_speaker_label(s, is_agent)

            return {
                "is_agent":      bool(is_agent),
                "is_overlap":    False,
                "speaker_label": speaker_label,
                "phase":         "locked",
                "reason":        "assigned",
                "duration_s":    round(duration_s, 2),
                "chunks_seen":   s.chunks_seen,
                "sim_agent":     result["sim_agent"],
                "sim_customer":  result["sim_customer"],
            }

    # ── Internals ────────────────────────────────────────────────────────────

    def _try_lock_locked(self, s: _Session) -> bool:
        """Run K-means on the calibration buffer. Return True if locked.

        Caller must hold self._lock.
        """
        X = np.stack(s.embeddings, axis=0)  # (N, D)
        try:
            labels, centroids = _kmeans2_cosine(X)
        except ValueError:
            return False

        # Confirm the two clusters are actually different speakers.
        # If the cosine distance between centroids is too small, both
        # clusters represent the same voice — keep calibrating.
        inter_dist = 1.0 - float(np.dot(centroids[0], centroids[1]))
        if inter_dist < MIN_CLUSTER_SEPARATION:
            log.info(
                f"[ADAPTIVE] session={s.session_id} not locking — "
                f"inter-cluster distance {inter_dist:.4f} < min {MIN_CLUSTER_SEPARATION}"
            )
            return False

        # Pick the cluster with MORE chunks during calibration as agent.
        # Rationale: in a sales call, the agent opens the conversation
        # (greeting, intro, product walk-through) so they talk more in
        # the first 30 sec than the customer does.
        c0_count = int((labels == 0).sum())
        c1_count = int((labels == 1).sum())
        if c0_count >= c1_count:
            agent_idx, customer_idx = 0, 1
        else:
            agent_idx, customer_idx = 1, 0

        s.agent_centroid = centroids[agent_idx].copy()
        s.customer_centroid = centroids[customer_idx].copy()
        # Re-label so label 0 is always the agent cluster (for stats clarity)
        s.last_calib_labels = (labels != agent_idx).astype(np.int32)
        s.locked = True

        log.info(
            f"[ADAPTIVE] session={s.session_id} LOCKED — "
            f"agent_chunks={int((s.last_calib_labels == 0).sum())} "
            f"customer_chunks={int((s.last_calib_labels == 1).sum())} "
            f"inter_dist={inter_dist:.4f}"
        )
        return True

    def _classify_locked(self, s: _Session, emb: np.ndarray) -> dict:
        """Assign emb to nearest centroid. Returns {is_agent, is_overlap, sim_agent, sim_customer}.
        Caller must hold self._lock."""
        sim_a = float(np.dot(emb, s.agent_centroid))
        sim_c = float(np.dot(emb, s.customer_centroid))
        base  = {"sim_agent": round(sim_a, 4), "sim_customer": round(sim_c, 4)}

        # Cross-talk detection: both similarities are high AND close together
        if (abs(sim_a - sim_c) <= OVERLAP_SIM_MARGIN
                and sim_a >= OVERLAP_SIM_FLOOR
                and sim_c >= OVERLAP_SIM_FLOOR):
            return {**base, "is_agent": False, "is_overlap": True}

        # Ambiguous chunks default to NOT agent (let them through).
        if abs(sim_a - sim_c) < AMBIGUITY_MARGIN:
            return {**base, "is_agent": False, "is_overlap": False}

        # Use EMA alpha=0.1 for centroid update (replaces raw LR).
        # After LOCK_STABLE_TURNS confirmed turns the model is stable — only
        # drift via EMA, no full re-clustering.
        stable = s.confirmed_post_lock_turns >= LOCK_STABLE_TURNS
        alpha  = EMA_ALPHA if stable else CENTROID_LR

        if sim_a > sim_c:
            if alpha > 0:
                merged = (1 - alpha) * s.agent_centroid + alpha * emb
                norm = np.linalg.norm(merged)
                if norm > 0:
                    s.agent_centroid = (merged / norm).astype(np.float32)
            return {**base, "is_agent": True, "is_overlap": False}
        else:
            if alpha > 0:
                merged = (1 - alpha) * s.customer_centroid + alpha * emb
                norm = np.linalg.norm(merged)
                if norm > 0:
                    s.customer_centroid = (merged / norm).astype(np.float32)
            return {**base, "is_agent": False, "is_overlap": False}

    def _resolve_speaker_label(self, s: _Session, is_agent: bool) -> str:
        """Map is_agent → 'Person 1'/'Person 2' using the person1_cluster anchor.
        Caller must hold self._lock."""
        if s.person1_cluster is None:
            # Anchor not resolved yet — use first_embedding vs centroids
            if s.first_embedding is not None and s.agent_centroid is not None:
                sim_a = float(np.dot(s.first_embedding, s.agent_centroid))
                sim_c = float(np.dot(s.first_embedding, s.customer_centroid))
                # person1_cluster = 0 means "agent centroid is Person 1"
                s.person1_cluster = 0 if sim_a >= sim_c else 1

        if s.person1_cluster is None:
            # No anchor at all yet — fallback: agent=Person 1
            return "Person 1" if is_agent else "Person 2"

        # person1_cluster == 0: agent centroid IS Person 1
        # person1_cluster == 1: customer centroid IS Person 1
        if s.person1_cluster == 0:
            return "Person 1" if is_agent else "Person 2"
        else:
            return "Person 2" if is_agent else "Person 1"

    # ── Person-label API (new June 2026 two-speaker path) ───────────────────
    #
    # label_utterance() is the public entry-point for the new /live/process
    # endpoint.  It runs the same embed pipeline as process() but instead of
    # returning is_agent it returns a stable "Person 1" / "Person 2" label.
    #
    # Label stability contract:
    #   - The very first speech chunk seen is always Person 1.
    #   - All subsequent chunks are compared to the first embedding; a cosine
    #     sim >= PRE_LOCK_SAME_SPEAKER_THRESH means "same person".
    #   - After K-means locks, we use centroid assignment AND anchor Person 1
    #     to whichever centroid the first embedding is closest to.
    #
    # This runs concurrently with whisper_stt.transcribe() in the live endpoint
    # (both operate on the same audio bytes independently).

    # Cosine similarity threshold used during pre-lock calibration.
    # Below this value vs Person 1 prototype → treat as Person 2 (different speaker).
    # 0.65 works well for MFCC on same-device recordings where same-speaker
    # similarity is ~0.75-0.95 and different-speaker similarity is ~0.45-0.70.
    PRE_LOCK_SAME_SPEAKER_THRESH = 0.65

    def label_utterance(
        self,
        session_id: str,
        audio_bytes: bytes,
        content_type: str = "audio/webm",
    ) -> dict:
        """Return {label, phase, ms} for a live audio chunk.

        label: "Person 1" | "Person 2" | "Unknown"
        phase: "calibrating" | "locked" | "too_short" | "silent" | "error" | "no_session"
        ms:    wall-clock milliseconds for the embedding step
        """
        import time as _time
        t0 = _time.time()

        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                return {"label": "Unknown", "phase": "no_session", "ms": 0}
            s.last_seen = _time.time()

        # ── Decode + speech gate ────────────────────────────────────────────
        try:
            pcm = _decode_audio_to_pcm(audio_bytes, target_sr=16000)
        except Exception as exc:
            return {"label": "Unknown", "phase": f"decode_error: {exc}",
                    "ms": int((_time.time() - t0) * 1000)}

        duration_s = pcm.size / 16000.0
        if duration_s < 0.3:
            return {"label": "Unknown", "phase": "too_short",
                    "ms": int((_time.time() - t0) * 1000)}
        if not _has_speech(pcm, sr=16000, min_rms=0.003):
            return {"label": "Unknown", "phase": "silent",
                    "ms": int((_time.time() - t0) * 1000)}

        # ── Embed ───────────────────────────────────────────────────────────
        try:
            emb = self._backend.embed(pcm, sr=16000).astype(np.float32)
        except Exception as exc:
            return {"label": "Unknown", "phase": f"embed_error: {exc}",
                    "ms": int((_time.time() - t0) * 1000)}

        ms = int((_time.time() - t0) * 1000)

        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                return {"label": "Unknown", "phase": "no_session", "ms": ms}

            # ── First utterance ever: anchor Person 1 ──────────────────────
            if s.first_embedding is None:
                s.first_embedding = emb
                s.p1_sum = emb.copy(); s.p1_count = 1
                return {"label": "Person 1", "phase": "calibrating", "ms": ms}

            # ── After lock: use centroid assignment with Person-1 anchor ───
            if s.locked and s.agent_centroid is not None:
                # Resolve Person-1 anchor once (compare first_embedding to centroids)
                if s.person1_cluster is None:
                    sim_a = float(np.dot(s.first_embedding, s.agent_centroid))
                    sim_c = float(np.dot(s.first_embedding, s.customer_centroid))
                    s.person1_cluster = 0 if sim_a >= sim_c else 1

                sim_a = float(np.dot(emb, s.agent_centroid))
                sim_c = float(np.dot(emb, s.customer_centroid))
                nearest_agent = sim_a >= sim_c
                if s.person1_cluster == 0:
                    label = "Person 1" if nearest_agent else "Person 2"
                else:
                    label = "Person 2" if nearest_agent else "Person 1"
                return {"label": label, "phase": "locked", "ms": ms}

            # ── Pre-lock: dual-prototype nearest-neighbour ──────────────────
            # Use running-mean prototypes so labeling improves as more
            # utterances arrive (not just the very first chunk each).
            p1_mean = s.p1_sum / s.p1_count   # always exists after first chunk
            sim_p1 = float(np.dot(emb, p1_mean / (np.linalg.norm(p1_mean) + 1e-9)))

            if s.second_embedding is None:
                # No Person 2 seen yet — decide purely by threshold
                if sim_p1 >= self.PRE_LOCK_SAME_SPEAKER_THRESH:
                    label = "Person 1"
                    s.p1_sum += emb; s.p1_count += 1
                else:
                    # First utterance different enough → Person 2 prototype
                    label = "Person 2"
                    s.second_embedding = emb
                    s.p2_sum = emb.copy(); s.p2_count = 1
            else:
                # Both prototypes exist → nearest wins
                p2_mean = s.p2_sum / s.p2_count
                sim_p2 = float(np.dot(emb, p2_mean / (np.linalg.norm(p2_mean) + 1e-9)))
                if sim_p1 >= sim_p2:
                    label = "Person 1"
                    s.p1_sum += emb; s.p1_count += 1
                else:
                    label = "Person 2"
                    s.p2_sum += emb; s.p2_count += 1

            return {"label": label, "phase": "calibrating", "ms": ms}

    # ────────────────────────────────────────────────────────────────────────

    def _gc_locked(self) -> None:
        """Drop sessions older than SESSION_TTL_S. Caller must hold self._lock."""
        now = time.time()
        stale = [sid for sid, s in self._sessions.items()
                 if (now - s.last_seen) > SESSION_TTL_S]
        for sid in stale:
            self._sessions.pop(sid, None)
        if stale:
            log.info(f"[ADAPTIVE] gc dropped {len(stale)} stale sessions")


# ── Module singleton ─────────────────────────────────────────────────────────

_engine: Optional[AdaptiveSpeakerEngine] = None


def get_engine() -> AdaptiveSpeakerEngine:
    global _engine
    if _engine is None:
        _engine = AdaptiveSpeakerEngine()
    return _engine
