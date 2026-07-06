"""Logging-only helpers to record per-call LLM token usage.

Each provider exposes usage differently:
- OpenAI:    resp.usage.{prompt_tokens, completion_tokens, total_tokens}
- Anthropic: msg.usage.{input_tokens, output_tokens}
- Gemini:    response.usage_metadata.{prompt_token_count, candidates_token_count, total_token_count}

These helpers normalize that to a single INFO log line. They never raise —
if usage is missing, a WARNING is logged and the caller proceeds.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def log_openai_usage(resp: Any, *, op: str, model: str) -> None:
    usage = getattr(resp, "usage", None)
    if usage is None:
        logger.warning("llm_usage op=%s provider=openai model=%s usage=missing", op, model)
        return
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", prompt + completion) or (prompt + completion))
    logger.info(
        "llm_usage op=%s provider=openai model=%s prompt=%d completion=%d total=%d",
        op, model, prompt, completion, total,
    )


def log_anthropic_usage(msg: Any, *, op: str, model: str) -> None:
    usage = getattr(msg, "usage", None)
    if usage is None:
        logger.warning("llm_usage op=%s provider=anthropic model=%s usage=missing", op, model)
        return
    prompt = int(getattr(usage, "input_tokens", 0) or 0)
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    total = prompt + completion
    logger.info(
        "llm_usage op=%s provider=anthropic model=%s prompt=%d completion=%d total=%d",
        op, model, prompt, completion, total,
    )


def log_gemini_usage(response: Any, *, op: str, model: str) -> None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        logger.warning("llm_usage op=%s provider=gemini model=%s usage=missing", op, model)
        return
    prompt = int(getattr(usage, "prompt_token_count", 0) or 0)
    completion = int(getattr(usage, "candidates_token_count", 0) or 0)
    total = int(getattr(usage, "total_token_count", prompt + completion) or (prompt + completion))
    logger.info(
        "llm_usage op=%s provider=gemini model=%s prompt=%d completion=%d total=%d",
        op, model, prompt, completion, total,
    )
