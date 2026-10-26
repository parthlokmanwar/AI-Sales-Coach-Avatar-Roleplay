"""
Voiceprint engine — acoustic fingerprinting for the Live Assistant.

Why this exists
---------------
Deepgram's diarisation assigns speaker numbers per-request, so labels can
flip between chunks. That makes it unreliable to say "speaker 0 is always
the RM." This module replaces label-based filtering with a true acoustic
fingerprint: we extract a fixed-length embedding from the RM's enrolment
clip, store it on disk, and at live time we score each chunk's embedding
against it via cosine similarity.

Layered storage:
  1. SQLite (voiceprints.db)  — source of truth, persists across restarts,
                                supports multiple agents (multi-user).
  2. In-memory dict            — hot cache for the active session, keyed by
                                 profile_id. Loaded lazily from SQLite.

Pluggable backend (env: VOICEPRINT_BACKEND):
  - "mfcc"                  — pure librosa+numpy, no extra deps, works on any
                              Python. Good enough for 2-speaker discrimination
                              (agent vs customer). 78-dim mean+std MFCC pool.
  - "resemblyzer"           — fast (~50ms/chunk), 256-dim d-vectors. Better
                              accuracy. Requires `pip install resemblyzer`
                              (note: webrtcvad dep needs Python <= 3.12 +
                              MSVC build tools on Windows).
  - "pyannote"              — strong accuracy, ~150ms/chunk on CPU, 512-dim.
                              Requires `pip install pyannote.audio` + HF_TOKEN.
                              EER ~3.8% on VoxCeleb.
  - "speechbrain"           — ECAPA-TDNN (same architecture family as TitaNet).
                              192-dim, EER ~1.5%. ~30-60ms on CPU. No new
                              install needed if pyannote 3.x is in the venv —
                              SpeechBrain rides in as a transitive dep.
                              Currently the best accuracy you can get without
                              wrestling NeMo on Python 3.14.
  - "titanet" (preferred)   — best accuracy of the bunch, 192-dim. Trained on
                              4.8M utterances by NVIDIA. EER 0.68% (vs 3.8%
                              for pyannote). Language-independent — works the
                              same for English, Hindi, Hinglish. ~8ms/chunk
                              on a CUDA GPU; ~50-80ms on CPU.
                              Requires `pip install nemo_toolkit[asr]`.

Public API (used by features/voice.py):
  engine = VoiceprintEngine()
  profile_id = engine.enroll(audio_bytes, content_type, name="RM")
  result     = engine.verify(audio_bytes, content_type, profile_id)
  engine.delete(profile_id)
  engine.list_profiles()
"""

from __future__ import annotations

import io
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

import numpy as np

# ── Module-level logger ──────────────────────────────────────────────────────
import logging
log = logging.getLogger("hdfc.voiceprint")

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "voiceprints.db"
CACHE_DIR = ROOT / "voiceprints_cache"
CACHE_DIR.mkdir(exist_ok=True)

# ── Backend selection ────────────────────────────────────────────────────────
# Default to "mfcc" (works on any Python — only needs librosa+numpy).
# Override with VOICEPRINT_BACKEND=resemblyzer or =pyannote for higher accuracy.
_BACKEND = os.environ.get("VOICEPRINT_BACKEND", "mfcc").lower()

# Cosine similarity thresholds (tuned per backend; empirically validated).
# Higher = stricter. > threshold means "this IS the enrolled speaker."
# MFCC space is more constrained than learned embeddings, so the threshold
# sits higher than for d-vectors / pyannote.
_THRESHOLDS = {
    "mfcc": 0.88,
    "resemblyzer": 0.75,
    "pyannote": 0.80,
    # SpeechBrain ECAPA-TDNN is the same architecture family as TitaNet —
    # very similar embedding geometry, so 0.75 is a good starting point.
    "speechbrain": 0.75,
    # TitaNet embeddings are more discriminative than pyannote's, so 0.75
    # cleanly separates same-speaker from impostor while keeping legit
    # within-speaker variation (different language / mood) above the line.
    "titanet": 0.75,
}
SIMILARITY_THRESHOLD = float(
    os.environ.get("VOICEPRINT_THRESHOLD", _THRESHOLDS.get(_BACKEND, 0.80))
)


# ── Audio decoding (format-agnostic via librosa) ─────────────────────────────

def _has_speech(pcm: np.ndarray, sr: int = 16000, min_rms: float = 0.005) -> bool:
    """Cheap voice-activity check: is the average RMS energy above min_rms?

    Used to reject enrolment / verify clips that are essentially silence.
    The earlier "samples=3 stored even though I didn't speak" bug was caused
    by accepting silent webm clips; the MFCC of silence is still a valid
    120-dim vector, but it fingerprints background noise rather than the
    user's voice.

    Tuning notes:
      min_rms=0.005 → roughly -45 dB RMS, which is well above ambient
      mic noise on a typical laptop and well below any normal speaking
      volume. Setting this too high risks false negatives on soft speakers.
    """
    if pcm.size == 0:
        return False
    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
    return rms >= min_rms


