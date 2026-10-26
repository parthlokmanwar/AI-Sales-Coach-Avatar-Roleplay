"""
LLM call abstraction — OpenRouter (primary) or Ollama (fallback).

Provider is selected by LLM_PROVIDER env var (default: "openrouter").
Set LLM_PROVIDER=ollama in .env to revert to local Ollama for all calls.

All call sites in rag/engine.py route through call_llm() — no scattered
requests.post calls to Ollama anymore.
"""

import os
import time
import json
import requests
from typing import Iterator
from pathlib import Path

# Mirror the .env loading pattern used in main.py so this module can be imported
# standalone (e.g. from a test script) without FastAPI boot.
# .env wins over an inherited env var (a blank .env value never clobbers one) —
# same rule as main._load_env_file, so a stale shell/--reload-parent value can't
# silently pin a deprecated model. See the comment in main.py for the full story.
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
            if _k and (_v or _k not in os.environ):
                os.environ[_k] = _v

LLM_PROVIDER    = os.getenv("LLM_PROVIDER",   "openrouter")
OPENROUTER_KEY  = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL  = "https://openrouter.ai/api/v1"
OLLAMA_BASE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# OpenRouter model names — read from .env with sensible defaults.
PITCH_MODEL = os.getenv("PITCH_MODEL", "openai/gpt-4o")
CHAT_MODEL  = os.getenv("CHAT_MODEL",  "openai/gpt-4o-mini")
CALC_MODEL  = os.getenv("CALC_MODEL",  CHAT_MODEL)  # narrates pre-computed numbers; gpt-4o-mini suffices

# Ollama fallback model names — used only when LLM_PROVIDER=ollama.
OLLAMA_PITCH_MODEL = os.getenv("OLLAMA_PITCH_MODEL", "qwen2.5:14b")
OLLAMA_CHAT_MODEL  = os.getenv("OLLAMA_CHAT_MODEL",  "llama3.1:8b")
OLLAMA_CALC_MODEL  = os.getenv("OLLAMA_CALC_MODEL",  "qwen2.5:14b")

# Maps model_role → (openrouter_model, ollama_model)
_ROLE_MAP = {
    "pitch": (PITCH_MODEL, OLLAMA_PITCH_MODEL),
    "chat":  (CHAT_MODEL,  OLLAMA_CHAT_MODEL),
    "calc":  (CALC_MODEL,  OLLAMA_CALC_MODEL),
}


def call_llm(
    prompt: str,
    *,
    system_prompt: str = "",
    model_role: str = "chat",
    temperature: float = 0.2,
    top_p: float = 0.9,
    max_tokens: int = 2000,
    json_mode: bool = False,
    timeout: int = 180,
) -> str:
    """
    Single entry point for all LLM calls.

    model_role: "chat" | "pitch" | "calc"
      - chat  → CHAT_MODEL  (conversational responses, gpt-4o-mini by default)
      - pitch → PITCH_MODEL (pitch generation, gpt-4o by default)
      - calc  → CALC_MODEL  (EMI narration, defaults to CHAT_MODEL)

    system_prompt: when provided, sent as a system message before the user message
      (OpenRouter) or as the Ollama `system` field. Enables prefix caching for
      content that is identical across multiple calls in the same pitch session.

    Raises requests.HTTPError or requests.Timeout on failure.
    """
    if LLM_PROVIDER == "ollama":
        return _ollama(
            prompt,
            system_prompt=system_prompt,
            model_role=model_role,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            json_mode=json_mode,
            timeout=timeout,
        )
    return _openrouter(
        prompt,
        system_prompt=system_prompt,
        model_role=model_role,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        json_mode=json_mode,
        timeout=timeout,
    )


