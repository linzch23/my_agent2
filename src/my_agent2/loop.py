from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .compactor import HistoryCompactor
from .context import ContextBuilder
from .memory import MemoryStore, TokenLog
from .model_client import build_model_client
from .runner import AgentRunner
from .skills import SkillsLoader
from .subagents import SubagentRegistry, SubagentSpec
from .team import MessageBus, TeammateManager
from .tools import (
    EditFileTool,
    GlobTool,
    GrepTool,
    LoadSkillTool,
    ReadFileTool,
    RememberTool,
    RunCommandTool,
    TodoStore,
    ToolRegistry,
    UpdateTodosTool,
    WebFetchTool,
    WriteFileTool,
)
from .tools.dispatch import DispatchSubagentTool
from .tools.team import (
    BroadcastTool,
    ListTeammatesTool,
    ReadInboxTool,
    SendMessageTool,
    SpawnTeammateTool,
)


class AgentApp:
    def __init__(self, root: Path | None = None) -> None:
        load_dotenv()
        self.root = root or Path.cwd()
        self.workspace = Path(os.getenv("MY_AGENT_WORKSPACE", str(self.root))).resolve()
        self.provider = os.getenv("MY_AGENT_PROVIDER", "deepseek")
        default_model = "deepseek-chat" if self.provider == "deepseek" else "claude-3-5-sonnet-latest"
        self.model = os.getenv("MY_AGENT_MODEL", default_model)
        self.max_tokens = int(os.getenv("MY_AGENT_MAX_TOKENS", "4096"))
        self.max_context_tokens = int(os.getenv("MY_AGENT_MAX_CONTEXT_TOKENS", "64000"))
        self.compact_threshold = float(os.getenv("MY_AGENT_COMPACT_THRESHOLD", "0.7"))
        self.compact_keep_messages = int(os.getenv("MY_AGENT_COMPACT_KEEP_MESSAGES", "8"))

        self.client = build_model_client(self.provider)
        self.memory = MemoryStore(self.root / "memory", user_file=self.root / "templates" / "USER.md")
        self.tokens = TokenLog(self.root / "memory" / "tokens.jsonl")
        self.skills = SkillsLoader(self.root / "skills")
        self.todos = TodoStore()
        self.team_bus = MessageBus(self.root / ".team" / "inbox")

        self.registry = self._build_registry()
        self.compactor = HistoryCompactor(
            client=self.client,
            model=self.model,
            memory_store=self.memory,
            token_log=self.tokens,
            keep_messages=self.compact_keep_messages,
            max_context_tokens=self.max_context_tokens,
            threshold=self.compact_threshold,
        )
        unarchived = self.memory.load_unarchived_history()
        if len(unarchived) >= 2:
            try:
                self.compactor.compact_startup(unarchived)
            except Exception as exc:
                print(f"[warning] startup compaction failed: {exc}")
        context = ContextBuilder(self.root / "templates", self.skills, self.memory)
        self.system_prompt = context.build(workspace=self.workspace)
        self.runner = AgentRunner(
            client=self.client,
            model=self.model,
            registry=self.registry,
            system_prompt=self.system_prompt,
            max_tokens=self.max_tokens,
            on_usage=self.tokens.record,
            compactor=self.compactor,
        )
        self.history: list[dict[str, Any]] = []

    def _build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(RunCommandTool(self.workspace))
        registry.register(WebFetchTool())
        registry.register(ReadFileTool(self.workspace))
        registry.register(WriteFileTool(self.workspace))
        registry.register(EditFileTool(self.workspace))
        registry.register(GlobTool(self.workspace))
        registry.register(GrepTool(self.workspace))
        registry.register(LoadSkillTool(self.skills))
        registry.register(UpdateTodosTool(self.todos))
        registry.register(RememberTool(self.memory))

        def teammate_tools(sender: str):
            return [
                SendMessageTool(self.team_bus, sender=sender),
                ReadInboxTool(self.team_bus, reader=sender),
            ]

        self.team = TeammateManager(
            team_dir=self.root / ".team",
            bus=self.team_bus,
            client=self.client,
            model=self.model,
            workspace=self.workspace,
            parent_registry=registry,
            teammate_tool_factory=teammate_tools,
            max_tokens=min(self.max_tokens, 3000),
        )
        registry.register(SpawnTeammateTool(self.team))
        registry.register(ListTeammatesTool(self.team))
        registry.register(SendMessageTool(self.team_bus, sender="lead"))
        registry.register(ReadInboxTool(self.team_bus, reader="lead"))
        registry.register(BroadcastTool(self.team_bus, self.team, sender="lead"))

        subagents = SubagentRegistry(self.root / "templates" / "subagents", self.skills)

        def make_runner(spec: SubagentSpec, sub_registry: ToolRegistry) -> AgentRunner:
            return AgentRunner(
                client=self.client,
                model=self.model,
                registry=sub_registry,
                system_prompt=spec.system_prompt,
                max_tokens=min(self.max_tokens, 3000),
                max_turns=spec.max_turns,
                on_usage=self.tokens.record,
            )

        registry.register(
            DispatchSubagentTool(
                parent_registry=registry,
                subagent_registry=subagents,
                runner_factory=make_runner,
            )
        )
        return registry

    def ask(
        self,
        user_input: str,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> str:
        self.history.append({"role": "user", "content": user_input})
        self.memory.append_history("user", user_input)
        reply = self.runner.step(self.history, on_text_delta=on_text_delta)
        self.memory.append_history("assistant", reply)
        return reply

    def compact_now(self) -> bool:
        return self.compactor.compact(self.history)