def _compute_rms(pcm: np.ndarray) -> float:
    """Average loudness of the clip (0..1 for normalized float audio).

    Used as Signal 3 of the 3-signal fusion: agent talks close to the mic
    so their RMS is consistently higher than customer audio coming through
    a phone speakerphone (where the path mic → codec → speaker → air → mic
    attenuates the signal). Tuned by inspecting real call samples; we treat
    >= AGENT_RMS_LOW as "agent-loud".
    """
    if pcm.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))


def _compute_hf_ratio(pcm: np.ndarray, sr: int = 16000,
                       low_band: tuple = (300, 3400),
                       high_band: tuple = (4000, 7800)) -> float:
    """Ratio of high-frequency energy (4-8 kHz) to low+mid energy (300-3400 Hz).

    Signal 2 of the 3-signal fusion: phone audio has a narrowband signature.
    GSM / VoIP codecs cut everything above ~3.4 kHz, so customer audio
    arriving via phone speakerphone has very little energy in 4-8 kHz.
    Agent talking directly into the mic preserves the full band, so their
    HF ratio is much higher.

    Computed via Welch periodogram (FFT-based). Robust to short clips
    (works on >= 0.5s) and runs in <5ms on a few-second clip.

    Tuning:
      HF_RATIO_AGENT_MIN = 0.12   (above this is likely direct-mic / agent)
      HF_RATIO_CUSTOMER_MAX = 0.06 (below this is likely phone audio)
    """
    if pcm.size < int(sr * 0.3):
        return 0.0
    # Use numpy's rfft directly to avoid scipy dependency.
    # Window with Hann to reduce spectral leakage.
    n = pcm.size
    win = np.hanning(n).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(pcm * win))
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    psd = spectrum ** 2
    low_mask = (freqs >= low_band[0]) & (freqs <= low_band[1])
    high_mask = (freqs >= high_band[0]) & (freqs <= high_band[1])
    low_e = float(psd[low_mask].sum())
    high_e = float(psd[high_mask].sum())
    if low_e <= 1e-12:
        return 0.0
    return high_e / low_e


# ── Fusion thresholds ────────────────────────────────────────────────────────
# Tuned for the "agent at mic + customer on phone speakerphone" case.
# Adjust here if real-call data shows different baselines.
AGENT_RMS_LOW = 0.04          # below this = quiet / customer-like
HF_RATIO_AGENT_MIN = 0.12     # above this = direct-mic / agent-like
HF_RATIO_CUSTOMER_MAX = 0.06  # below this = phone-band / customer-like


def _ffmpeg_binary() -> Optional[str]:
    """Return path to a usable ffmpeg binary.

    Order:
      1. The portable ffmpeg shipped by imageio-ffmpeg (preferred — no system
         install needed, works the same on every dev's machine).
      2. ffmpeg on system PATH (in case the user already has it).
      3. None — caller will fall back to librosa/soundfile (only works for
         already-decoded formats like wav).
    """
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    # Last resort: rely on PATH
    import shutil
    return shutil.which("ffmpeg")


def _decode_audio_to_pcm(audio_bytes: bytes, target_sr: int = 16000) -> np.ndarray:
    """
    Decode arbitrary audio (webm/opus from browser MediaRecorder, mp4, wav,
    mp3, ogg) into a mono float32 PCM array at target_sr.

    Strategy:
      1. Pipe the bytes through ffmpeg → 16-bit signed little-endian PCM.
         This is the most reliable path because browsers send webm/opus and
         soundfile alone can't decode that container.
      2. If ffmpeg is unavailable, fall back to librosa/soundfile — which only
         works for already-decoded formats (wav, flac, mp3, ogg-vorbis).

    Returns: 1D np.ndarray of float32 samples in [-1, 1].
    Raises:  ValueError if the bytes cannot be decoded by any path.
    """
    ffmpeg = _ffmpeg_binary()

    # ── Path 1: ffmpeg pipe (handles webm/opus, mp4, anything ffmpeg knows) ──
    if ffmpeg:
        import subprocess
        try:
            # -i pipe:0       → read from stdin
            # -f s16le        → output raw 16-bit little-endian PCM
            # -ac 1           → mono
            # -ar target_sr   → resample to target_sr (16000 by default)
            # pipe:1          → write to stdout
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner", "-loglevel", "error",
                    "-i", "pipe:0",
                    "-f", "s16le",
                    "-ac", "1",
                    "-ar", str(target_sr),
                    "pipe:1",
                ],
                input=audio_bytes,
                capture_output=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                pcm_i16 = np.frombuffer(proc.stdout, dtype=np.int16)
                if pcm_i16.size == 0:
                    raise ValueError("ffmpeg produced empty PCM output.")
                # Normalise to [-1.0, 1.0] float32 (what librosa/MFCC expect)
                return (pcm_i16.astype(np.float32) / 32768.0)
            # ffmpeg exited non-zero — surface its stderr for debugging
            stderr_tail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()[-200:]
            raise ValueError(f"ffmpeg decode failed (rc={proc.returncode}): {stderr_tail}")
        except FileNotFoundError as e:
            log.warning(f"[VOICEPRINT] ffmpeg binary not runnable: {e} — falling back to librosa")
        except subprocess.TimeoutExpired:
            raise ValueError("Audio decode timed out (clip > 15s or ffmpeg stalled).")
        except ValueError:
            raise
        except Exception as e:
            log.warning(f"[VOICEPRINT] ffmpeg path errored: {e} — falling back to librosa")

    # ── Path 2: librosa/soundfile fallback (wav/flac/mp3/ogg only) ──
    try:
        import librosa
        audio, _sr = librosa.load(
            io.BytesIO(audio_bytes),
            sr=target_sr,
            mono=True,
            dtype=np.float32,
        )
        if audio.size == 0:
            raise ValueError("Decoded audio is empty.")
        return audio
    except Exception as e:
        raise ValueError(
            f"Could not decode audio: {e}. "
            "If the clip is webm/opus from the browser, ffmpeg is required — "
            "the `imageio-ffmpeg` package ships a portable one (already in requirements.txt)."
        )


# ── Embedding backends ───────────────────────────────────────────────────────

class _MFCCBackend:
    """
    Lightweight homegrown speaker embedding using MFCC features.
    Pipeline:  raw PCM → 20 MFCCs + delta + delta-delta → mean & std pool
               → 120-dim vector → L2-normalise.
    No external deps beyond librosa+numpy. Works on any Python version.
    Not as discriminative as d-vectors, but for the 2-speaker case (agent
    vs customer) it gives reliable separation when used with threshold ~0.88.
    """

    name = "mfcc"
    expected_dim = 120  # (20 + 20 + 20) coeffs × (mean + std) = 120

    def __init__(self):
        try:
            import librosa  # noqa: F401  — surface ImportError early
        except ImportError as e:
            raise ImportError(
                "librosa not installed. Run `pip install librosa soundfile`."
            ) from e
        log.info(f"[VOICEPRINT] MFCC backend ready (no model load required)")

    def embed(self, pcm: np.ndarray, sr: int = 16000) -> np.ndarray:
        import librosa
        # Trim leading/trailing silence — top_db=40 is gentler than 30 so we
        # don't over-trim Hindi recordings that have natural inter-word pauses.
        # If the trim leaves nothing voiced, fall back to using the raw clip
        # (the user already spoke into the mic for 5s — trust that signal).
        pcm_t, _ = librosa.effects.trim(pcm, top_db=40)
        if pcm_t.size < int(sr * 0.3):
            # Fallback: use the raw (untrimmed) audio if we have at least 0.3s of it
            if pcm.size >= int(sr * 0.3):
                pcm_t = pcm
            else:
                raise ValueError(
                    f"Audio too short for embedding (got {pcm.size / sr:.2f}s of voiced audio, "
                    f"need >= 0.3s). Speak the whole phrase without long pauses."
                )

        # 20 MFCCs per frame; 25ms window, 10ms hop is the standard speaker-ID config.
        mfcc = librosa.feature.mfcc(
            y=pcm_t, sr=sr, n_mfcc=20,
            n_fft=400, hop_length=160, win_length=400,
        )
        # Delta + delta-delta capture short-term + curvature dynamics
        d1 = librosa.feature.delta(mfcc, order=1)
        d2 = librosa.feature.delta(mfcc, order=2)
        feats = np.vstack([mfcc, d1, d2])  # (60, T)

        # Pool across time: mean and std per coefficient → 120-dim vector
        mean = feats.mean(axis=1)
        std = feats.std(axis=1)
        vec = np.concatenate([mean, std]).astype(np.float32)

        # L2-normalise so cosine similarity == dot product
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec


class _ResemblyzerBackend:
    """Resemblyzer — VoiceEncoder produces a 256-dim L2-normalised embedding."""

    name = "resemblyzer"
    expected_dim = 256

    def __init__(self):
        try:
            from resemblyzer import VoiceEncoder
        except ImportError as e:
            raise ImportError(
                "resemblyzer not installed. Run `pip install resemblyzer`."
            ) from e
        log.info("[VOICEPRINT] Loading Resemblyzer VoiceEncoder (CPU)...")
        t0 = time.time()
        self.encoder = VoiceEncoder(device="cpu")
        log.info(f"[VOICEPRINT] Resemblyzer ready in {int((time.time()-t0)*1000)}ms")

    def embed(self, pcm: np.ndarray, sr: int = 16000) -> np.ndarray:
        # Resemblyzer expects 16kHz mono float32. preprocess_wav handles
        # silence trimming and normalisation. embed_utterance returns
        # an L2-normalised 256-dim vector.
        from resemblyzer import preprocess_wav
        wav = preprocess_wav(pcm, source_sr=sr)
        if wav.size < 16000:  # need ~1 second min
            raise ValueError("Audio too short after silence trimming (need >= 1s).")
        emb = self.encoder.embed_utterance(wav)
        return emb.astype(np.float32)


