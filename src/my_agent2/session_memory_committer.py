from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol


class MemoryExtractor(Protocol):
    def extract(self, *, session_uri: str, summary: str, metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        ...


class LlmMemoryExtractor:
    def __init__(self, client: Any, model: str, *, max_tokens: int = 1200) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def extract(self, *, session_uri: str, summary: str, metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        prompt = _extraction_prompt(session_uri, summary, metadata)
        try:
            response = self.client.create_message(
                model=self.model, max_tokens=self.max_tokens,
                system="你从对话摘要中提取结构化长期记忆。只输出合法 JSON，不要其他文字。所有字段使用中文。",
                messages=[{"role": "user", "content": prompt}], tools=[],
            )
            text = "\n".join(
                getattr(block, "text", "")
                for block in (response.content if hasattr(response, "content") else [])
            )
            operations = _parse_extraction_json(text)
            return operations, None
        except Exception as e:
            return [], f"extraction_failed: {e}"


class NoopMemoryExtractor:
    def extract(self, *, session_uri: str, summary: str, metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        return [], None


class SessionMemoryCommitter:
    def __init__(self, tree: Any, memory_store: Any, extractor: MemoryExtractor) -> None:
        self.tree = tree
        self.memory_store = memory_store
        self.extractor = extractor

    def commit_compaction(self, session_id: str, compaction_id: str) -> str:
        # Read compaction entry
        branch = self.tree.getBranch(session_id)
        entry = next((e for e in branch if isinstance(e, dict) and e.get("id") == compaction_id
                      or hasattr(e, "id") and e.id == compaction_id), None)
        if entry is None:
            raise ValueError(f"Compaction entry {compaction_id} not found in session {session_id}")

        if isinstance(entry, dict):
            summary = entry.get("summary", "")
            compacted_ids = entry.get("compactedEntryIds", [])
            first_kept = entry.get("firstKeptEntryId", "")
            token_before = entry.get("tokenEstimateBefore", 0)
            token_after = entry.get("tokenEstimateAfter", 0)
        else:
            summary = entry.summary
            compacted_ids = entry.compactedEntryIds
            first_kept = entry.firstKeptEntryId
            token_before = entry.tokenEstimateBefore
            token_after = entry.tokenEstimateAfter

        # Debug info
        try:
            debug = self.tree.debugBuildModelContext(session_id)
        except Exception:
            debug = {}

        # Generate archive URI
        now = datetime.now(timezone.utc)
        date_part = now.strftime("%Y/%m/%d")
        archive_uri = f"ctx://sessions/archives/{date_part}/{session_id}-{compaction_id}"

        metadata = {
            "session_id": session_id,
            "compaction_id": compaction_id,
            "compactedEntryIds": compacted_ids,
            "firstKeptEntryId": first_kept,
            "tokenEstimateBefore": token_before,
            "tokenEstimateAfter": token_after,
            "debug": debug,
        }

        # Extract memory operations
        operations, error = self.extractor.extract(
            session_uri=archive_uri, summary=summary, metadata=metadata,
        )
        if error:
            metadata["extraction_error"] = error

        # Commit
        self.memory_store.commit_session_archive(
            session_uri=archive_uri,
            summary=summary,
            operations=operations,
            metadata=metadata,
        )
        return archive_uri


def _extraction_prompt(session_uri: str, summary: str, metadata: dict[str, Any]) -> str:
    essential = {
        "session_id": metadata.get("session_id", ""),
        "compaction_id": metadata.get("compaction_id", ""),
        "token_estimate_before": metadata.get("tokenEstimateBefore", 0),
        "token_estimate_after": metadata.get("tokenEstimateAfter", 0),
    }
    return f"""从以下对话摘要中提取值得长期保留的记忆。

会话: {session_uri}
基本信息: {json.dumps(essential, ensure_ascii=False)}

摘要:
{summary}

输出一个 JSON 对象，包含 "operations" 数组。每条 operation 格式:
{{
  "action": "upsert" | "append" | "quarantine",
  "category": "profile" | "preferences" | "entities" | "events" | "decisions" | "constraints" | "open_tasks" | "cases" | "patterns" | "tools" | "skills",
  "key": "稳定去重标识（英文slug）",
  "title": "简短中文标题",
  "abstract": "一句话中文摘要",
  "overview": "可注入上下文的中文概述（2-4句）",
  "content": "完整中文正文",
  "reason": "为什么值得长期保留",
  "trust_score": 0.0-1.0,
  "tags": ["可选标签"],
  "links": [
    {{
      "target_uri": "mem://...",
      "relation": "supports|contradicts|updates|related|derived_from|uses_tool",
      "confidence": 0.0-1.0,
      "reason": "关联原因"
    }}
  ]
}}

重要规则:
- 如果用户明确改变了之前的偏好/决策（如从暗色主题改为亮色），使用 action="upsert" + 相同的 key，并在 links 中用 "updates" 关联旧记忆。
- 只提取有跨会话价值的长期信息，跳过临时调试细节。
- 所有文本字段使用中文。
- 只输出 JSON 对象，不要其他文字。"""


def _parse_extraction_json(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    # Try to find JSON block
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()
    # Find outermost { }
    if "{" in text and "}" in text:
        start = text.index("{")
        end = text.rindex("}") + 1
        text = text[start:end]
    data = json.loads(text)
    return data.get("operations", [])