def _openrouter(
    prompt: str,
    *,
    system_prompt: str = "",
    model_role: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    json_mode: bool,
    timeout: int,
) -> str:
    model = _ROLE_MAP.get(model_role, (CHAT_MODEL, OLLAMA_CHAT_MODEL))[0]
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
    }
    # When system_prompt is provided it becomes the first message, enabling
    # OpenAI prefix caching for content that is byte-identical across calls
    # (e.g. persona + distilled facts + fact lock shared across 10 pitch sections).
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    resp = requests.post(
        f"{OPENROUTER_URL}/chat/completions",
        headers=headers,
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _ollama(
    prompt: str,
    *,
    system_prompt: str = "",
    model_role: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    json_mode: bool,
    timeout: int,
) -> str:
    model = _ROLE_MAP.get(model_role, (CHAT_MODEL, OLLAMA_CHAT_MODEL))[1]
    body: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "num_predict": max_tokens,
        },
    }
    if system_prompt:
        body["system"] = system_prompt
    if json_mode:
        body["format"] = "json"

    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def call_llm_stream(
    prompt: str,
    *,
    system_prompt: str = "",
    model_role: str = "chat",
    temperature: float = 0.2,
    top_p: float = 0.9,
    max_tokens: int = 2000,
    timeout: int = 180,
) -> Iterator[str]:
    """
    Streaming variant of call_llm(). Yields text tokens as they arrive from
    the provider.  Falls back to a single-chunk yield if streaming is
    unavailable (e.g. Ollama with stream=False env override).

    Usage:
        for token in call_llm_stream(prompt, model_role="chat"):
            print(token, end="", flush=True)
    """
    if LLM_PROVIDER == "ollama":
        yield from _ollama_stream(
            prompt,
            system_prompt=system_prompt,
            model_role=model_role,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    else:
        yield from _openrouter_stream(
            prompt,
            system_prompt=system_prompt,
            model_role=model_role,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            timeout=timeout,
        )


def _openrouter_stream(
    prompt: str,
    *,
    system_prompt: str,
    model_role: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    timeout: int,
) -> Iterator[str]:
    model = _ROLE_MAP.get(model_role, (CHAT_MODEL, OLLAMA_CHAT_MODEL))[0]
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
    }
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": True,
    }
    resp = requests.post(
        f"{OPENROUTER_URL}/chat/completions",
        headers=headers,
        json=body,
        stream=True,
        timeout=timeout,
    )
    resp.raise_for_status()
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data: "):
            continue
        chunk = line[6:]
        if chunk.strip() == "[DONE]":
            break
        try:
            data = json.loads(chunk)
            token = data["choices"][0]["delta"].get("content", "")
            if token:
                yield token
        except (KeyError, json.JSONDecodeError):
            continue


def _ollama_stream(
    prompt: str,
    *,
    system_prompt: str,
    model_role: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    timeout: int,
) -> Iterator[str]:
    model = _ROLE_MAP.get(model_role, (CHAT_MODEL, OLLAMA_CHAT_MODEL))[1]
    body: dict = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "num_predict": max_tokens,
        },
    }
    if system_prompt:
        body["system"] = system_prompt
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json=body,
        stream=True,
        timeout=timeout,
    )
    resp.raise_for_status()
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        try:
            data = json.loads(raw_line)
            token = data.get("response", "")
            if token:
                yield token
            if data.get("done", False):
                break
        except json.JSONDecodeError:
            continue


def resolved_model(model_role: str) -> str:
    """Return the actual model name that will be used for a given role."""
    idx = 0 if LLM_PROVIDER != "ollama" else 1
    return _ROLE_MAP.get(model_role, (CHAT_MODEL, OLLAMA_CHAT_MODEL))[idx]


def print_config():
    """Print resolved provider/model config — never prints the key value."""
    key_ok = bool(OPENROUTER_KEY) if LLM_PROVIDER == "openrouter" else True
    print(
        f"[LLM CONFIG] provider={LLM_PROVIDER}"
        f"  PITCH_MODEL={resolved_model('pitch')}"
        f"  CHAT_MODEL={resolved_model('chat')}"
        f"  key_loaded={key_ok}"
    )