class _PyannoteBackend:
    """pyannote.audio — speaker embedding via the speechbrain ECAPA model."""

    name = "pyannote"
    expected_dim = 512  # ECAPA default

    def __init__(self):
        # Compat shim: pyannote.audio 3.4 calls torchaudio.list_audio_backends()
        # at Audio.__init__ time, but that function was removed in torchaudio
        # 2.10. Without this, every Inference() call dies with AttributeError.
        # We only use waveform-dict input (no file path), so the backend
        # choice is informational — returning ['soundfile'] satisfies the
        # branch and lets the rest of pyannote work normally.
        import torchaudio
        if not hasattr(torchaudio, "list_audio_backends"):
            torchaudio.list_audio_backends = lambda: ["soundfile"]

        try:
            from pyannote.audio import Inference, Model
        except ImportError as e:
            raise ImportError(
                "pyannote.audio not installed. Run `pip install pyannote.audio` "
                "and set HF_TOKEN env var with a HuggingFace access token."
            ) from e
        hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
        if not hf_token:
            raise RuntimeError(
                "HF_TOKEN env var required for pyannote — accept the model licence "
                "at https://huggingface.co/pyannote/embedding and provide a token."
            )
        log.info("[VOICEPRINT] Loading pyannote embedding model...")
        t0 = time.time()
        model = Model.from_pretrained("pyannote/embedding", use_auth_token=hf_token)
        self.inference = Inference(model, window="whole")
        log.info(f"[VOICEPRINT] pyannote ready in {int((time.time()-t0)*1000)}ms")

    def embed(self, pcm: np.ndarray, sr: int = 16000) -> np.ndarray:
        # pyannote Inference accepts a {"waveform": Tensor[C,T], "sample_rate": int} dict
        import torch
        if pcm.size < 16000:
            raise ValueError("Audio too short (need >= 1s).")
        waveform = torch.from_numpy(pcm).unsqueeze(0)  # shape (1, T)
        emb = self.inference({"waveform": waveform, "sample_rate": sr})
        emb_np = np.asarray(emb, dtype=np.float32).flatten()
        # L2-normalise so cosine similarity == dot product
        norm = np.linalg.norm(emb_np)
        if norm > 0:
            emb_np = emb_np / norm
        return emb_np


class _SpeechBrainBackend:
    """
    SpeechBrain ECAPA-TDNN — drop-in replacement for TitaNet when NeMo can't
    be installed (e.g. Python 3.14 + no MSVC).

    Architecture: same ECAPA-TDNN family as TitaNet, trained on VoxCeleb 1+2.
    Embedding:    192-dim L2-normalised vector (same dim as TitaNet).
    Accuracy:     ~1.5% EER on VoxCeleb (between pyannote's 3.8% and
                  TitaNet's 0.68%) — closes most of the accuracy gap.
    Latency:      ~30-60 ms on CPU, ~5-10 ms on CUDA.
    Install:      No new pip install required if pyannote 3.x is already in
                  the venv — SpeechBrain rides in as a pyannote dep.

    Model is downloaded from HuggingFace on first use (~80 MB) and cached
    under voiceprints_cache/speechbrain_ecapa/.
    """

    name = "speechbrain"
    expected_dim = 192

    def __init__(self):
        try:
            from speechbrain.inference.speaker import EncoderClassifier
            import torch
        except ImportError as e:
            raise ImportError(
                "speechbrain not installed. It usually rides in as a pyannote 3.x "
                "dependency — run `pip install pyannote.audio` to pull it in."
            ) from e

        # SpeechBrain ≥1.0 takes a LocalStrategy enum; older builds don't have it.
        try:
            from speechbrain.utils.fetching import LocalStrategy
            _copy_strategy = LocalStrategy.COPY
        except ImportError:
            _copy_strategy = None

        # Pick the best device available. torch.cuda.is_available() only checks
        # that the runtime loaded — it does NOT verify the installed PyTorch
        # has kernel binaries for this GPU's compute capability. Newer GPUs
        # (e.g. RTX 50-series, sm_120) paired with older PyTorch wheels raise
        # "no kernel image is available for execution on the device" the first
        # time a real op runs. Probe with a tiny op and fall back to CPU.
        self._device = "cpu"
        run_opts = {"device": "cpu"}
        if torch.cuda.is_available():
            try:
                _probe = torch.zeros(1, device="cuda") + 1
                torch.cuda.synchronize()
                _ = _probe.cpu()
                run_opts = {"device": "cuda"}
                self._device = f"cuda ({torch.cuda.get_device_name(0)})"
            except Exception as e:
                log.warning(
                    f"[VOICEPRINT] CUDA reports available but kernel probe failed "
                    f"({type(e).__name__}: {e}) — falling back to CPU"
                )

        savedir = CACHE_DIR / "speechbrain_ecapa"
        log.info("[VOICEPRINT] Loading SpeechBrain ECAPA-TDNN…")
        log.info(f"[VOICEPRINT]   (first run downloads ~80 MB; cached to {savedir})")
        t0 = time.time()
        # On Windows, SpeechBrain's default fetch strategy creates symlinks from
        # savedir into the HF hub cache, which requires SeCreateSymbolicLinkPrivilege
        # (Admin or Developer Mode). Force COPY so unprivileged processes can load
        # the model. local_strategy expects the LocalStrategy enum — passing a
        # string slides through **kwargs and silently falls back to SYMLINK.
        fh_kwargs = {
            "source": "speechbrain/spkrec-ecapa-voxceleb",
            "savedir": str(savedir),
            "run_opts": run_opts,
        }
        if _copy_strategy is not None:
            fh_kwargs["local_strategy"] = _copy_strategy
        try:
            self.model = EncoderClassifier.from_hparams(**fh_kwargs)
        except TypeError:
            fh_kwargs.pop("local_strategy", None)
            self.model = EncoderClassifier.from_hparams(**fh_kwargs)
        log.info(
            f"[VOICEPRINT] SpeechBrain ECAPA ready in {int((time.time()-t0)*1000)}ms "
            f"on {self._device}"
        )

    @property
    def device(self) -> str:
        return self._device

    def embed(self, pcm: np.ndarray, sr: int = 16000) -> np.ndarray:
        import torch

        if sr != 16000:
            # SpeechBrain ECAPA is trained on 16 kHz. Resample defensively.
            try:
                import librosa
                pcm = librosa.resample(pcm.astype(np.float32), orig_sr=sr, target_sr=16000)
                sr = 16000
            except Exception as e:
                raise ValueError(f"SpeechBrain needs 16 kHz audio, got {sr} Hz and resample failed: {e}")

        if pcm.size < int(0.5 * sr):
            raise ValueError(
                f"Audio too short for SpeechBrain (got {pcm.size / sr:.2f}s, need >= 0.5s)."
            )

        # encode_batch wants shape (B, T) float32 tensor; returns (B, 1, 192).
        wav = torch.from_numpy(pcm.astype(np.float32)).unsqueeze(0)
        if self._device.startswith("cuda"):
            wav = wav.cuda()
        with torch.no_grad():
            emb = self.model.encode_batch(wav)

        emb_np = emb.detach().cpu().numpy().astype(np.float32).flatten()
        norm = np.linalg.norm(emb_np)
        if norm > 0:
            emb_np = emb_np / norm
        return emb_np


