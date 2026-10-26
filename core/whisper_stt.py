"""
Local speech-to-text via faster-whisper — Deepgram's offline fallback.

When Deepgram fails (network down, key expired, free-tier exhausted), the
frontend retries the same audio chunk against this endpoint. faster-whisper
runs the OpenAI Whisper model locally via CTranslate2, so this path is:
  - free (no API quota)
  - fully offline (no network call)
  - format-agnostic (webm/opus, mp4, mp3, wav all decoded via librosa)

Trade-off: ~80-150ms per chunk on RTX-class GPU with large-v3-turbo;
~1.5-3s on CPU. Fast enough on GPU to act as either primary or fallback.

Configuration (env vars):
  WHISPER_MODEL_SIZE      — tiny | base | small | medium | large-v3-turbo
                            (default: large-v3-turbo, ~809 MB int8)
  WHISPER_LIVE_MODEL_SIZE — model for the always-on live path
                            (default: small — minimum viable for Hindi/Marathi accuracy;
                             set large-v3-turbo on GPU for best quality, tiny only if
                             CPU-bound and latency is the hard constraint)
  WHISPER_COMPUTE_TYPE    — int8 | int8_float16 | float16 | float32
                            default int8: smallest VRAM, near-float16 accuracy
  WHISPER_DEVICE          — cpu | cuda  (default: cuda; auto-falls back to cpu
                            if torch reports no CUDA device)
  WHISPER_LANGUAGE        — auto | en | hi | mr  (default: auto)
                            forcing the language speeds up inference + improves accuracy

Public API:
  engine = get_engine()
  result = engine.transcribe(audio_bytes, content_type, language_hint="auto")
  # result = {"transcript": "...", "language": "en", "ms": 1234}
"""

from __future__ import annotations

import io
import logging
import os
import time
from typing import Optional

import numpy as np

log = logging.getLogger("hdfc.whisper")

# ── Config from env ──────────────────────────────────────────────────────────
#
# Default stack: large-v3-turbo on CUDA with int8 quantisation.
#   - large-v3-turbo: OpenAI's distilled large-v3 (~809 MB, ~8x faster than
#     full large-v3, near-identical WER on Indian English / Hindi / Marathi).
#   - cuda + int8:   ~3 GB VRAM on RTX 5060, ~80-150ms per short clip.
#                    Falls back to CPU automatically if torch reports no CUDA.
_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "large-v3-turbo").lower()
_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8").lower()
_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda").lower()
_DEFAULT_LANG = os.environ.get("WHISPER_LANGUAGE", "auto").lower()


# GPU verification at import time. torch is an optional dep (pulled in by
# pyannote in this project) so we import defensively.
#
# IMPORTANT: torch.cuda.is_available() is only an *informational* probe here.
# faster-whisper runs on CTranslate2, which ships its own CUDA runtime via the
# nvidia-cublas-cu12 / nvidia-cudnn-cu12 / nvidia-cuda-nvrtc-cu12 wheels — it does
# NOT use torch for inference. A CPU-only torch build (torch==x.y.z+cpu) reports
# cuda_available=False, but CTranslate2 can still use the GPU. We previously
# downgraded _DEVICE to "cpu" on that false negative, which forced large-v3-turbo
# onto the CPU (~30s/clip) AND skipped the cuBLAS/cuDNN DLL registration below.
# So we no longer downgrade here — we keep the requested device and rely on the
# runtime cuda→cpu fallback in WhisperSTT.model if CUDA genuinely isn't usable.
if _DEVICE == "cuda":
    try:
        import torch  # noqa: F401
        if torch.cuda.is_available():
            log.info(
                f"[WHISPER] CUDA available (torch) — model='{_MODEL_SIZE}' will "
                f"run on GPU: {torch.cuda.get_device_name(0)}"
            )
        else:
            log.info(
                "[WHISPER] torch reports no CUDA (likely a CPU-only torch build) "
                "— this does NOT block CTranslate2's own CUDA runtime; keeping "
                "device=cuda. Will fall back to CPU at load time only if the CUDA "
                "DLLs are truly unavailable."
            )
    except ImportError:
        log.info(
            "[WHISPER] torch not installed — skipping GPU probe and trusting "
            "CTranslate2 to find its CUDA DLLs."
        )


# ── Audio decoder (ffmpeg pipe, same approach as voiceprint engine) ─────────
#
# Why we don't use librosa.load() here anymore:
#   librosa relies on soundfile → audioread → ffmpeg.  soundfile alone can't
#   read webm/opus (the format the browser MediaRecorder produces) so the
#   call falls through to audioread, which silently fails on Windows if
#   ffmpeg isn't on PATH.  Result: Whisper sees "Format not recognised" and
#   every Hindi/Marathi clip dies.
#
# Direct ffmpeg pipe (using the portable binary shipped by imageio-ffmpeg)
# handles every browser format reliably, on every machine, no system install
# needed.

