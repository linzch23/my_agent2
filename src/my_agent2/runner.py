from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .model_client import TextBlock
from .tools.registry import ToolRegistry


def _text_from_content(content: list[Any]) -> str:
    texts = [block.text for block in content if isinstance(block, TextBlock)]
    return "\n".join(texts).strip()


class AgentRunner:
    def __init__(
        self,
        *,
        client: Any,
        model: str,
        registry: ToolRegistry,
        system_prompt: str,
        max_tokens: int = 4096,
        max_turns: int | None = None,
        on_usage=None,
        compactor=None,
    ) -> None:
        self.client = client
        self.model = model
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.max_turns = max_turns
        self.on_usage = on_usage
        self.compactor = compactor

    def step(
        self,
        history: list[dict[str, Any]],
        on_text_delta: Callable[[str], None] | None = None,
        on_assistant_message: Callable[[list[Any]], None] | None = None,
        on_tool_call: Callable[[Any], None] | None = None,
        on_tool_result: Callable[[dict[str, str]], None] | None = None,
        history_provider: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> str:
        turns = 0
        while True:
            if self.max_turns is not None and turns >= self.max_turns:
                return f"Stopped after reaching max_turns={self.max_turns}."
            turns += 1
            if history_provider is not None:
                history = history_provider()

            request = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": self.system_prompt,
                "messages": history,
                "tools": self.registry.definitions(),
            }
            if on_text_delta:
                message = self.client.stream_message(**request, on_text_delta=on_text_delta)
            else:
                message = self.client.create_message(**request)
            if self.on_usage:
                self.on_usage(self.model, message.usage)

            history.append({"role": "assistant", "content": message.content})
            if on_assistant_message:
                on_assistant_message(message.content)
            if message.stop_reason != "tool_use":
                if self.compactor:
                    self.compactor.maybe_compact(history)
                return _text_from_content(message.content)

            tool_blocks = [block for block in message.content if block.type == "tool_use"]
            for block in tool_blocks:
                if on_tool_call:
                    on_tool_call(block)
            tool_results = self._execute_tool_blocks(tool_blocks)
            for result in tool_results:
                if on_tool_result:
                    on_tool_result(result)
            history.append({"role": "user", "content": tool_results})

    def _execute_tool_blocks(self, tool_blocks: list[Any]) -> list[dict[str, str]]:
        results: dict[str, str] = {}
        index = 0
        while index < len(tool_blocks):
            block = tool_blocks[index]
            tool = self.registry.get(block.name)

            if tool and tool.concurrency_safe:
                group = []
                while index < len(tool_blocks):
                    candidate = tool_blocks[index]
                    candidate_tool = self.registry.get(candidate.name)
                    if not candidate_tool or not candidate_tool.concurrency_safe:
                        break
                    group.append(candidate)
                    index += 1

                if len(group) == 1:
                    item = group[0]
                    results[item.id] = self.registry.execute(item.name, item.input)
                else:
                    print(f"[parallel tools] {', '.join(item.name for item in group)}")
                    with ThreadPoolExecutor(max_workers=len(group)) as pool:
                        outputs = list(
                            pool.map(lambda item: self.registry.execute(item.name, item.input), group)
                        )
                    for item, output in zip(group, outputs):
                        results[item.id] = output
                continue

            results[block.id] = self.registry.execute(block.name, block.input)
            index += 1

        return [
            {"type": "tool_result", "tool_use_id": block.id, "content": results[block.id]}
            for block in tool_blocks
        ]