class _TitaNetBackend:
    """
    NVIDIA TitaNet Large — state-of-the-art speaker embedding model.

    Why this is the best backend
    ----------------------------
    - 192-dim embedding (smaller than pyannote's 512 but more information-dense
      because the model was trained on 4.8M utterances vs pyannote's ~800k).
    - 0.68% EER on VoxCeleb cleaned vs pyannote's 3.8% — roughly 5x fewer
      same-speaker / impostor confusions.
    - Squeeze-and-Excitation blocks let the model dynamically focus on
      voice-characteristic frequencies, dropping room/codec noise.
    - Language-independent by design: trained on multilingual data, so
      Hindi / English / Hinglish all produce embeddings of the same quality.
    - Runs at ~8 ms / 3-sec chunk on a modern CUDA GPU, ~50-80 ms on CPU.

    Heads-up: NeMo is a heavyweight install (~2-3 GB of deps). The first
    embed() call downloads the model from HuggingFace (~96 MB, one-time,
    cached under ~/.cache/torch/NeMo/).
    """

    name = "titanet"
    expected_dim = 192

    def __init__(self):
        try:
            import nemo.collections.asr as nemo_asr
            import torch
        except ImportError as e:
            raise ImportError(
                "nemo_toolkit not installed. Run `pip install nemo_toolkit[asr]` "
                "to enable the TitaNet backend (large download, ~2-3 GB)."
            ) from e

        log.info("[VOICEPRINT] Loading NVIDIA TitaNet Large…")
        log.info("[VOICEPRINT]   (first run downloads ~96 MB from HuggingFace)")
        t0 = time.time()
        self.model = nemo_asr.models.EncDecSpeakerLabelModel.from_pretrained(
            "nvidia/speakerverification_en_titanet_large"
        )

        # Move to GPU if a CUDA build of torch is present. Falls back silently
        # to CPU otherwise — model still works, just slower (~50-80ms).
        self._device = "cpu"
        if torch.cuda.is_available():
            try:
                self.model = self.model.cuda()
                self._device = f"cuda ({torch.cuda.get_device_name(0)})"
            except Exception as e:
                log.warning(f"[VOICEPRINT] Could not move TitaNet to GPU: {e} — using CPU")
        self.model.eval()
        log.info(
            f"[VOICEPRINT] TitaNet ready in {int((time.time()-t0)*1000)}ms "
            f"on {self._device}"
        )

    @property
    def device(self) -> str:
        return self._device

    def embed(self, pcm: np.ndarray, sr: int = 16000) -> np.ndarray:
        import torch

        # TitaNet is trained on 16 kHz mono. Caller (decode_audio_to_pcm) already
        # resamples to 16 kHz, but enforce it defensively in case future callers
        # bypass that path.
        if sr != 16000:
            try:
                import librosa
                pcm = librosa.resample(pcm.astype(np.float32), orig_sr=sr, target_sr=16000)
                sr = 16000
            except Exception as e:
                raise ValueError(f"TitaNet needs 16 kHz audio, got {sr} Hz and resample failed: {e}")

        if pcm.size < int(0.5 * sr):
            raise ValueError(
                f"Audio too short for TitaNet (got {pcm.size / sr:.2f}s, need >= 0.5s)."
            )

        # NeMo's speaker model exposes both forward() (raw tensors) and
        # get_embedding(file_path). The tensor path avoids a temp-file write
        # per chunk and is what we want for live streaming.
        audio_signal = torch.from_numpy(pcm.astype(np.float32)).unsqueeze(0)
        length = torch.tensor([pcm.size], dtype=torch.long)
        if self._device.startswith("cuda"):
            audio_signal = audio_signal.cuda()
            length = length.cuda()

        with torch.no_grad():
            _, embs = self.model.forward(
                input_signal=audio_signal,
                input_signal_length=length,
            )

        emb_np = embs.detach().cpu().numpy().astype(np.float32).flatten()
        norm = np.linalg.norm(emb_np)
        if norm > 0:
            emb_np = emb_np / norm
        return emb_np


