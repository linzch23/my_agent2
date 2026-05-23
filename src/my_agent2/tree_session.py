from __future__ import annotations

"""Pi-compatible append-only tree sessions.

Inspired by the Pi coding-agent session model:
https://github.com/earendil-works/pi/tree/main/packages/coding-agent

Pi is MIT licensed:
MIT License, Copyright (c) 2025 Mario Zechner.

This module is an original Python adaptation for my_agent2. It ports the
minimal tree-session concepts needed here: JSONL source-of-truth storage,
id/parentId entries, active leaf context construction, labels, branch summaries,
compaction entries, and a small Context Ladder extension seam. It does not port
Pi's TUI or complete agent runtime.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Protocol
from uuid import uuid4


SESSION_VERSION = 1


class ContextLayer(IntEnum):
    L0_METADATA = 0
    L1_SUMMARY = 1
    L2_SELECTED_MESSAGES = 2
    L3_TOOL_EVIDENCE = 3
    L4_RAW_FILE_OR_LOG = 4


EntryType = Literal[
    "session_info",
    "session_state",
    "message",
    "tool_call",
    "tool_result",
    "branch_summary",
    "compaction",
    "label",
    "context_layer",
    "raw",
    "custom",
]


@dataclass(kw_only=True)
class SessionEntry:
    type: EntryType
    id: str
    sessionId: str
    parentId: str | None
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class SessionInfoEntry(SessionEntry):
    version: int = SESSION_VERSION
    rootId: str | None = None
    activeLeafId: str | None = None
    title: str | None = None
    createdAt: str = ""
    updatedAt: str = ""
    type: Literal["session_info"] = "session_info"


@dataclass(kw_only=True)
class SessionStateEntry(SessionEntry):
    activeLeafId: str | None = None
    reason: Literal["jump", "append", "resume", "fork", "clone"] = "append"
    type: Literal["session_state"] = "session_state"


@dataclass(kw_only=True)
class MessageEntry(SessionEntry):
    message: dict[str, Any]
    type: Literal["message"] = "message"


@dataclass(kw_only=True)
class ToolCallEntry(SessionEntry):
    toolCall: dict[str, Any]
    type: Literal["tool_call"] = "tool_call"


@dataclass(kw_only=True)
class ToolResultEntry(SessionEntry):
    toolResult: dict[str, Any]
    type: Literal["tool_result"] = "tool_result"


@dataclass(kw_only=True)
class BranchSummaryEntry(SessionEntry):
    fromLeafId: str = ""
    targetEntryId: str = ""
    commonAncestorId: str | None = None
    summarizedEntryIds: list[str] = field(default_factory=list)
    summary: str = ""
    type: Literal["branch_summary"] = "branch_summary"


@dataclass(kw_only=True)
class CompactionEntry(SessionEntry):
    summary: str = ""
    compactedEntryIds: list[str] = field(default_factory=list)
    firstKeptEntryId: str = ""
    tokenEstimateBefore: int = 0
    tokenEstimateAfter: int = 0
    type: Literal["compaction"] = "compaction"


@dataclass(kw_only=True)
class LabelEntry(SessionEntry):
    targetId: str = ""
    label: str | None = None
    type: Literal["label"] = "label"


@dataclass(kw_only=True)
class ContextLayerEntry(SessionEntry):
    targetId: str = ""
    contextLayer: str = ContextLayer.L2_SELECTED_MESSAGES.name
    type: Literal["context_layer"] = "context_layer"


@dataclass(kw_only=True)
class RawEntry(SessionEntry):
    rawRef: str = ""
    summaryRef: str | None = None
    type: Literal["raw"] = "raw"


@dataclass(kw_only=True)
class CustomEntry(SessionEntry):
    customType: str = ""
    data: dict[str, Any] | None = None
    type: Literal["custom"] = "custom"


@dataclass
class TreeSession:
    id: str
    filePath: Path
    entries: list[SessionEntry] = field(default_factory=list)
    entriesById: dict[str, SessionEntry] = field(default_factory=dict)
    childrenByParent: dict[str | None, list[str]] = field(default_factory=dict)
    labels: dict[str, str | None] = field(default_factory=dict)
    activeLeafId: str | None = None
    rootId: str | None = None
    title: str | None = None
    createdAt: str = ""
    updatedAt: str = ""


class SummarizerProtocol(Protocol):
    def summarize(
        self,
        messages: list[dict[str, Any]],
        *,
        summary_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        ...


class BaseSummarizer:
    def summarize(
        self,
        messages: list[dict[str, Any]],
        *,
        summary_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        raise NotImplementedError


class FakeSummarizer(BaseSummarizer):
    def summarize(
        self,
        messages: list[dict[str, Any]],
        *,
        summary_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        content = " || ".join(str(message.get("content", message)) for message in messages)
        return f"{summary_type.upper()} SUMMARY: {content}"


class LlmSummarizer(BaseSummarizer):
    def __init__(self, client: Any, model: str, *, max_tokens: int = 1200) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def summarize(
        self,
        messages: list[dict[str, Any]],
        *,
        summary_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        transcript = "\n\n".join(
            f"{message.get('role', 'unknown').upper()}:\n{message.get('content', message)}"
            for message in messages
        )
        prompt = (
            f"Create a concise {summary_type} for an agent tree session.\n"
            "Preserve goals, constraints, decisions, files, tool evidence, open tasks, and risks.\n\n"
            f"{transcript}"
        )
        response = self.client.create_message(
            model=self.model,
            max_tokens=self.max_tokens,
            system="You summarize agent session branches for later context recovery.",
            messages=[{"role": "user", "content": prompt}],
            tools=[],
        )
        return "\n".join(getattr(block, "text", "") for block in response.content).strip()


class TokenEstimator(Protocol):
    def estimate_entry(self, entry: SessionEntry) -> int:
        ...

    def estimate_message(self, message: dict[str, Any]) -> int:
        ...


class SimpleTokenEstimator:
    def estimate_entry(self, entry: SessionEntry) -> int:
        return max(1, len(json.dumps(_to_json(entry), ensure_ascii=False)) // 4)

    def estimate_message(self, message: dict[str, Any]) -> int:
        return max(1, len(json.dumps(_json_safe(message), ensure_ascii=False)) // 4)


class JsonlSessionStorage:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def getSessionFilePath(self, session_id: str) -> Path:
        return self.session_dir / f"{session_id}.jsonl"

    def listSessions(self) -> list[str]:
        return sorted(path.stem for path in self.session_dir.glob("*.jsonl"))

    def append_line(self, session_id: str, entry: SessionEntry) -> None:
        path = self.getSessionFilePath(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_to_json(entry), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read_lines(self, session_id: str) -> list[dict[str, Any]]:
        path = self.getSessionFilePath(session_id)
        if not path.exists():
            raise FileNotFoundError(path)
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
        return rows

    def deleteSession(self, session_id: str) -> None:
        self.getSessionFilePath(session_id).unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _new_id(existing: Iterable[str] = ()) -> str:
    used = set(existing)
    for _ in range(100):
        value = uuid4().hex[:8]
        if value not in used:
            return value
    return uuid4().hex


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return _json_safe(asdict(value))
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if hasattr(value, "__dict__"):
        return _json_safe({k: v for k, v in vars(value).items() if not k.startswith("_")})
    return str(value)


def _to_json(entry: SessionEntry) -> dict[str, Any]:
    data = _json_safe(asdict(entry))
    data["type"] = entry.type
    return data


def _layer_name(layer: ContextLayer | str | None) -> str:
    if isinstance(layer, ContextLayer):
        return layer.name
    if isinstance(layer, str):
        return layer
    return ContextLayer.L2_SELECTED_MESSAGES.name


def _layer_value(layer: ContextLayer | str | None) -> int:
    if isinstance(layer, ContextLayer):
        return int(layer)
    if isinstance(layer, str):
        try:
            return int(ContextLayer[layer])
        except KeyError:
            return int(ContextLayer.L2_SELECTED_MESSAGES)
    return int(ContextLayer.L2_SELECTED_MESSAGES)


class TreeSessionManager:
    def __init__(
        self,
        *,
        session_dir: Path | None = None,
        cwd: str = "",
        summarizer: SummarizerProtocol | Callable[..., str] | None = None,
        token_estimator: TokenEstimator | None = None,
        compact_keep_messages: int = 4,
    ) -> None:
        self.storage = JsonlSessionStorage(session_dir or Path(".sessions"))
        self.sessions: dict[str, TreeSession] = {}
        self.compact_keep_messages = compact_keep_messages
        self.summarizer = self._coerce_summarizer(summarizer)
        self.token_estimator = token_estimator or SimpleTokenEstimator()
        if not self.storage.listSessions():
            self.createSession("default", cwd=cwd)
        else:
            first = self.storage.listSessions()[0]
            self.loadSession(first)

    def createSession(self, session_id: str | None = None, *, cwd: str = "", title: str | None = None) -> str:
        session_id = session_id or uuid4().hex
        if self.storage.getSessionFilePath(session_id).exists():
            raise FileExistsError(self.storage.getSessionFilePath(session_id))
        now = _now()
        info = SessionInfoEntry(
            id=_new_id(),
            sessionId=session_id,
            parentId=None,
            timestamp=now,
            version=SESSION_VERSION,
            rootId=None,
            activeLeafId=None,
            title=title,
            createdAt=now,
            updatedAt=now,
            metadata={"contextLayer": ContextLayer.L0_METADATA.name},
        )
        self.storage.append_line(session_id, info)
        session = TreeSession(
            id=session_id,
            filePath=self.storage.getSessionFilePath(session_id),
            title=title,
            createdAt=now,
            updatedAt=now,
        )
        self.sessions[session_id] = session
        self._apply_entry(session, info)
        return session_id

    def loadSession(self, session_id: str) -> TreeSession:
        rows = self.storage.read_lines(session_id)
        if not rows or rows[0].get("type") != "session_info":
            raise ValueError("session file first line must be session_info")
        session = TreeSession(id=session_id, filePath=self.storage.getSessionFilePath(session_id))
        for row in rows:
            self._apply_entry(session, self._entry_from_dict(row))
        if session.activeLeafId is None:
            context_entries = [entry for entry in session.entries if self._is_tree_entry(entry)]
            session.activeLeafId = context_entries[-1].id if context_entries else None
        self.sessions[session_id] = session
        return session

    def resumeSession(self, session_id: str) -> TreeSession:
        session = self.loadSession(session_id)
        self._append_state(session_id, session.activeLeafId, "resume")
        return session

    def saveSessionMetadata(self, session_id: str, *, title: str | None = None) -> str:
        session = self._session(session_id)
        now = _now()
        entry = SessionInfoEntry(
            id=_new_id(session.entriesById),
            sessionId=session_id,
            parentId=None,
            timestamp=now,
            version=SESSION_VERSION,
            rootId=session.rootId,
            activeLeafId=session.activeLeafId,
            title=title if title is not None else session.title,
            createdAt=session.createdAt or now,
            updatedAt=now,
            metadata={"contextLayer": ContextLayer.L0_METADATA.name},
        )
        self.storage.append_line(session_id, entry)
        self._apply_entry(session, entry)
        return entry.id

    def listSessions(self) -> list[str]:
        return self.storage.listSessions()

    def deleteSession(self, session_id: str) -> None:
        self.storage.deleteSession(session_id)
        self.sessions.pop(session_id, None)

    def getSessionFilePath(self, session_id: str) -> Path:
        return self.storage.getSessionFilePath(session_id)

    def append_entry(
        self,
        session_id: str,
        parent_id: str | None,
        entry: SessionEntry | dict[str, Any],
    ) -> str:
        session = self._session(session_id)
        if parent_id is not None and parent_id not in session.entriesById:
            raise KeyError(f"parent entry not found: {parent_id}")
        normalized = self._normalize_entry(session, parent_id, entry)
        self.storage.append_line(session_id, normalized)
        self._apply_entry(session, normalized)
        if self._is_tree_entry(normalized):
            if session.rootId is None and normalized.parentId is None:
                self.saveSessionMetadata(session_id, title=session.title)
            self._append_state(session_id, normalized.id, "append")
        return normalized.id

    def append_message(self, session_id: str, message: dict[str, Any], *, parent_id: str | None = None) -> str:
        session = self._session(session_id)
        return self.append_entry(
            session_id,
            session.activeLeafId if parent_id is None else parent_id,
            {
                "type": "message",
                "message": _json_safe(message),
                "metadata": {"contextLayer": ContextLayer.L2_SELECTED_MESSAGES.name},
            },
        )

    def append_tool_call(self, session_id: str, tool_call: dict[str, Any], *, parent_id: str | None = None) -> str:
        session = self._session(session_id)
        return self.append_entry(
            session_id,
            session.activeLeafId if parent_id is None else parent_id,
            {
                "type": "tool_call",
                "toolCall": _json_safe(tool_call),
                "metadata": {"contextLayer": ContextLayer.L0_METADATA.name},
            },
        )

    def append_tool_result(self, session_id: str, tool_result: dict[str, Any], *, parent_id: str | None = None) -> str:
        session = self._session(session_id)
        return self.append_entry(
            session_id,
            session.activeLeafId if parent_id is None else parent_id,
            {
                "type": "tool_result",
                "toolResult": _json_safe(tool_result),
                "metadata": {"contextLayer": ContextLayer.L3_TOOL_EVIDENCE.name},
            },
        )

    def getActiveBranch(self, session_id: str) -> list[SessionEntry]:
        return self.getBranch(session_id, self._session(session_id).activeLeafId)

    def getBranch(self, session_id: str, leaf_id: str | None = None) -> list[SessionEntry]:
        session = self._session(session_id)
        current_id = session.activeLeafId if leaf_id is None else leaf_id
        branch: list[SessionEntry] = []
        while current_id:
            entry = session.entriesById.get(current_id)
            if entry is None:
                break
            if self._is_tree_entry(entry):
                branch.append(entry)
            current_id = entry.parentId
        return list(reversed(branch))

    def buildModelContext(self, session_id: str) -> list[dict[str, Any]]:
        return self._build_context(session_id, max_layer=None)["messages"]

    def buildContextByLadder(self, session_id: str, maxLayer: ContextLayer | str) -> list[dict[str, Any]]:
        return self._build_context(session_id, max_layer=maxLayer)["messages"]

    def jumpToEntry(self, session_id: str, entry_id: str) -> None:
        session = self._session(session_id)
        if entry_id not in session.entriesById:
            raise KeyError(f"entry not found: {entry_id}")
        self._append_state(session_id, entry_id, "jump")

    def forkFromEntry(self, session_id: str, entry_id: str) -> None:
        session = self._session(session_id)
        if entry_id not in session.entriesById:
            raise KeyError(f"entry not found: {entry_id}")
        self._append_state(session_id, entry_id, "fork")

    def cloneActiveBranch(self, session_id: str) -> str:
        source = self._session(session_id)
        clone_id = self.createSession(cwd="", title=(source.title or "clone"))
        id_map: dict[str, str] = {}
        parent_id: str | None = None
        for entry in self.getActiveBranch(session_id):
            cloned = _to_json(entry)
            old_id = cloned["id"]
            cloned["id"] = _new_id(self._session(clone_id).entriesById)
            cloned["sessionId"] = clone_id
            cloned["parentId"] = parent_id
            if cloned["type"] == "label" and cloned.get("targetId") in id_map:
                cloned["targetId"] = id_map[cloned["targetId"]]
            if cloned["type"] == "branch_summary":
                if cloned.get("fromLeafId") in id_map:
                    cloned["fromLeafId"] = id_map[cloned["fromLeafId"]]
                if cloned.get("targetEntryId") in id_map:
                    cloned["targetEntryId"] = id_map[cloned["targetEntryId"]]
                if cloned.get("commonAncestorId") in id_map:
                    cloned["commonAncestorId"] = id_map[cloned["commonAncestorId"]]
                cloned["summarizedEntryIds"] = [
                    id_map[item] for item in cloned.get("summarizedEntryIds", []) if item in id_map
                ]
            if cloned["type"] == "compaction":
                cloned["compactedEntryIds"] = [
                    id_map[item] for item in cloned.get("compactedEntryIds", []) if item in id_map
                ]
                if cloned.get("firstKeptEntryId") in id_map:
                    cloned["firstKeptEntryId"] = id_map[cloned["firstKeptEntryId"]]
            new_id = self.append_entry(clone_id, parent_id, cloned)
            id_map[old_id] = new_id
            parent_id = new_id
        self._append_state(clone_id, parent_id, "clone")
        return clone_id

    def addLabel(self, session_id: str, entry_id: str, label: str | None) -> str:
        session = self._session(session_id)
        if entry_id not in session.entriesById:
            raise KeyError(f"entry not found: {entry_id}")
        return self.append_entry(
            session_id,
            session.activeLeafId,
            {
                "type": "label",
                "targetId": entry_id,
                "label": label,
                "metadata": {"contextLayer": ContextLayer.L0_METADATA.name},
            },
        )

    def get_label(self, session_id: str, entry_id: str) -> str | None:
        return self._session(session_id).labels.get(entry_id)

    def createBranchSummary(
        self,
        session_id: str,
        oldLeafId: str | None,
        targetId: str,
        summarizer: SummarizerProtocol | Callable[..., str] | None = None,
    ) -> str:
        session = self._session(session_id)
        if targetId not in session.entriesById:
            raise KeyError(f"entry not found: {targetId}")
        summary_entries, common = self._collect_branch_summary_entries(session_id, oldLeafId, targetId)
        messages = self._entries_to_context(summary_entries, max_layer=ContextLayer.L3_TOOL_EVIDENCE)
        summary = self._coerce_summarizer(summarizer).summarize(
            messages,
            summary_type="branch_summary",
            metadata={"oldLeafId": oldLeafId, "targetId": targetId, "commonAncestorId": common},
        )
        entry_id = self.append_entry(
            session_id,
            targetId,
            {
                "type": "branch_summary",
                "fromLeafId": oldLeafId or "",
                "targetEntryId": targetId,
                "commonAncestorId": common,
                "summarizedEntryIds": [entry.id for entry in summary_entries],
                "summary": summary,
                "metadata": {"contextLayer": ContextLayer.L1_SUMMARY.name},
            },
        )
        self._append_state(session_id, entry_id, "jump")
        return entry_id

    def compactActiveBranch(
        self,
        session_id: str,
        maxContextTokens: int = 64_000,
        keepRecentTokens: int = 20_000,
        summarizer: SummarizerProtocol | Callable[..., str] | None = None,
    ) -> str | None:
        branch = self.getActiveBranch(session_id)
        contextable = [entry for entry in branch if self._entry_enters_context(entry)]
        token_before = sum(self.token_estimator.estimate_entry(entry) for entry in contextable)
        if token_before <= maxContextTokens and len(contextable) <= self.compact_keep_messages:
            return None

        kept: list[SessionEntry] = []
        kept_tokens = 0
        for entry in reversed(contextable):
            kept.append(entry)
            kept_tokens += self.token_estimator.estimate_entry(entry)
            if kept_tokens >= keepRecentTokens:
                break
        kept = list(reversed(kept))
        if not kept:
            kept = contextable[-self.compact_keep_messages :]
        first_kept = kept[0]
        kept_ids = {entry.id for entry in kept}
        compacted = [entry for entry in contextable if entry.id not in kept_ids]
        if not compacted:
            return None

        messages = self._entries_to_context(compacted, max_layer=ContextLayer.L3_TOOL_EVIDENCE)
        summary = self._coerce_summarizer(summarizer).summarize(
            messages,
            summary_type="compaction",
            metadata={"maxContextTokens": maxContextTokens, "keepRecentTokens": keepRecentTokens},
        )
        # TODO: implement Pi-style split-turn compaction so tool-result/tool-call boundaries
        # are never separated when a single turn is larger than keepRecentTokens.
        token_after = self.token_estimator.estimate_message(
            {"role": "user", "content": summary}
        ) + sum(self.token_estimator.estimate_entry(entry) for entry in kept)
        return self.append_entry(
            session_id,
            self._session(session_id).activeLeafId,
            {
                "type": "compaction",
                "summary": summary,
                "compactedEntryIds": [entry.id for entry in compacted],
                "firstKeptEntryId": first_kept.id,
                "tokenEstimateBefore": token_before,
                "tokenEstimateAfter": token_after,
                "metadata": {"contextLayer": ContextLayer.L1_SUMMARY.name},
            },
        )

    def getContextLayer(self, session_id: str, entry_id: str) -> ContextLayer:
        entry = self._session(session_id).entriesById[entry_id]
        return ContextLayer[entry.metadata.get("contextLayer", self._default_layer(entry).name)]

    def setContextLayer(self, session_id: str, entry_id: str, layer: ContextLayer | str) -> str:
        session = self._session(session_id)
        if entry_id not in session.entriesById:
            raise KeyError(f"entry not found: {entry_id}")
        return self.append_entry(
            session_id,
            session.activeLeafId,
            {
                "type": "context_layer",
                "targetId": entry_id,
                "contextLayer": _layer_name(layer),
                "metadata": {"contextLayer": ContextLayer.L0_METADATA.name},
            },
        )

    def resolveRawRef(self, rawRef: str) -> str:
        path = Path(rawRef)
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
        return rawRef

    def debugBuildModelContext(self, session_id: str) -> dict[str, Any]:
        result = self._build_context(session_id, max_layer=None, debug=True)
        return result["debug"]

    def render_tree(self, session_id: str, *, filter_mode: str = "default") -> str:
        session = self._session(session_id)
        lines: list[str] = []
        roots = [entry.id for entry in session.entries if self._is_tree_entry(entry) and entry.parentId is None]
        for root_id in roots:
            self._render_node(session, root_id, lines, "", True, filter_mode)
        return "\n".join(lines) if lines else "(empty tree)"

    appendEntry = append_entry
    appendMessage = append_message
    loadSession = loadSession
    createSession = createSession
    saveSessionMetadata = saveSessionMetadata
    listSessions = listSessions
    resumeSession = resumeSession
    deleteSession = deleteSession
    getSessionFilePath = getSessionFilePath
    getActiveBranch = getActiveBranch
    buildModelContext = buildModelContext
    jumpToEntry = jumpToEntry
    forkFromEntry = forkFromEntry
    cloneActiveBranch = cloneActiveBranch
    add_label = addLabel
    createBranchSummary = createBranchSummary
    compactActiveBranch = compactActiveBranch
    build_context_by_ladder = buildContextByLadder
    debug_build_model_context = debugBuildModelContext

    def _session(self, session_id: str) -> TreeSession:
        if session_id not in self.sessions:
            return self.loadSession(session_id)
        return self.sessions[session_id]

    def _append_state(
        self,
        session_id: str,
        active_leaf_id: str | None,
        reason: Literal["jump", "append", "resume", "fork", "clone"],
    ) -> str:
        session = self._session(session_id)
        entry = SessionStateEntry(
            id=_new_id(session.entriesById),
            sessionId=session_id,
            parentId=None,
            timestamp=_now(),
            activeLeafId=active_leaf_id,
            reason=reason,
            metadata={"contextLayer": ContextLayer.L0_METADATA.name},
        )
        self.storage.append_line(session_id, entry)
        self._apply_entry(session, entry)
        return entry.id

    def _apply_entry(self, session: TreeSession, entry: SessionEntry) -> None:
        session.entries.append(entry)
        session.entriesById[entry.id] = entry
        if self._is_tree_entry(entry):
            session.childrenByParent.setdefault(entry.parentId, []).append(entry.id)
            if entry.parentId is None and session.rootId is None:
                session.rootId = entry.id
        if isinstance(entry, SessionInfoEntry):
            session.title = entry.title if entry.title is not None else session.title
            session.createdAt = entry.createdAt or session.createdAt
            session.updatedAt = entry.updatedAt or session.updatedAt
            session.rootId = entry.rootId or session.rootId
            session.activeLeafId = entry.activeLeafId or session.activeLeafId
        elif isinstance(entry, SessionStateEntry):
            session.activeLeafId = entry.activeLeafId
        elif isinstance(entry, LabelEntry):
            session.labels[entry.targetId] = entry.label
        elif isinstance(entry, ContextLayerEntry):
            target = session.entriesById.get(entry.targetId)
            if target is not None:
                target.metadata["contextLayer"] = entry.contextLayer

    def _normalize_entry(
        self,
        session: TreeSession,
        parent_id: str | None,
        entry: SessionEntry | dict[str, Any],
    ) -> SessionEntry:
        if isinstance(entry, SessionEntry):
            data = _to_json(entry)
        else:
            data = dict(entry)
        data.setdefault("id", _new_id(session.entriesById))
        data.setdefault("sessionId", session.id)
        data["parentId"] = parent_id
        data.setdefault("timestamp", _now())
        data.setdefault("metadata", {})
        return self._entry_from_dict(data)

    def _entry_from_dict(self, data: dict[str, Any]) -> SessionEntry:
        entry_type = data["type"]
        common = {
            "id": data["id"],
            "sessionId": data["sessionId"],
            "parentId": data.get("parentId"),
            "timestamp": data["timestamp"],
            "metadata": dict(data.get("metadata") or {}),
        }
        if entry_type == "session_info":
            return SessionInfoEntry(
                **common,
                version=int(data.get("version", SESSION_VERSION)),
                rootId=data.get("rootId"),
                activeLeafId=data.get("activeLeafId"),
                title=data.get("title"),
                createdAt=data.get("createdAt", data["timestamp"]),
                updatedAt=data.get("updatedAt", data["timestamp"]),
            )
        if entry_type == "session_state":
            return SessionStateEntry(**common, activeLeafId=data.get("activeLeafId"), reason=data.get("reason", "append"))
        if entry_type == "message":
            return MessageEntry(**common, message=data["message"])
        if entry_type == "tool_call":
            return ToolCallEntry(**common, toolCall=data["toolCall"])
        if entry_type == "tool_result":
            return ToolResultEntry(**common, toolResult=data["toolResult"])
        if entry_type == "branch_summary":
            return BranchSummaryEntry(
                **common,
                fromLeafId=data.get("fromLeafId") or data.get("fromId", ""),
                targetEntryId=data.get("targetEntryId", ""),
                commonAncestorId=data.get("commonAncestorId"),
                summarizedEntryIds=list(data.get("summarizedEntryIds") or []),
                summary=data.get("summary", ""),
            )
        if entry_type == "compaction":
            return CompactionEntry(
                **common,
                summary=data.get("summary", ""),
                compactedEntryIds=list(data.get("compactedEntryIds") or []),
                firstKeptEntryId=data.get("firstKeptEntryId", ""),
                tokenEstimateBefore=int(data.get("tokenEstimateBefore", data.get("tokensBefore", 0))),
                tokenEstimateAfter=int(data.get("tokenEstimateAfter", 0)),
            )
        if entry_type == "label":
            return LabelEntry(**common, targetId=data["targetId"], label=data.get("label"))
        if entry_type == "context_layer":
            return ContextLayerEntry(**common, targetId=data["targetId"], contextLayer=data["contextLayer"])
        if entry_type == "raw":
            return RawEntry(**common, rawRef=data["rawRef"], summaryRef=data.get("summaryRef"))
        if entry_type == "custom":
            return CustomEntry(**common, customType=data.get("customType", ""), data=data.get("data"))
        raise ValueError(f"unsupported entry type: {entry_type!r}")

    def _is_tree_entry(self, entry: SessionEntry) -> bool:
        return not isinstance(entry, (SessionInfoEntry, SessionStateEntry, ContextLayerEntry))

    def _entry_enters_context(self, entry: SessionEntry) -> bool:
        if isinstance(entry, (MessageEntry, ToolResultEntry, BranchSummaryEntry, CompactionEntry)):
            return self.getContextLayer(entry.sessionId, entry.id) != ContextLayer.L4_RAW_FILE_OR_LOG
        return False

    def _default_layer(self, entry: SessionEntry) -> ContextLayer:
        if isinstance(entry, (SessionInfoEntry, SessionStateEntry, LabelEntry, CustomEntry, ContextLayerEntry, ToolCallEntry)):
            return ContextLayer.L0_METADATA
        if isinstance(entry, (BranchSummaryEntry, CompactionEntry)):
            return ContextLayer.L1_SUMMARY
        if isinstance(entry, MessageEntry):
            return ContextLayer.L2_SELECTED_MESSAGES
        if isinstance(entry, ToolResultEntry):
            return ContextLayer.L3_TOOL_EVIDENCE
        if isinstance(entry, RawEntry):
            return ContextLayer.L4_RAW_FILE_OR_LOG
        return ContextLayer.L2_SELECTED_MESSAGES

    def _build_context(
        self,
        session_id: str,
        *,
        max_layer: ContextLayer | str | None,
        debug: bool = False,
    ) -> dict[str, Any]:
        session = self._session(session_id)
        branch = self.getActiveBranch(session_id)
        active_ids = [entry.id for entry in branch]
        latest_compaction = next((entry for entry in reversed(branch) if isinstance(entry, CompactionEntry)), None)
        compacted_ids = set(latest_compaction.compactedEntryIds if latest_compaction else [])

        entries_for_context: list[SessionEntry] = []
        if latest_compaction:
            entries_for_context.append(latest_compaction)
            found_first_kept = False
            compaction_index = branch.index(latest_compaction)
            for entry in branch[:compaction_index]:
                if entry.id == latest_compaction.firstKeptEntryId:
                    found_first_kept = True
                if found_first_kept and entry.id not in compacted_ids:
                    entries_for_context.append(entry)
            entries_for_context.extend(branch[compaction_index + 1 :])
        else:
            entries_for_context = branch

        messages = self._entries_to_context(entries_for_context, max_layer=max_layer)
        included_ids = [entry.id for entry in entries_for_context if self._context_message_for_entry(entry, max_layer) is not None]
        included_set = set(included_ids)
        sibling_ids = self._sibling_branch_ids(session, set(active_ids))
        excluded: dict[str, str] = {}
        for entry in session.entries:
            if entry.id in included_set:
                continue
            if entry.id in compacted_ids:
                excluded[entry.id] = "compacted"
            elif entry.id in sibling_ids:
                excluded[entry.id] = "sibling_branch"
            elif isinstance(entry, (LabelEntry, CustomEntry, SessionInfoEntry, SessionStateEntry, ContextLayerEntry, ToolCallEntry)):
                excluded[entry.id] = f"{entry.type}_not_in_context"
            elif self.getContextLayer(session_id, entry.id) == ContextLayer.L4_RAW_FILE_OR_LOG:
                excluded[entry.id] = "l4_raw_not_in_context"
            elif max_layer is not None and _layer_value(self.getContextLayer(session_id, entry.id)) > _layer_value(max_layer):
                excluded[entry.id] = "above_context_ladder_layer"
            elif entry.id not in active_ids:
                excluded[entry.id] = "not_on_active_path"
        debug_payload = {
            "activeLeafId": session.activeLeafId,
            "activePathEntryIds": active_ids,
            "includedEntryIds": included_ids,
            "excludedEntryIds": list(excluded.keys()),
            "excludedReason": excluded,
            "siblingBranchEntryIds": sorted(sibling_ids),
            "compactionApplied": latest_compaction is not None,
            "branchSummaryApplied": any(isinstance(entry, BranchSummaryEntry) for entry in entries_for_context),
            "estimatedTokens": sum(self.token_estimator.estimate_message(message) for message in messages),
            "contextLayers": {entry.id: self.getContextLayer(session_id, entry.id).name for entry in session.entries},
        }
        return {"messages": messages, "debug": debug_payload}

    def _entries_to_context(
        self,
        entries: list[SessionEntry],
        *,
        max_layer: ContextLayer | str | None,
    ) -> list[dict[str, Any]]:
        messages = []
        for entry in entries:
            message = self._context_message_for_entry(entry, max_layer)
            if message is not None:
                messages.append(message)
        return messages

    def _context_message_for_entry(
        self,
        entry: SessionEntry,
        max_layer: ContextLayer | str | None,
    ) -> dict[str, Any] | None:
        layer = self.getContextLayer(entry.sessionId, entry.id)
        if layer == ContextLayer.L4_RAW_FILE_OR_LOG:
            return None
        if max_layer is not None and _layer_value(layer) > _layer_value(max_layer):
            return None
        if isinstance(entry, MessageEntry):
            return entry.message
        if isinstance(entry, ToolResultEntry):
            return {"role": "user", "content": [entry.toolResult]}
        if isinstance(entry, BranchSummaryEntry):
            return {"role": "user", "content": f"Branch summary from {entry.fromLeafId}:\n{entry.summary}"}
        if isinstance(entry, CompactionEntry):
            return {
                "role": "user",
                "content": (
                    f"Compaction summary ({entry.tokenEstimateBefore} tokens before):\n"
                    f"{entry.summary}"
                ),
            }
        return None

    def _collect_branch_summary_entries(
        self,
        session_id: str,
        old_leaf_id: str | None,
        target_id: str,
    ) -> tuple[list[SessionEntry], str | None]:
        if old_leaf_id is None:
            return [], None
        old_path = self.getBranch(session_id, old_leaf_id)
        target_path = self.getBranch(session_id, target_id)
        old_ids = {entry.id for entry in old_path}
        common = None
        for entry in reversed(target_path):
            if entry.id in old_ids:
                common = entry.id
                break
        collected = []
        by_id = self._session(session_id).entriesById
        current = by_id.get(old_leaf_id)
        while current and current.id != common:
            if self._entry_enters_context(current):
                collected.append(current)
            current = by_id.get(current.parentId) if current.parentId else None
        return list(reversed(collected)), common

    def _sibling_branch_ids(self, session: TreeSession, active_ids: set[str]) -> set[str]:
        sibling_ids = set()
        for entry in session.entries:
            if self._is_tree_entry(entry) and entry.id not in active_ids:
                sibling_ids.add(entry.id)
        return sibling_ids

    def _render_node(
        self,
        session: TreeSession,
        entry_id: str,
        lines: list[str],
        prefix: str,
        is_last: bool,
        filter_mode: str,
    ) -> None:
        entry = session.entriesById[entry_id]
        children = session.childrenByParent.get(entry_id, [])
        visible = self._visible_in_tree(session, entry, filter_mode)
        connector = "└─ " if is_last else "├─ "
        if visible:
            active = " ← active" if entry.id == session.activeLeafId else ""
            label = f" [{session.labels[entry.id]}]" if session.labels.get(entry.id) else ""
            lines.append(f"{prefix}{connector}{entry.id} {entry.type}: {self._entry_preview(entry)}{label}{active}")
            child_prefix = prefix + ("   " if is_last else "│  ")
        else:
            child_prefix = prefix
        for index, child_id in enumerate(children):
            self._render_node(session, child_id, lines, child_prefix, index == len(children) - 1, filter_mode)

    def _visible_in_tree(self, session: TreeSession, entry: SessionEntry, filter_mode: str) -> bool:
        if filter_mode == "all":
            return True
        if filter_mode == "labeled-only":
            return bool(session.labels.get(entry.id))
        if filter_mode == "user-only":
            return isinstance(entry, MessageEntry) and entry.message.get("role") == "user"
        if filter_mode == "no-tools":
            return not isinstance(entry, (ToolCallEntry, ToolResultEntry))
        return not isinstance(entry, (ToolCallEntry,))

    def _entry_preview(self, entry: SessionEntry) -> str:
        if isinstance(entry, MessageEntry):
            return str(entry.message.get("content", ""))[:80]
        if isinstance(entry, ToolCallEntry):
            return str(entry.toolCall.get("name", ""))[:80]
        if isinstance(entry, ToolResultEntry):
            return str(entry.toolResult.get("content", ""))[:80]
        if isinstance(entry, BranchSummaryEntry):
            return entry.summary[:80]
        if isinstance(entry, CompactionEntry):
            return entry.summary[:80]
        return ""

    def _coerce_summarizer(
        self,
        summarizer: SummarizerProtocol | Callable[..., str] | None,
    ) -> SummarizerProtocol:
        if summarizer is None:
            return self.summarizer if hasattr(self, "summarizer") else FakeSummarizer()
        if hasattr(summarizer, "summarize"):
            return summarizer  # type: ignore[return-value]

        class CallableSummarizer(BaseSummarizer):
            def summarize(self, messages, *, summary_type, metadata=None):
                try:
                    return summarizer(messages, summary_type)  # type: ignore[misc]
                except TypeError:
                    return summarizer(messages)  # type: ignore[misc]

        return CallableSummarizer()
