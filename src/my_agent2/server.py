from __future__ import annotations

import json
import mimetypes
import os
import threading
from dataclasses import asdict, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .loop import AgentApp
from .tree_session import (
    BranchSummaryEntry,
    CompactionEntry,
    MessageEntry,
    ToolCallEntry,
    ToolResultEntry,
)
from .web_adapter import (
    list_session_summaries,
    load_session_records,
    memory_payload,
    records_to_file_changes,
    records_to_node_detail,
    records_to_run_steps,
    records_to_tool_events,
    records_to_tree_nodes,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class WebState:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.frontend_dir = root / "frontend"
        self.app = AgentApp(root=root)
        self.lock = threading.RLock()

    def close(self) -> None:
        self.app.close()


def main() -> None:
    root = Path(os.getenv("MY_AGENT_ROOT", Path.cwd())).resolve()
    host = os.getenv("MY_AGENT_WEB_HOST", DEFAULT_HOST)
    port = int(os.getenv("MY_AGENT_WEB_PORT", str(DEFAULT_PORT)))
    state = WebState(root)
    server = ThreadingHTTPServer((host, port), _handler_factory(state))
    print(f"my_agent2 web 已就绪：http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
        state.close()


def _handler_factory(state: WebState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "my_agent2-web/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if path == "/api/health":
                    self._send_json({"ok": True})
                    return
                if path == "/api/state":
                    filter_mode = query.get("filter", ["default"])[0]
                    with state.lock:
                        self._send_json(_state_payload(state.app, filter_mode=filter_mode))
                    return
                if path == "/api/sessions":
                    with state.lock:
                        self._send_json(_sessions_payload(state.app))
                    return
                session_route = _session_resource_route(path)
                if session_route is not None:
                    session_id, resource, node_id = session_route
                    with state.lock:
                        self._send_json(_session_resource_payload(state, session_id, resource, node_id))
                    return
                if path == "/api/tree":
                    filter_mode = query.get("filter", ["default"])[0]
                    with state.lock:
                        self._send_json(_tree_payload(state.app, filter_mode=filter_mode))
                    return
                if path == "/api/context/debug":
                    with state.lock:
                        self._send_json(state.app.tree.debugBuildModelContext(state.app.session_id))
                    return
                if path == "/api/context":
                    prefix = query.get("prefix", [""])[0]
                    limit = int(query.get("limit", ["200"])[0])
                    with state.lock:
                        self._send_json({"objects": state.app.memory.list_context(prefix=prefix, limit=limit)})
                    return
                if path == "/api/tools":
                    with state.lock:
                        self._send_json({"tools": state.app.registry.definitions()})
                    return
                if path == "/api/memory":
                    with state.lock:
                        self._send_json(memory_payload(state.root / "memory"))
                    return
                if path == "/api/mcp":
                    with state.lock:
                        self._send_json({"report": state.app.mcp.report()})
                    return
                if path == "/api/team":
                    with state.lock:
                        self._send_json({"team": state.app.team.list_all()})
                    return
                self._serve_static(path, state.frontend_dir)
            except Exception as exc:
                self._send_error(exc)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if path == "/api/chat/stream":
                    self._chat_stream()
                    return
                payload = self._read_json()
                with state.lock:
                    if path == "/api/chat":
                        message = str(payload.get("message", "")).strip()
                        if not message:
                            raise ValueError("message is required")
                        reply = state.app.ask(message)
                        self._send_json({"reply": reply, "state": _state_payload(state.app)})
                        return
                    if path == "/api/tree/jump":
                        state.app.jump_to_entry(_entry_id(payload))
                        self._send_json(_state_payload(state.app))
                        return
                    if path == "/api/tree/fork":
                        state.app.fork_from_entry(_entry_id(payload))
                        self._send_json(_state_payload(state.app))
                        return
                    if path == "/api/tree/clone":
                        state.app.clone_active_branch()
                        self._send_json(_state_payload(state.app))
                        return
                    if path == "/api/tree/label":
                        state.app.label_entry(_entry_id(payload), str(payload.get("label", "")).strip())
                        self._send_json(_state_payload(state.app))
                        return
                    if path == "/api/compact":
                        compacted = state.app.compact_now()
                        self._send_json({"compacted": compacted, "state": _state_payload(state.app)})
                        return
                    if path == "/api/sessions/select":
                        session_id = str(payload.get("sessionId", "")).strip()
                        if session_id not in state.app.tree.listSessions():
                            raise KeyError(f"session not found: {session_id}")
                        state.app.session_id = session_id
                        state.app.tree.resumeSession(session_id)
                        state.app.history = state.app.tree.buildModelContext(session_id)
                        self._send_json(_state_payload(state.app))
                        return
                    if path == "/api/sessions":
                        title = str(payload.get("title", "")).strip() or None
                        session_id = state.app.tree.createSession(title=title, cwd=str(state.app.workspace))
                        state.app.session_id = session_id
                        state.app.history = state.app.tree.buildModelContext(session_id)
                        self._send_json(_state_payload(state.app), status=HTTPStatus.CREATED)
                        return
                self.send_error(HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self._send_error(exc)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _chat_stream(self) -> None:
            payload = self._read_json()
            message = str(payload.get("message", "")).strip()
            if not message:
                self._send_json({"error": "message is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            def emit(event: str, data: dict[str, Any]) -> None:
                self._write_sse(event, data)

            try:
                with state.lock:
                    emit("user_message", {"text": message})
                    reply = state.app.ask(
                        message,
                        on_text_delta=lambda text: emit("delta", {"text": text}),
                        on_tool_call=lambda block: emit("tool_call", _plain_data(block)),
                        on_tool_result=lambda result: emit("tool_result", result),
                    )
                    emit("done", {"reply": reply, "state": _state_payload(state.app)})
            except BrokenPipeError:
                return
            except Exception as exc:
                emit("error", {"error": str(exc)})

        def _serve_static(self, path: str, frontend_dir: Path) -> None:
            if path in {"", "/"}:
                target = frontend_dir / "index.html"
            else:
                target = (frontend_dir / path.lstrip("/")).resolve()
                try:
                    target.relative_to(frontend_dir.resolve())
                except ValueError as exc:
                    raise FileNotFoundError(path) from exc
            if not target.exists() or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            if not raw.strip():
                return {}
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, exc: Exception) -> None:
            status = HTTPStatus.BAD_REQUEST if isinstance(exc, (KeyError, ValueError)) else HTTPStatus.INTERNAL_SERVER_ERROR
            self._send_json({"error": str(exc)}, status=status)

        def _write_sse(self, event: str, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False)
            self.wfile.write(f"event: {event}\n".encode("utf-8"))
            for line in data.splitlines() or [""]:
                self.wfile.write(f"data: {line}\n".encode("utf-8"))
            self.wfile.write(b"\n")
            self.wfile.flush()

    return Handler


def _entry_id(payload: dict[str, Any]) -> str:
    entry_id = str(payload.get("entryId", "")).strip()
    if not entry_id:
        raise ValueError("entryId is required")
    return entry_id


def _sessions_payload(app: AgentApp) -> dict[str, Any]:
    return {
        "activeSessionId": app.session_id,
        "sessions": list_session_summaries(app.root / "sessions"),
    }


def _session_resource_route(path: str) -> tuple[str, str, str | None] | None:
    parts = [unquote(part) for part in path.strip("/").split("/") if part]
    if len(parts) == 4 and parts[:2] == ["api", "sessions"]:
        return parts[2], parts[3], None
    if len(parts) == 5 and parts[:2] == ["api", "sessions"] and parts[3] == "node":
        return parts[2], "node", parts[4]
    return None


def _session_resource_payload(
    state: WebState,
    session_id: str,
    resource: str,
    node_id: str | None,
) -> Any:
    records = load_session_records(session_id, state.root / "sessions")
    if resource == "raw":
        return {"sessionId": session_id, "records": records}
    if resource == "runs":
        return {
            "sessionId": session_id,
            "runs": records_to_run_steps(records),
            "fileChanges": records_to_file_changes(records),
            "toolEvents": records_to_tool_events(records),
        }
    if resource == "tree":
        return {"sessionId": session_id, "nodes": records_to_tree_nodes(records)}
    if resource == "node" and node_id:
        return {"sessionId": session_id, **records_to_node_detail(records, node_id)}
    raise KeyError(f"unknown session resource: {resource}")


def _state_payload(app: AgentApp, *, filter_mode: str = "default") -> dict[str, Any]:
    return {
        "provider": app.provider,
        "model": app.model,
        "workspace": str(app.workspace),
        "sessionId": app.session_id,
        "sessions": app.tree.listSessions(),
        "tree": _tree_payload(app, filter_mode=filter_mode),
        "tools": app.registry.names(),
        "todos": app.todos.render(),
        "mcp": app.mcp.report(),
        "team": app.team.list_all(),
    }


def _tree_payload(app: AgentApp, *, filter_mode: str = "default") -> dict[str, Any]:
    session = app.tree.sessions[app.session_id]
    debug = app.tree.debugBuildModelContext(app.session_id)
    nodes = []
    for entry in session.entries:
        if not app.tree._is_tree_entry(entry):  # TreeSessionManager owns the JSONL replay rules.
            continue
        nodes.append(
            {
                "id": entry.id,
                "type": entry.type,
                "parentId": entry.parentId,
                "timestamp": entry.timestamp,
                "metadata": entry.metadata,
                "label": session.labels.get(entry.id),
                "active": entry.id == session.activeLeafId,
                "visible": _visible(entry, filter_mode, session.labels),
                "preview": _entry_preview(entry),
                "contextLayer": app.tree.getContextLayer(app.session_id, entry.id).name,
                "data": _plain_data(entry),
            }
        )
    return {
        "sessionId": app.session_id,
        "filePath": str(app.tree.getSessionFilePath(app.session_id)),
        "activeLeafId": session.activeLeafId,
        "rootId": session.rootId,
        "title": session.title,
        "nodes": nodes,
        "childrenByParent": session.childrenByParent,
        "labels": session.labels,
        "debug": debug,
        "rendered": app.tree.render_tree(app.session_id, filter_mode=filter_mode),
    }


def _visible(entry: Any, filter_mode: str, labels: dict[str, str | None]) -> bool:
    if filter_mode == "all":
        return True
    if filter_mode == "labeled-only":
        return bool(labels.get(entry.id))
    if filter_mode == "user-only":
        return isinstance(entry, MessageEntry) and entry.message.get("role") == "user"
    if filter_mode == "no-tools":
        return not isinstance(entry, (ToolCallEntry, ToolResultEntry))
    return not isinstance(entry, ToolCallEntry)


def _entry_preview(entry: Any) -> str:
    if isinstance(entry, MessageEntry):
        return _content_preview(entry.message.get("content", ""))
    if isinstance(entry, ToolCallEntry):
        return str(entry.toolCall.get("name", ""))[:120]
    if isinstance(entry, ToolResultEntry):
        return _content_preview(entry.toolResult.get("content", ""))
    if isinstance(entry, BranchSummaryEntry):
        return entry.summary[:120]
    if isinstance(entry, CompactionEntry):
        return entry.summary[:120]
    return ""


def _content_preview(content: Any) -> str:
    if isinstance(content, str):
        return content.replace("\n", " ")[:160]
    if isinstance(content, list):
        parts = []
        for item in content:
            data = _plain_data(item)
            if isinstance(data, dict):
                if data.get("type") == "text":
                    parts.append(str(data.get("text", "")))
                elif data.get("type") == "tool_use":
                    parts.append(f"tool:{data.get('name', '')}")
                elif data.get("type") == "tool_result":
                    parts.append(str(data.get("content", "")))
            else:
                parts.append(str(data))
        return " ".join(part for part in parts if part).replace("\n", " ")[:160]
    return str(content).replace("\n", " ")[:160]


def _plain_data(value: Any) -> Any:
    if is_dataclass(value):
        return _plain_data(asdict(value))
    if isinstance(value, list):
        return [_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _plain_data({key: item for key, item in vars(value).items() if not key.startswith("_")})
    return str(value)


if __name__ == "__main__":
    main()