def _load_backend():
    """Lazy-load the configured backend; raise a clear error if unavailable."""
    if _BACKEND == "mfcc":
        return _MFCCBackend()
    if _BACKEND == "pyannote":
        return _PyannoteBackend()
    if _BACKEND == "resemblyzer":
        return _ResemblyzerBackend()
    if _BACKEND == "speechbrain":
        return _SpeechBrainBackend()
    if _BACKEND == "titanet":
        return _TitaNetBackend()
    raise ValueError(
        f"Unknown VOICEPRINT_BACKEND='{_BACKEND}'. "
        f"Use 'mfcc', 'resemblyzer', 'pyannote', 'speechbrain', or 'titanet'."
    )


# ── SQLite store ─────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS voiceprints (
    profile_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    backend      TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    embedding    BLOB NOT NULL,
    created_at   REAL NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 1
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_SCHEMA)
    return conn


# ── Voiceprint engine (public) ───────────────────────────────────────────────

class VoiceprintEngine:
    """
    Singleton-style engine. Wraps backend embedding + SQLite store + RAM cache.

    Thread-safety: SQLite calls are short and serial; the backend's embed()
    method may take 50-200ms per call — wrap calls in a thread executor at
    the FastAPI layer to avoid blocking the event loop.
    """

    def __init__(self):
        self._backend = None  # lazy
        self._mem_cache: dict[str, np.ndarray] = {}
        # Ensure schema is created early so the file exists for inspection.
        _connect().close()

        # Count enrolled profiles AND break them down by backend so the
        # operator can see immediately whether their stored profiles match
        # the configured backend (otherwise verify() will reject them).
        backend_counts: dict[str, int] = {}
        with _connect() as conn:
            rows = conn.execute("SELECT backend, COUNT(*) FROM voiceprints GROUP BY backend").fetchall()
            for b, n in rows:
                backend_counts[b] = n
        total = sum(backend_counts.values())
        compat = backend_counts.get(_BACKEND, 0)
        breakdown = ", ".join(f"{b}={n}" for b, n in sorted(backend_counts.items())) or "(none)"

        log.info(f"[VOICEPRINT] ─── Voice-filter engine ──────────────────────")
        log.info(f"[VOICEPRINT] Backend          : {_BACKEND}")
        log.info(f"[VOICEPRINT] Threshold        : {SIMILARITY_THRESHOLD}")
        log.info(f"[VOICEPRINT] DB               : {DB_PATH.name}")
        log.info(f"[VOICEPRINT] Profiles stored  : {total}  [{breakdown}]")
        log.info(f"[VOICEPRINT] Compatible w/ {_BACKEND:<11s}: {compat}")
        if total > 0 and compat == 0:
            log.warning(
                f"[VOICEPRINT] ⚠ Zero profiles match backend '{_BACKEND}'. "
                f"Users must re-enroll — old profiles cannot be verified across backends."
            )
        log.info(f"[VOICEPRINT] ──────────────────────────────────────────────")

    # ── Backend lazy-load (so server can boot even if model download is slow) ─
    @property
    def backend(self):
        if self._backend is None:
            self._backend = _load_backend()
        return self._backend

    @property
    def backend_name(self) -> str:
        return _BACKEND

    # ── Public API ───────────────────────────────────────────────────────────

    def enroll(
        self,
        audio_bytes: bytes,
        content_type: str = "audio/webm",
        name: str = "RM",
    ) -> dict:
        """
        Register a new voiceprint. Returns:
            {profile_id, name, backend, dim, similarity_threshold, duration_s}
        Raises ValueError on bad audio.
        """
        if len(audio_bytes) < 1000:
            raise ValueError("Enrolment clip too small (< 1KB).")
        pcm = _decode_audio_to_pcm(audio_bytes, target_sr=16000)
        duration_s = pcm.size / 16000.0
        if duration_s < 1.0:
            raise ValueError(
                f"Enrolment clip too short ({duration_s:.1f}s) — need at least 1 second of audio."
            )

        rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
        if not _has_speech(pcm, sr=16000, min_rms=0.008):
            raise ValueError(
                f"No speech detected in enrolment clip (rms={rms:.4f}). "
                "Please speak the whole phrase clearly into the mic and try again."
            )

        emb = self.backend.embed(pcm, sr=16000)
        if emb.size != self.backend.expected_dim:
            log.warning(
                f"[VOICEPRINT] Unexpected emb dim {emb.size} (expected "
                f"{self.backend.expected_dim}) — storing anyway."
            )

        profile_id = uuid.uuid4().hex[:12]
        blob = emb.tobytes()
        now = time.time()

        with _connect() as conn:
            conn.execute(
                "INSERT INTO voiceprints (profile_id, name, backend, dim, embedding, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (profile_id, name, self.backend.name, int(emb.size), blob, now),
            )
            conn.commit()

        # Hot cache so the first verify() doesn't have to re-read SQLite.
        self._mem_cache[profile_id] = emb

        log.info(
            f"[VOICEPRINT] Enrolled profile_id={profile_id} name='{name}' "
            f"backend={self.backend.name} dim={emb.size} duration={duration_s:.1f}s"
        )
        return {
            "profile_id": profile_id,
            "name": name,
            "backend": self.backend.name,
            "dim": int(emb.size),
            "similarity_threshold": SIMILARITY_THRESHOLD,
            "duration_s": round(duration_s, 2),
        }

    def verify(
        self,
        audio_bytes: bytes,
        profile_id: str,
        content_type: str = "audio/webm",
    ) -> dict:
        """
        Score a live chunk against an enrolled voiceprint.
        Returns:
            {is_agent: bool, similarity: float, threshold: float, duration_s: float}
        """
        # Explicit cross-backend check: enrolment was done with backend X but
        # the server is now running backend Y → embeddings are in different
        # spaces and cosine similarity is meaningless. Refuse cleanly so the
        # frontend can surface a "please re-enroll" message instead of acting
        # on a nonsense similarity score.
        stored_backend = self._get_profile_backend(profile_id)
        if stored_backend is not None and stored_backend != self.backend.name:
            raise ValueError(
                f"Profile was enrolled with backend='{stored_backend}' but the "
                f"server is running backend='{self.backend.name}'. Please re-enroll "
                f"your voice — embeddings from different backends are not "
                f"comparable."
            )

        stored = self._get_embedding(profile_id)
        if stored is None:
            raise KeyError(f"No voiceprint found for profile_id={profile_id}")

        pcm = _decode_audio_to_pcm(audio_bytes, target_sr=16000)
        duration_s = pcm.size / 16000.0
        if duration_s < 0.8:
            # Too short for a reliable score — be conservative and say "not agent"
            # so the chunk is processed as customer (better to ask LLM than skip).
            return {
                "is_agent": False,
                "similarity": 0.0,
                "threshold": SIMILARITY_THRESHOLD,
                "duration_s": round(duration_s, 2),
                "reason": "audio_too_short",
            }

        # ── Compute all 3 acoustic signals ──────────────────────────────────
        # Signal 1: speaker embedding similarity (voice identity)
        # Signal 2: high-frequency energy ratio (phone-band vs direct-mic)
        # Signal 3: RMS loudness (close-mic vs distant)
        try:
            emb = self.backend.embed(pcm, sr=16000)
        except ValueError as e:
            return {
                "is_agent": False,
                "similarity": 0.0,
                "threshold": SIMILARITY_THRESHOLD,
                "duration_s": round(duration_s, 2),
                "reason": f"embed_failed: {e}",
            }

        sim = float(np.dot(emb, stored))  # both L2-normalised → cosine == dot
        hf_ratio = _compute_hf_ratio(pcm, sr=16000)
        rms = _compute_rms(pcm)

        # ── Decision logic (inverted from earlier 3-vote scheme) ────────────
        # Lesson learned the hard way: HF and RMS only differentiate
        # "phone-speakerphone customer" from "direct-mic speaker". They
        # CANNOT separate two people standing in the same room — both will
        # have high HF (direct mic) and high RMS (close to mic), so an
        # up-vote scheme falsely flagged the in-room customer as agent.
        #
        # New design — HF + RMS act ONLY as a "definitely-customer-on-phone"
        # detector. If both fire, we skip the embedding check entirely and
        # call it customer. Otherwise the embedding alone decides.
        #
        #   phone_customer = (HF < HF_RATIO_CUSTOMER_MAX) AND (RMS < AGENT_RMS_LOW)
        #     → True: definitely customer (phone-band, quiet through speaker)
        #     → False: same-room scenario → trust voice fingerprint
        #
        # This handles BOTH demo setups:
        #   - Customer on phone speakerphone → HF/RMS catches it cleanly.
        #   - Customer physically in the room → embedding decides by voice
        #     identity (works because friend's voice ≠ enrolled voice).
        phone_customer = (
            hf_ratio < HF_RATIO_CUSTOMER_MAX and rms < AGENT_RMS_LOW
        )
        vote_emb = sim >= SIMILARITY_THRESHOLD
        if phone_customer:
            is_agent = False
            decision = "phone_customer_detected"
        else:
            is_agent = vote_emb
            decision = "embedding_only"

        return {
            "is_agent": bool(is_agent),
            "similarity": round(sim, 4),
            "threshold": SIMILARITY_THRESHOLD,
            "hf_ratio": round(hf_ratio, 4),
            "hf_customer_max": HF_RATIO_CUSTOMER_MAX,
            "rms": round(rms, 4),
            "rms_threshold": AGENT_RMS_LOW,
            "decision": decision,
            "phone_customer": bool(phone_customer),
            "vote_embedding": bool(vote_emb),
            "duration_s": round(duration_s, 2),
        }

    def add_sample(
        self,
        audio_bytes: bytes,
        profile_id: str,
        content_type: str = "audio/webm",
    ) -> dict:
        """
        Append another speech sample to an existing profile.
        The stored embedding is updated via incremental mean:

            new_emb = (old_emb * n + sample_emb) / (n + 1)   then L2-normalise

        This makes the fingerprint progressively more robust to:
          - language switches (English / Hindi / Hinglish)
          - prosody differences (calm vs animated)
          - day-to-day vocal variation

        Returns: {profile_id, name, backend, dim, sample_count,
                  similarity_threshold, duration_s}
        Raises:  KeyError if profile_id is unknown; ValueError on bad audio.
        """
        if len(audio_bytes) < 1000:
            raise ValueError("Sample clip too small (< 1KB).")
        pcm = _decode_audio_to_pcm(audio_bytes, target_sr=16000)
        duration_s = pcm.size / 16000.0
        if duration_s < 1.0:
            raise ValueError(
                f"Sample clip too short ({duration_s:.1f}s) — need at least 1 second of audio."
            )

        rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
        if not _has_speech(pcm, sr=16000, min_rms=0.008):
            raise ValueError(
                f"No speech detected in sample clip (rms={rms:.4f}). "
                "Please speak the whole phrase clearly into the mic and try again."
            )

        # Read current row
        with _connect() as conn:
            row = conn.execute(
                "SELECT name, backend, dim, embedding, sample_count "
                "FROM voiceprints WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"No voiceprint found for profile_id={profile_id}")

        name, backend_name, dim, blob, sample_count = row
        if backend_name != self.backend.name:
            raise ValueError(
                f"Profile was enrolled with backend='{backend_name}' but current "
                f"backend is '{self.backend.name}' — cannot add cross-backend samples."
            )

        old_emb = np.frombuffer(blob, dtype=np.float32, count=dim).copy()
        new_sample_emb = self.backend.embed(pcm, sr=16000)
        if new_sample_emb.size != dim:
            raise ValueError(
                f"Embedding dim mismatch: stored={dim}, new={new_sample_emb.size}"
            )

        # Incremental running mean (de-normalise the average, add, re-normalise)
        merged = (old_emb * sample_count + new_sample_emb) / (sample_count + 1)
        norm = np.linalg.norm(merged)
        if norm > 0:
            merged = (merged / norm).astype(np.float32)

        new_count = sample_count + 1
        with _connect() as conn:
            conn.execute(
                "UPDATE voiceprints SET embedding = ?, sample_count = ? "
                "WHERE profile_id = ?",
                (merged.tobytes(), new_count, profile_id),
            )
            conn.commit()

        # Refresh hot cache
        self._mem_cache[profile_id] = merged

        log.info(
            f"[VOICEPRINT] add_sample profile_id={profile_id} "
            f"sample_count={new_count} duration={duration_s:.1f}s"
        )
        return {
            "profile_id": profile_id,
            "name": name,
            "backend": self.backend.name,
            "dim": int(merged.size),
            "sample_count": new_count,
            "similarity_threshold": SIMILARITY_THRESHOLD,
            "duration_s": round(duration_s, 2),
        }

    def delete(self, profile_id: str) -> bool:
        """Remove a voiceprint. Returns True if a row was deleted."""
        self._mem_cache.pop(profile_id, None)
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM voiceprints WHERE profile_id = ?", (profile_id,)
            )
            conn.commit()
            return cur.rowcount > 0

    def list_profiles(self) -> list[dict]:
        """List all enrolled voiceprints (metadata only — embeddings excluded)."""
        with _connect() as conn:
            rows = conn.execute(
                "SELECT profile_id, name, backend, dim, created_at, sample_count "
                "FROM voiceprints ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "profile_id": r[0],
                "name": r[1],
                "backend": r[2],
                "dim": r[3],
                "created_at": r[4],
                "sample_count": r[5],
            }
            for r in rows
        ]

    # ── Internal: cache-aware embedding lookup ───────────────────────────────

    def _get_profile_backend(self, profile_id: str) -> Optional[str]:
        """Return the backend name a profile was enrolled with, or None."""
        with _connect() as conn:
            row = conn.execute(
                "SELECT backend FROM voiceprints WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        return row[0] if row else None

    def _get_embedding(self, profile_id: str) -> Optional[np.ndarray]:
        cached = self._mem_cache.get(profile_id)
        if cached is not None:
            return cached
        with _connect() as conn:
            row = conn.execute(
                "SELECT embedding, dim FROM voiceprints WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        if row is None:
            return None
        blob, dim = row
        emb = np.frombuffer(blob, dtype=np.float32, count=dim).copy()
        self._mem_cache[profile_id] = emb
        return emb


# ── Module singleton ─────────────────────────────────────────────────────────
# Lazy-init the engine; the backend model is loaded only when first used.
_engine: Optional[VoiceprintEngine] = None


def get_engine() -> VoiceprintEngine:
    global _engine
    if _engine is None:
        _engine = VoiceprintEngine()
    return _engine
