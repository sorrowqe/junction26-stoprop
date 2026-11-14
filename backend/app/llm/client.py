"""OpenAI-compatible client for the Hostinger AI Router: timeouts, one retry, usage logging.

Every HTTP attempt is logged to SQLite (kind, model, tokens in/out, latency, ok/error class).
The API key is never logged or returned.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import httpx2  # the HTTP library openai 3.x is built on (not classic httpx)
import openai
from openai import AsyncOpenAI

from app import config
from app.store import get_store

TIMEOUT_S = 30.0
RETRIES = 1  # one retry on 5xx / timeout / connection error

# Tests can replace this to inject an httpx2.MockTransport.
http_client_factory = None


class LLMError(Exception):
    """The LLM call failed (after the retry). Callers fail soft."""


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    model: str
    credits: float = 0.0


def _client() -> AsyncOpenAI:
    kwargs = {}
    if http_client_factory is not None:
        kwargs["http_client"] = http_client_factory()
    return AsyncOpenAI(
        base_url=config.router_base_url(),
        api_key=config.router_api_key(),
        timeout=TIMEOUT_S,
        max_retries=0,
        **kwargs,
    )


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError, httpx2.TimeoutException)):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code >= 500


async def chat(messages: list[dict], *, model: str, max_tokens: int = 2000, kind: str = "tagger") -> LLMResult:
    if not config.llm_configured():
        raise LLMError("LLM not configured")
    store = get_store()
    last: Exception | None = None
    async with _client() as client:
        for attempt in range(RETRIES + 1):
            t0 = time.monotonic()
            try:
                resp = await client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, temperature=0,
                )
            except Exception as exc:  # noqa: BLE001 - classified below
                latency = int((time.monotonic() - t0) * 1000)
                store.log_usage(kind, model, 0, 0, latency, ok=False, error=type(exc).__name__)
                last = exc
                if attempt < RETRIES and _retryable(exc):
                    continue
                raise LLMError(type(exc).__name__) from exc
            latency = int((time.monotonic() - t0) * 1000)
            usage = resp.usage
            tin = getattr(usage, "prompt_tokens", 0) or 0
            tout = getattr(usage, "completion_tokens", 0) or 0
            text = (resp.choices[0].message.content or "") if resp.choices else ""
            credits = credits_charged(usage)
            store.log_usage(kind, model, tin, tout, latency, ok=True, credits=credits)
            return LLMResult(text=text, input_tokens=tin, output_tokens=tout, latency_ms=latency, model=model,
                             credits=credits)
    raise LLMError(type(last).__name__ if last else "unknown")


EMBED_BATCH = 256


async def embed(texts: list[str], *, model: str) -> tuple[list[list[float]], dict]:
    """Embeddings in batches (swarm deep mode). Same retry + usage logging as chat (kind "embed").
    Returns (vectors in input order, {"input_tokens", "credits", "calls"})."""
    if not (config.router_api_key() and config.router_base_url()):
        raise LLMError("LLM not configured")
    store = get_store()
    vectors: list[list[float]] = []
    meta = {"input_tokens": 0, "credits": 0.0, "calls": 0}
    async with _client() as client:
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start:start + EMBED_BATCH]
            for attempt in range(RETRIES + 1):
                t0 = time.monotonic()
                try:
                    resp = await client.embeddings.create(model=model, input=batch)
                except Exception as exc:  # noqa: BLE001 - classified below
                    store.log_usage("embed", model, 0, 0, int((time.monotonic() - t0) * 1000), ok=False,
                                    error=type(exc).__name__)
                    if attempt < RETRIES and _retryable(exc):
                        continue
                    raise LLMError(type(exc).__name__) from exc
                usage = resp.usage
                tin = getattr(usage, "prompt_tokens", 0) or getattr(usage, "total_tokens", 0) or 0
                credits = credits_charged(usage)
                store.log_usage("embed", model, tin, 0, int((time.monotonic() - t0) * 1000), ok=True, credits=credits)
                meta["input_tokens"] += tin
                meta["credits"] += credits
                meta["calls"] += 1
                vectors.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
                break
    return vectors, meta


def credits_charged(usage) -> float:
    """The Hostinger router reports billing as usage.credits_charged (an extra field on the usage object)."""
    value = getattr(usage, "credits_charged", None)
    if value is None:
        value = (getattr(usage, "model_extra", None) or {}).get("credits_charged")
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- JSON parsing

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json_array(text: str) -> list | None:
    """Best-effort: a JSON array from model output (code fences, prose around it, {"spans": [...]}).

    Returns None if no array can be parsed; [] is a valid "nothing found".
    """
    if text is None:
        return None
    candidates = [text.strip()]
    candidates += [m.group(1).strip() for m in _FENCE.finditer(text)]
    start, end = text.find("["), text.rfind("]")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for c in candidates:
        if not c:
            continue
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("spans", "findings", "results", "items"):
                if isinstance(data.get(key), list):
                    return data[key]
    return None
