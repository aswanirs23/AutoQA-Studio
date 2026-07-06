"""Map provider SDK exceptions to HTTP-friendly errors."""

from __future__ import annotations

from fastapi import HTTPException


def map_upstream_exception(prefix: str, exc: BaseException) -> HTTPException:
    """Return 429 for quota/rate limits (e.g. Gemini ResourceExhausted), else 502 with prefix."""
    name = type(exc).__name__
    msg = str(exc)
    if name == "ResourceExhausted":
        return _quota_http(msg)
    if "429" in msg and ("quota" in msg.lower() or "rate" in msg.lower()):
        return _quota_http(msg)
    if "quota" in msg.lower() and "exceed" in msg.lower():
        return _quota_http(msg)
    return HTTPException(status_code=502, detail=f"{prefix}: {msg[:2000]}")


def _quota_http(msg: str) -> HTTPException:
    hint = (
        "The AI provider returned quota or rate limit (429). For Gemini: wait and retry, "
        "try another model id (e.g. gemini-1.5-flash), or enable billing. "
        "For OpenAI/Anthropic: check plan and rate limits. "
    )
    return HTTPException(status_code=429, detail=hint + msg[:1500])
