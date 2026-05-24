from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - allows stdlib-only unit tests
    def load_dotenv(*args, **kwargs):
        return False

from .compactor import HistoryCompactor
from .context import ContextBuilder
from .memory import MemoryStore, TokenLog
from .mcp_bridge import MCPClientManager
from .model_client import build_model_client
from .runner import AgentRunner
from .skills import SkillsLoader
from .subagents import SubagentRegistry, SubagentSpec
from .team import MessageBus, TeammateManager
from .tree_session import LlmSummarizer, TreeSessionManager
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
        self.startup_compaction = _env_flag("MY_AGENT_STARTUP_COMPACTION", default=False)

        self.client = build_model_client(self.provider)
        self.memory = MemoryStore(self.root / "memory", user_file=self.root / "templates" / "USER.md")
        self.tokens = TokenLog(self.root / "memory" / "tokens.jsonl")
        self.skills = SkillsLoader(self.root / "skills")
        self.todos = TodoStore()
        self.team_bus = MessageBus(self.root / ".team" / "inbox")
        self.mcp = MCPClientManager(self.root / "mcp_servers.json")
        self.tree = TreeSessionManager(
            session_dir=self.root / "sessions",
            cwd=str(self.workspace),
            summarizer=LlmSummarizer(self.client, self.model),
            compact_keep_messages=self.compact_keep_messages,
        )
        self.session_id = os.getenv("MY_AGENT_SESSION_ID") or self.tree.listSessions()[0]
        self.tree.resumeSession(self.session_id)

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
        if self.startup_compaction:
            unarchived = self.memory.load_unarchived_history()
            if len(unarchived) >= 2:
                try:
                    self.compactor.compact_startup(unarchived)
                except Exception as exc:
                    print(f"[warning] startup compaction failed: {exc}")

        # SessionMemoryCommitter: bridges tree compaction → memory OS
        from .session_memory_committer import SessionMemoryCommitter, LlmMemoryExtractor
        self.memory.set_auto_link_client(self.client, self.model)
        self.session_memory_committer = SessionMemoryCommitter(
            tree=self.tree,
            memory_store=self.memory,
            extractor=LlmMemoryExtractor(self.client, self.model),
        )

        context = ContextBuilder(self.root / "templates", self.skills, self.memory)
        self.system_prompt = context.build(workspace=self.workspace)
        self.runner = AgentRunner(
            client=self.client,
            model=self.model,
            registry=self.registry,
            system_prompt=self.system_prompt,
            max_tokens=self.max_tokens,
            on_usage=self.tokens.record,
            compactor=None,
        )
        self.history: list[dict[str, Any]] = self.tree.buildModelContext(self.session_id)

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

        self.mcp.start()
        for tool in self.mcp.tools():
            if registry.get(tool.name) is not None:
                print(f"[warning] skipped MCP tool name collision: {tool.name}")
                continue
            registry.register(tool)
        for status in self.mcp.statuses.values():
            if status.status == "error":
                print(f"[warning] MCP server {status.name} failed: {status.error}")

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
        self.tree.append_message(self.session_id, {"role": "user", "content": user_input})
        self.memory.append_history("user", user_input)
        prompt_history = self.tree.buildModelContext(self.session_id)
        reply = self.runner.step(
            prompt_history,
            on_text_delta=on_text_delta,
            on_assistant_message=lambda content: self.tree.append_message(
                self.session_id,
                {"role": "assistant", "content": content},
            ),
            on_tool_call=lambda block: self.tree.append_tool_call(
                self.session_id,
                {"id": block.id, "name": block.name, "input": block.input},
            ),
            on_tool_result=lambda result: self.tree.append_tool_result(self.session_id, result),
            history_provider=lambda: self.tree.buildModelContext(self.session_id),
        )
        self.memory.append_history("assistant", reply)
        self.history = self.tree.buildModelContext(self.session_id)
        return reply

    def compact_now(self) -> bool:
        compaction_id = self._compact_active_branch()
        if compaction_id:
            try:
                archive_uri = self.session_memory_committer.commit_compaction(
                    self.session_id, compaction_id
                )
                print(f"[memory] session archive: {archive_uri}")
            except Exception as exc:
                print(f"[warning] memory commit failed: {exc}")
            return True
        return False

    def _compact_active_branch(self) -> str | None:
        return self.tree.compactActiveBranch(
            self.session_id,
            maxContextTokens=self.max_context_tokens,
            keepRecentTokens=max(1, self.max_context_tokens // 4),
            summarizer=LlmSummarizer(self.client, self.model),
        )

    def tree_view(self, filter_mode: str = "default") -> str:
        return self.tree.render_tree(self.session_id, filter_mode=filter_mode)

    def jump_to_entry(self, entry_id: str) -> None:
        self.tree.jumpToEntry(self.session_id, entry_id)
        self.history = self.tree.buildModelContext(self.session_id)

    def fork_from_entry(self, entry_id: str) -> None:
        self.tree.forkFromEntry(self.session_id, entry_id)
        self.history = self.tree.buildModelContext(self.session_id)

    def clone_active_branch(self) -> str:
        new_session_id = self.tree.cloneActiveBranch(self.session_id)
        self.session_id = new_session_id
        self.history = self.tree.buildModelContext(self.session_id)
        return new_session_id

    def label_entry(self, entry_id: str, label: str) -> None:
        self.tree.addLabel(self.session_id, entry_id, label)

    def close(self) -> None:
        self.mcp.close()


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
