"""Screenshot / image parser using vision models.

Steps:
1. Read uploaded bytes; build a data: URL for the vision API.
2. describe_image_data_url: Gemini, Anthropic, or OpenAI per LLM_PROVIDER + API keys.
3. Prepend optional feature_name; store analysis in raw_context for test generation.
"""

import asyncio
import base64
from typing import Any

from starlette.datastructures import UploadFile

from backend.config import effective_llm_provider, get_effective_settings, resolved_model_id
from backend.services.llm_usage import log_anthropic_usage, log_gemini_usage, log_openai_usage
from backend.services.parsers.base import BaseParser, InputFieldDef, ParsedInput, ParserMeta
from backend.services.parsers.registry import ParserRegistry


async def describe_image_data_url(
    data_url: str,
    settings: Any,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> str:
    """Call vision API to describe a UI image for QA.

    Respects explicit UI/API provider (no fallback when set). Model id comes from
    optional override or .env default for that provider. Shared with the Figma
    parser for rendered-frame analysis.
    """
    provider = effective_llm_provider(settings, provider_override)
    model_id = resolved_model_id(settings, provider, model_override)
    prompt = """Analyze this UI screenshot for QA test design.
List:
1) Screen purpose (short)
2) Visible UI elements (buttons, inputs, links, labels)
3) Likely user actions and flows
4) Validation or error scenarios you can infer
Respond in plain text, concise bullet points."""

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("Gemini API key is required for screenshot analysis — add it in Settings")
        return await _gemini_vision_describe(data_url, prompt, settings, model_id)

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("Anthropic API key is required for screenshot analysis — add it in Settings")
        from anthropic import AsyncAnthropic

        media_type = data_url.split(";")[0].split(":")[1]
        b64_data = data_url.split(",", 1)[1]
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        msg = await client.messages.create(
            model=model_id,
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64_data,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        log_anthropic_usage(msg, op="vision_describe", model=model_id)
        parts: list[str] = []
        for b in msg.content:
            if hasattr(b, "text"):
                parts.append(b.text)
        return "\n".join(parts)

    if provider != "openai":
        raise ValueError(f"Unknown LLM_PROVIDER for vision: {provider}")

    if not settings.openai_api_key:
        raise ValueError("OpenAI API key is required for screenshot analysis — add it in Settings")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.chat.completions.create(
        model=model_id,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        max_tokens=2048,
    )
    log_openai_usage(resp, op="vision_describe", model=model_id)
    return resp.choices[0].message.content or ""


async def _gemini_vision_describe(
    data_url: str,
    prompt: str,
    settings: Any,
    model_id: str,
) -> str:
    """Gemini multimodal: text + inline image bytes (runs sync SDK in a thread)."""
    import google.generativeai as genai

    mime = data_url.split(";")[0].split(":")[1]
    b64_data = data_url.split(",", 1)[1]
    raw = base64.standard_b64decode(b64_data)

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(model_id)

    def _sync_call() -> str:
        response = model.generate_content(
            [
                prompt,
                {"mime_type": mime, "data": raw},
            ],
            generation_config=genai.GenerationConfig(max_output_tokens=2048),
        )
        log_gemini_usage(response, op="vision_describe", model=model_id)
        return response.text or ""

    return await asyncio.to_thread(_sync_call)


class ScreenshotParser(BaseParser):
    meta = ParserMeta(
        name="screenshot",
        display_name="Screenshot",
        description="Upload a UI screenshot; a vision model extracts elements and flows.",
        input_fields=[
            InputFieldDef(
                name="feature_name",
                type="text",
                label="Feature name (optional)",
                placeholder="e.g. dashboard",
                required=False,
            ),
        ],
        accepts_file=True,
    )

    async def parse(self, data: dict[str, Any], file: UploadFile | None) -> ParsedInput:
        if file is None:
            raise ValueError("Image file is required for screenshot input")

        content = await file.read()
        if not content:
            raise ValueError("Empty file upload")

        mime = file.content_type or "image/png"
        b64 = base64.standard_b64encode(content).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"

        settings = get_effective_settings()
        feature = str(data.get("feature_name") or "").strip()

        # Injected by generate router so vision matches UI / request
        llm_override = data.pop("_llm_provider", None)
        model_override = data.pop("_llm_model", None)

        vision_text = await describe_image_data_url(
            data_url,
            settings,
            provider_override=llm_override,
            model_override=model_override,
        )

        raw = f"Feature hint: {feature or 'general'}\n\nVision analysis:\n{vision_text}"

        ext = "png"
        if mime == "image/jpeg":
            ext = "jpg"
        elif mime == "image/webp":
            ext = "webp"
        elif mime == "image/gif":
            ext = "gif"

        return ParsedInput(
            source_type="screenshot",
            feature_name=feature or "screenshot",
            screens=[],
            ui_elements=[],
            user_actions=[],
            business_rules=[],
            raw_context=raw,
            metadata={
                "filename": file.filename,
                "mime": mime,
                "_image_bytes": content,
                "_image_mime": mime,
                "_image_ext": ext,
            },
        )


# Self-register on import
ParserRegistry.register(ScreenshotParser())
