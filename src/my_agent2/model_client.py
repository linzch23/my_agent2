from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass(frozen=True)
class AgentMessage:
    content: list[TextBlock | ToolUseBlock]
    stop_reason: str
    usage: Any = None


class ModelClient(Protocol):
    def create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentMessage:
        ...

    def stream_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: Callable[[str], None] | None = None,
    ) -> AgentMessage:
        ...


def build_model_client(provider: str) -> ModelClient:
    provider = provider.lower().strip()
    if provider == "anthropic":
        return AnthropicModelClient(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        )
    if provider == "deepseek":
        return OpenAICompatibleModelClient(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
    if provider == "openai-compatible":
        return OpenAICompatibleModelClient(
            api_key=os.environ["OPENAI_COMPATIBLE_API_KEY"],
            base_url=os.environ["OPENAI_COMPATIBLE_BASE_URL"],
        )
    raise ValueError(
        f"Unsupported MY_AGENT_PROVIDER={provider!r}. "
        "Use anthropic, deepseek, or openai-compatible."
    )


class AnthropicModelClient:
    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url)

    def _request_kwargs(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [_to_anthropic_message(message) for message in messages],
        }
        if tools:
            kwargs["tools"] = tools
        return kwargs

    def create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentMessage:
        response = self.client.messages.create(
            **self._request_kwargs(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
            )
        )
        return _from_anthropic_response(response)

    def stream_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: Callable[[str], None] | None = None,
    ) -> AgentMessage:
        with self.client.messages.stream(
            **self._request_kwargs(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
            )
        ) as stream:
            for event in stream:
                if getattr(event, "type", None) != "content_block_delta":
                    continue
                delta = getattr(event, "delta", None)
                if getattr(delta, "type", None) != "text_delta":
                    continue
                text = getattr(delta, "text", "")
                if text and on_text_delta:
                    on_text_delta(text)
            response = stream.get_final_message()
        return _from_anthropic_response(response)


def _from_anthropic_response(response) -> AgentMessage:
    blocks: list[TextBlock | ToolUseBlock] = []
    for block in response.content:
        if block.type == "text":
            blocks.append(TextBlock(text=block.text))
        elif block.type == "tool_use":
            blocks.append(ToolUseBlock(id=block.id, name=block.name, input=block.input))
    return AgentMessage(
        content=blocks,
        stop_reason=response.stop_reason,
        usage=response.usage,
    )


class OpenAICompatibleModelClient:
    def __init__(self, *, api_key: str, base_url: str) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def _request_kwargs(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}] + _to_openai_messages(messages),
        }
        if tools:
            kwargs["tools"] = [_to_openai_tool(tool) for tool in tools]
        return kwargs

    def create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentMessage:
        response = self.client.chat.completions.create(
            **self._request_kwargs(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
            )
        )
        choice = response.choices[0]
        message = choice.message
        blocks: list[TextBlock | ToolUseBlock] = []
        if message.content:
            blocks.append(TextBlock(text=message.content))
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            try:
                tool_input = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                tool_input = {"_raw_arguments": call.function.arguments}
            blocks.append(ToolUseBlock(id=call.id, name=call.function.name, input=tool_input))
        return AgentMessage(
            content=blocks,
            stop_reason="tool_use" if tool_calls else (choice.finish_reason or "stop"),
            usage=response.usage,
        )

    def stream_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_delta: Callable[[str], None] | None = None,
    ) -> AgentMessage:
        stream = self.client.chat.completions.create(
            **self._request_kwargs(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
            ),
            stream=True,
        )

        text_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage = None

        for chunk in stream:
            usage = getattr(chunk, "usage", None) or usage
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue

            choice = choices[0]
            finish_reason = getattr(choice, "finish_reason", None) or finish_reason
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
                if on_text_delta:
                    on_text_delta(content)

            for call in getattr(delta, "tool_calls", None) or []:
                index = getattr(call, "index", None)
                if index is None:
                    index = len(tool_calls)
                state = tool_calls.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                if getattr(call, "id", None):
                    state["id"] = call.id
                function = getattr(call, "function", None)
                if function is None:
                    continue
                if getattr(function, "name", None):
                    state["name"] += function.name
                if getattr(function, "arguments", None):
                    state["arguments"] += function.arguments

        blocks: list[TextBlock | ToolUseBlock] = []
        text = "".join(text_parts)
        if text:
            blocks.append(TextBlock(text=text))

        for index in sorted(tool_calls):
            state = tool_calls[index]
            try:
                tool_input = json.loads(state["arguments"] or "{}")
            except json.JSONDecodeError:
                tool_input = {"_raw_arguments": state["arguments"]}
            if state["name"]:
                blocks.append(
                    ToolUseBlock(
                        id=state["id"] or f"call_{index}",
                        name=state["name"],
                        input=tool_input,
                    )
                )

        return AgentMessage(
            content=blocks,
            stop_reason="tool_use" if tool_calls else (finish_reason or "stop"),
            usage=usage,
        )


def _to_anthropic_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message["content"]
    if isinstance(content, str):
        return message
    converted = []
    for block in content:
        if isinstance(block, TextBlock):
            converted.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            converted.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            )
        else:
            converted.append(block)
    return {"role": message["role"], "content": converted}


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        content = message["content"]
        if isinstance(content, str):
            converted.append({"role": role, "content": content})
            continue

        if role == "assistant":
            text = "\n".join(block.text for block in content if isinstance(block, TextBlock))
            tool_calls = []
            for block in content:
                if isinstance(block, ToolUseBlock):
                    tool_calls.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(block.input, ensure_ascii=False),
                            },
                        }
                    )
            item = {"role": "assistant", "content": text or None}
            if tool_calls:
                item["tool_calls"] = tool_calls
            converted.append(item)
            continue

        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                converted.append(
                    {
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": block.get("content", ""),
                    }
                )
    return converted


def _to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }
