"""Provider-agnostic tool-calling loop.

Supports Anthropic and OpenAI tool calling. Gemini is not yet wired
(the function-calling shape differs and is left for a follow-up).

The loop is bounded by:
- ``budget`` (caller-owned ``Budget``; tokens recorded each turn)
- ``max_turns`` (hard cap on tool-use rounds)
- The LLM emitting the special ``done`` tool

Tool execution is delegated to ``on_tool_call(name, input) -> str``. The
returned string becomes the ``tool_result`` content fed back to the model.
The orchestrator uses this hook to dispatch to the BrowserDriver and to
record entries in the Evidence Ledger.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from backend.config import Settings, effective_llm_provider
from backend.services.browser_explorer.budget import Budget, BudgetExceeded

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolLoopResult:
    stopped: str  # "done" | "max_turns" | "budget" | "error" | "no_tool_call"
    final_text: str
    turns: int
    last_tool: str | None
    error: str | None = None


# Type for the caller-supplied tool dispatcher.
# Returns a string (will be sent back to the LLM as tool_result content).
ToolCallback = Callable[[str, dict[str, Any]], Awaitable[str]]


async def run_tool_loop(
    *,
    system: str,
    user: str,
    tools: list[Tool],
    on_tool_call: ToolCallback,
    settings: Settings,
    budget: Budget,
    model_id: str,
    max_turns: int = 80,
    done_tool_name: str = "done",
    provider_override: str | None = None,
) -> ToolLoopResult:
    """Execute the tool-using agent loop until ``done_tool_name`` is called
    or any cap is hit. Dispatches to Anthropic or OpenAI based on the
    configured / overridden provider.
    """
    provider = (provider_override or effective_llm_provider(settings, None)).lower()
    common = dict(
        system=system,
        user=user,
        tools=tools,
        on_tool_call=on_tool_call,
        settings=settings,
        budget=budget,
        model_id=model_id,
        max_turns=max_turns,
        done_tool_name=done_tool_name,
    )
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            return ToolLoopResult(
                stopped="error", final_text="", turns=0, last_tool=None,
                error="Anthropic API key is not set — add it in Settings",
            )
        return await _run_anthropic(**common)
    if provider == "openai":
        if not settings.openai_api_key:
            return ToolLoopResult(
                stopped="error", final_text="", turns=0, last_tool=None,
                error="OpenAI API key is not set — add it in Settings",
            )
        return await _run_openai(**common)
    return ToolLoopResult(
        stopped="error", final_text="", turns=0, last_tool=None,
        error=f"Tool use is not yet supported for provider {provider!r}. Use openai or anthropic.",
    )


async def _run_anthropic(
    *,
    system: str,
    user: str,
    tools: list[Tool],
    on_tool_call: ToolCallback,
    settings: Settings,
    budget: Budget,
    model_id: str,
    max_turns: int,
    done_tool_name: str,
) -> ToolLoopResult:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    tool_specs = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]

    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    final_text = ""
    last_tool: str | None = None

    for turn in range(1, max_turns + 1):
        try:
            resp = await client.messages.create(
                model=model_id,
                max_tokens=2048,
                system=system,
                tools=tool_specs,
                messages=messages,
            )
        except Exception as e:
            logger.exception("anthropic call failed on turn %d", turn)
            return ToolLoopResult(
                stopped="error", final_text=final_text, turns=turn, last_tool=last_tool, error=str(e)
            )

        # Token accounting → budget. Anthropic's usage object is not awaitable.
        usage = getattr(resp, "usage", None)
        if usage is not None:
            try:
                budget.record_tokens(int(getattr(usage, "input_tokens", 0)) + int(getattr(usage, "output_tokens", 0)))
            except BudgetExceeded as e:
                return ToolLoopResult(
                    stopped="budget", final_text=final_text, turns=turn, last_tool=last_tool, error=e.reason
                )

        # Collect text + tool_use blocks from the assistant message.
        assistant_blocks: list[dict[str, Any]] = []
        tool_uses: list[tuple[str, str, dict]] = []  # (id, name, input)
        for b in resp.content:
            block_type = getattr(b, "type", None)
            if block_type == "text":
                txt = getattr(b, "text", "")
                final_text = txt or final_text
                assistant_blocks.append({"type": "text", "text": txt})
            elif block_type == "tool_use":
                tu_id = getattr(b, "id", "")
                tu_name = getattr(b, "name", "")
                tu_input = getattr(b, "input", {}) or {}
                tool_uses.append((tu_id, tu_name, tu_input))
                assistant_blocks.append(
                    {"type": "tool_use", "id": tu_id, "name": tu_name, "input": tu_input}
                )

        # Always echo the assistant turn into the conversation, exactly as received.
        messages.append({"role": "assistant", "content": assistant_blocks})

        if not tool_uses:
            # Model emitted text only with no tool call — treat as "give up".
            return ToolLoopResult(
                stopped="no_tool_call", final_text=final_text, turns=turn, last_tool=last_tool
            )

        # If the LLM called the done tool, finish.
        for tu_id, tu_name, tu_input in tool_uses:
            if tu_name == done_tool_name:
                last_tool = tu_name
                # Send a final tool_result so the conversation is well-formed.
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tu_id,
                                "content": "ok",
                            }
                        ],
                    }
                )
                reason = (tu_input or {}).get("reason", "")
                return ToolLoopResult(
                    stopped="done",
                    final_text=reason or final_text,
                    turns=turn,
                    last_tool=tu_name,
                )

        # Otherwise: dispatch each tool call to the orchestrator and feed
        # results back. We do them sequentially because most browser actions
        # depend on the page state from the previous one.
        tool_results: list[dict[str, Any]] = []
        for tu_id, tu_name, tu_input in tool_uses:
            last_tool = tu_name
            try:
                result_str = await on_tool_call(tu_name, tu_input)
            except BudgetExceeded as e:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu_id,
                        "content": f"BUDGET_EXCEEDED: {e.reason}",
                        "is_error": True,
                    }
                )
                messages.append({"role": "user", "content": tool_results})
                return ToolLoopResult(
                    stopped="budget",
                    final_text=final_text,
                    turns=turn,
                    last_tool=last_tool,
                    error=e.reason,
                )
            except Exception as e:
                logger.warning("tool %s raised: %s", tu_name, e)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu_id,
                        "content": f"ERROR: {type(e).__name__}: {e}",
                        "is_error": True,
                    }
                )
            else:
                # Result content must be a string for Anthropic.
                content = result_str if isinstance(result_str, str) else json.dumps(result_str)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu_id,
                        "content": content,
                    }
                )

        messages.append({"role": "user", "content": tool_results})

    return ToolLoopResult(
        stopped="max_turns", final_text=final_text, turns=max_turns, last_tool=last_tool
    )


async def _run_openai(
    *,
    system: str,
    user: str,
    tools: list[Tool],
    on_tool_call: ToolCallback,
    settings: Settings,
    budget: Budget,
    model_id: str,
    max_turns: int,
    done_tool_name: str,
) -> ToolLoopResult:
    """OpenAI Chat Completions tool-calling loop.

    Surface differences vs Anthropic:
    - Tool spec uses ``{type: "function", function: {name, description, parameters}}``.
    - Tool calls live on ``message.tool_calls`` with ``arguments`` as a JSON STRING
      (not a parsed dict — we json.loads it ourselves).
    - Tool results are echoed as ``{role: "tool", tool_call_id, content}`` — one
      message per result, not bundled like Anthropic's tool_result blocks.
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    tool_specs = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    final_text = ""
    last_tool: str | None = None

    for turn in range(1, max_turns + 1):
        try:
            resp = await client.chat.completions.create(
                model=model_id,
                messages=messages,
                tools=tool_specs,
                max_tokens=2048,
            )
        except Exception as e:
            logger.exception("openai call failed on turn %d", turn)
            return ToolLoopResult(
                stopped="error", final_text=final_text, turns=turn, last_tool=last_tool, error=str(e)
            )

        # Token accounting → budget.
        usage = getattr(resp, "usage", None)
        if usage is not None:
            try:
                budget.record_tokens(int(getattr(usage, "prompt_tokens", 0)) + int(getattr(usage, "completion_tokens", 0)))
            except BudgetExceeded as e:
                return ToolLoopResult(
                    stopped="budget", final_text=final_text, turns=turn, last_tool=last_tool, error=e.reason
                )

        choice = resp.choices[0]
        msg = choice.message
        text = msg.content or ""
        if text:
            final_text = text
        tool_calls = list(msg.tool_calls or [])

        # Echo the assistant message into history. OpenAI requires
        # serialized tool_calls to be present if any.
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": text or None,
        }
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ]
        messages.append(assistant_msg)

        if not tool_calls:
            return ToolLoopResult(
                stopped="no_tool_call", final_text=final_text, turns=turn, last_tool=last_tool
            )

        # Check for the done tool first — execute and short-circuit.
        for tc in tool_calls:
            if tc.function.name == done_tool_name:
                last_tool = tc.function.name
                try:
                    parsed_args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    parsed_args = {}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": "ok",
                    }
                )
                reason = parsed_args.get("reason", "")
                return ToolLoopResult(
                    stopped="done",
                    final_text=reason or final_text,
                    turns=turn,
                    last_tool=last_tool,
                )

        # Otherwise dispatch each tool call sequentially.
        for tc in tool_calls:
            last_tool = tc.function.name
            try:
                parsed_args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"ERROR: invalid JSON in arguments: {e}",
                    }
                )
                continue

            try:
                result_str = await on_tool_call(tc.function.name, parsed_args)
            except BudgetExceeded as e:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"BUDGET_EXCEEDED: {e.reason}",
                    }
                )
                return ToolLoopResult(
                    stopped="budget",
                    final_text=final_text,
                    turns=turn,
                    last_tool=last_tool,
                    error=e.reason,
                )
            except Exception as e:
                logger.warning("tool %s raised: %s", tc.function.name, e)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"ERROR: {type(e).__name__}: {e}",
                    }
                )
            else:
                content = result_str if isinstance(result_str, str) else json.dumps(result_str)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": content,
                    }
                )

    return ToolLoopResult(
        stopped="max_turns", final_text=final_text, turns=max_turns, last_tool=last_tool
    )