def _ffmpeg_binary() -> Optional[str]:
    """Locate a usable ffmpeg binary. Prefers the portable wheel."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg")


def _decode_audio_to_pcm(audio_bytes: bytes, target_sr: int = 16000) -> np.ndarray:
    """Decode arbitrary audio bytes (webm/opus, mp4, wav, mp3, ogg) → mono
    float32 PCM @ 16 kHz, normalised to [-1, 1] for Whisper.

    Two ffmpeg attempts:
      1. stdin pipe — fastest, no disk I/O; works for webm/wav/mp3/ogg.
      2. temp file  — a pipe is NOT seekable, so containers whose index sits
         at the END of the file (mp4/m4a from Safari/iOS MediaRecorder, some
         Android recordings) fail attempt 1 with AVERROR_INVALIDDATA
         ("Invalid data found when processing input"). A real file lets
         ffmpeg seek and decode them.
    """
    if not audio_bytes:
        raise ValueError("Empty audio upload (0 bytes).")

    ffmpeg = _ffmpeg_binary()
    if ffmpeg:
        import subprocess

        def _to_pcm(stdout: bytes) -> np.ndarray:
            pcm_i16 = np.frombuffer(stdout, dtype=np.int16)
            if pcm_i16.size == 0:
                raise ValueError("ffmpeg produced empty PCM output.")
            return pcm_i16.astype(np.float32) / 32768.0

        out_args = ["-f", "s16le", "-ac", "1", "-ar", str(target_sr), "pipe:1"]

        # ── Attempt 1: stdin pipe ────────────────────────────────────────
        pipe_fail = ""
        try:
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", *out_args],
                input=audio_bytes,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                return _to_pcm(proc.stdout)
            pipe_fail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()[-200:]
            log.warning(
                f"[WHISPER] pipe decode failed (rc={proc.returncode}, "
                f"size={len(audio_bytes)}B, head={audio_bytes[:12].hex()}) — retrying via temp file"
            )
        except subprocess.TimeoutExpired:
            raise ValueError("Whisper decode timed out.")
        except Exception as e:
            pipe_fail = str(e)
            log.warning(f"[WHISPER] pipe decode errored: {e} — retrying via temp file")

        # ── Attempt 2: temp file (seekable — required for mp4/m4a) ──────
        import tempfile
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", tmp_path, *out_args],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                return _to_pcm(proc.stdout)
            file_fail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()[-200:]
            raise ValueError(
                f"Could not decode the recording (size={len(audio_bytes)}B, "
                f"head={audio_bytes[:12].hex()}). ffmpeg pipe: {pipe_fail or 'n/a'} | "
                f"file: {file_fail or 'n/a'}"
            )
        except subprocess.TimeoutExpired:
            raise ValueError("Whisper decode timed out.")
        except ValueError:
            raise
        except Exception as e:
            log.warning(f"[WHISPER] ffmpeg file path errored: {e} — falling back to librosa")
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    # Last-resort librosa fallback (only works for already-decoded formats)
    try:
        import librosa
        audio, _ = librosa.load(
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
            "imageio-ffmpeg ships a portable ffmpeg — install it via requirements.txt."
        )


class WhisperSTT:
    """
    Singleton-style Whisper STT. Model is loaded lazily on first transcribe()
    call so server boot stays fast; subsequent calls reuse the loaded model.

    Thread-safety: faster-whisper's WhisperModel.transcribe() is safe to call
    from multiple threads concurrently. We wrap every call in a thread executor
    at the FastAPI layer regardless, so the asyncio loop doesn't block.
    """

    def __init__(self, model_size_override: Optional[str] = None):
        self._model = None  # lazy
        self._model_size = (model_size_override or _MODEL_SIZE).lower()
        log.info(
            f"[WHISPER] Configured (model={self._model_size}, compute={_COMPUTE_TYPE}, "
            f"device={_DEVICE}, default_lang={_DEFAULT_LANG}) — model loads on first use"
        )
        if _DEVICE == "cuda":
            log.info(f"[WHISPER] Startup confirmed: device=GPU (cuda), model={self._model_size}")
        else:
            log.warning(
                f"[WHISPER] Startup confirmed: device=CPU, model={self._model_size} — "
                f"expect ~1.5-3s per chunk vs ~80-150ms on GPU"
            )

    @property
    def model(self):
        if self._model is None:
            # On Windows + CUDA, faster-whisper needs cuBLAS/cuDNN/nvrtc DLLs
            # on the loader path. The nvidia-* pip packages ship them under
            # site-packages/nvidia/*/bin. os.add_dll_directory alone is not
            # enough because CTranslate2 chain-loads further DLLs that resolve
            # via the process PATH — so we prepend to PATH too.
            if _DEVICE == "cuda" and os.name == "nt":
                try:
                    import sysconfig
                    purelib = sysconfig.get_paths()["purelib"]
                    added = []
                    for sub in ("cublas", "cudnn", "cuda_nvrtc"):
                        dll_dir = os.path.join(purelib, "nvidia", sub, "bin")
                        if os.path.isdir(dll_dir):
                            os.add_dll_directory(dll_dir)
                            added.append(dll_dir)
                    if added:
                        os.environ["PATH"] = os.pathsep.join(added) + os.pathsep + os.environ.get("PATH", "")
                        log.info(f"[WHISPER] CUDA DLL dirs registered: {len(added)} paths")
                except Exception as e:
                    log.warning(f"[WHISPER] CUDA DLL path setup failed: {e}")
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise ImportError(
                    "faster-whisper not installed. Run `pip install faster-whisper`."
                ) from e
            log.info(
                f"[WHISPER] Loading model='{self._model_size}' on device='{_DEVICE}' "
                f"(first call — may download model if not cached)..."
            )
            t0 = time.time()
            # faster-whisper caches downloaded models in:
            #   Linux/Mac: ~/.cache/huggingface/hub/
            #   Windows:   %USERPROFILE%\.cache\huggingface\hub\
            #
            # Runtime cuda→cpu fallback: if CUDA was requested but the runtime is
            # genuinely unusable (missing cuBLAS/cuDNN DLLs, no GPU), CTranslate2
            # raises a RuntimeError here. Catch it and load on CPU so the live
            # path still works (slowly) instead of 500-ing every chunk.
            try:
                self._model = WhisperModel(
                    self._model_size,
                    device=_DEVICE,
                    compute_type=_COMPUTE_TYPE,
                )
            except Exception as e:
                if _DEVICE == "cuda":
                    log.warning(
                        f"[WHISPER] CUDA load failed ({type(e).__name__}: {e}) — "
                        f"falling back to CPU. Install nvidia-cublas-cu12 / "
                        f"nvidia-cudnn-cu12 / nvidia-cuda-nvrtc-cu12 for GPU speed."
                    )
                    self._model = WhisperModel(
                        self._model_size, device="cpu", compute_type=_COMPUTE_TYPE,
                    )
                else:
                    raise
            log.info(f"[WHISPER] Model ready in {int((time.time()-t0)*1000)}ms")
        return self._model

    def transcribe(
        self,
        audio_bytes: bytes,
        content_type: str = "audio/webm",
        language_hint: str = "auto",
        telephone: bool = False,
    ) -> dict:
        """
        Transcribe an audio chunk. Returns:
            {"transcript": "...", "language": "en"|"hi"|"mr", "ms": 1234,
             "confidence": 0.0..1.0}

        language_hint:
            "auto" — let Whisper detect (~50ms overhead, sometimes wrong on short clips)
            "en"   — force English (Hinglish words romanised correctly)
            "hi"   — force Hindi  (output in Devanagari)
            "mr"   — force Marathi (output in Devanagari)
        """
        t0 = time.time()
        if len(audio_bytes) < 800:
            raise ValueError("Audio clip too small (< 800 bytes).")

        pcm = _decode_audio_to_pcm(audio_bytes, target_sr=16000)
        duration_s = pcm.size / 16000.0
        if duration_s < 0.5:
            raise ValueError(
                f"Audio too short ({duration_s:.1f}s) — need at least 0.5s."
            )

        # Pick effective language: explicit hint > env default > auto-detect
        effective_lang: Optional[str] = None
        lang_hint = (language_hint or "auto").lower()
        if lang_hint in ("en", "english"):
            effective_lang = "en"
        elif lang_hint in ("hi", "hindi"):
            effective_lang = "hi"
        elif lang_hint in ("mr", "marathi"):
            effective_lang = "mr"
        elif _DEFAULT_LANG in ("en", "hi", "mr"):
            effective_lang = _DEFAULT_LANG
        # else: None → Whisper auto-detect

        # ── initial_prompt: prime Whisper to output in the correct script ──────
        # Without a Devanagari seed, even with language='hi'/'mr' the small model
        # sometimes romanises the output ("kya hoga" instead of "क्या होगा").
        # Providing a short Devanagari string as the prompt context forces the
        # model into the correct script from the first token.
        _INITIAL_PROMPTS: dict[str, str] = {
            "hi": "नमस्ते। यह हिंदी में बातचीत है।",
            "mr": "नमस्कार. हे मराठी संभाषण आहे.",
        }
        initial_prompt: Optional[str] = _INITIAL_PROMPTS.get(effective_lang or "")
        # Telephone audio (8kHz μ-law, quiet): the Devanagari seed prompt sends
        # whisper into repetition-hallucination loops ("लेक्ष लेक्ष लेक्ष…").
        # Verified on dumped Twilio call audio — same clip transcribes perfectly
        # without the prompt. Script forcing matters less on this path anyway.
        if telephone:
            initial_prompt = None

        # ── Beam-size strategy ────────────────────────────────────────────────
        # English short clips: greedy (beam=1) is fast and accurate enough.
        # Non-English (Hindi/Marathi): always use beam=3 even for short clips.
        #   Greedy decoding on Hindi/Marathi often picks the wrong token when two
        #   Devanagari sequences have similar probabilities — beam search catches this.
        # Longer clips (≥ 2s): beam=5 for all languages (sentence-level accuracy).
        is_non_english = effective_lang is not None and effective_lang != "en"
        if duration_s >= 2.0:
            beam = 5
        elif is_non_english:
            beam = 3   # accuracy > raw speed for Hindi/Marathi short clips
        else:
            beam = 1   # English short clips: greedy is fine and saves ~30ms

        # ── Temperature ───────────────────────────────────────────────────────
        # For Hindi/Marathi: use a fallback temperature [0.0, 0.2]. If the
        # first greedy pass has suspiciously low log-probability (model unsure),
        # faster-whisper retries with slight sampling — better than a wrong
        # deterministic result. English keeps temperature=0.0 (stable).
        temperature: object = [0.0, 0.2] if is_non_english else 0.0

        # ── no_speech_threshold ────────────────────────────────────────────────
        # Default 0.6 is too aggressive for Hindi/Marathi: the model assigns lower
        # speech probability to languages with less training data, silently dropping
        # valid speech segments. Lowering to 0.4 keeps more segments for inspection.
        no_speech_thr = 0.4 if is_non_english else 0.6

        segments, info = self.model.transcribe(
            pcm,
            language=effective_lang,
            beam_size=beam,
            best_of=beam,                          # match beam for N-best scoring
            temperature=temperature,
            initial_prompt=initial_prompt,
            no_speech_threshold=no_speech_thr,
            vad_filter=True,                       # drop pure-silence chunks early
            vad_parameters=dict(min_silence_duration_ms=500),
            condition_on_previous_text=False,      # each chunk is independent
        )

        # Materialise the segment generator into one transcript string.
        # Whisper's avg_logprob is in log space; exp() gives a 0-1 confidence proxy.
        parts: list[str] = []
        logprobs: list[float] = []
        for seg in segments:
            txt = (seg.text or "").strip()
            if txt:
                parts.append(txt)
                if seg.avg_logprob is not None:
                    logprobs.append(seg.avg_logprob)

        transcript = " ".join(parts).strip()
        confidence = float(np.exp(np.mean(logprobs))) if logprobs else 0.0
        elapsed_ms = int((time.time() - t0) * 1000)

        log.info(
            f"[WHISPER] {duration_s:.1f}s audio → '{transcript[:60]}' "
            f"hint={effective_lang or 'auto'} detected={info.language} "
            f"beam={beam} conf={confidence:.2f} in {elapsed_ms}ms"
        )

        return {
            "transcript": transcript,
            "language": info.language,
            "confidence": round(confidence, 4),
            "ms": elapsed_ms,
            "duration_s": round(duration_s, 2),
        }


# ── Module singletons ────────────────────────────────────────────────────────
_engine: Optional[WhisperSTT] = None

# Live-path model. Default is "small" — the minimum viable model for acceptable
# Hindi and Marathi accuracy. "tiny" is fast but produces incorrect Devanagari
# on short utterances and should only be used when CPU latency is the hard
# constraint. Set WHISPER_LIVE_MODEL_SIZE=large-v3-turbo on a GPU deployment
# for best multilingual quality (reuses the main engine singleton, no extra RAM).
_LIVE_MODEL_SIZE = os.environ.get("WHISPER_LIVE_MODEL_SIZE", "small").lower()
_live_engine: Optional[WhisperSTT] = None


def get_engine() -> WhisperSTT:
    global _engine
    if _engine is None:
        _engine = WhisperSTT()
    return _engine


def get_live_engine() -> WhisperSTT:
    """Live-path Whisper (WHISPER_LIVE_MODEL_SIZE, default: small).
    If the same model size as the main engine, shares the singleton."""
    global _live_engine
    if _live_engine is None:
        if _LIVE_MODEL_SIZE == _MODEL_SIZE:
            _live_engine = get_engine()
        else:
            _live_engine = WhisperSTT(model_size_override=_LIVE_MODEL_SIZE)
    return _live_engine
