from __future__ import annotations

from typing import Any

from .base import Tool, object_schema


VALID_STATUSES = {"pending", "in_progress", "completed"}


class TodoStore:
    def __init__(self) -> None:
        self.todos: list[dict[str, Any]] = []

    def update(self, todos: list[dict[str, Any]]) -> str:
        cleaned = []
        for index, item in enumerate(todos, start=1):
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            status = item.get("status", "pending")
            if status not in VALID_STATUSES:
                status = "pending"
            cleaned.append({"id": item.get("id", index), "content": content, "status": status})

        if sum(1 for item in cleaned if item["status"] == "in_progress") > 1:
            return "Error: only one todo may be in_progress at a time."
        self.todos = cleaned
        return self.render()

    def render(self) -> str:
        if not self.todos:
            return "(No todos.)"
        icon = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
        return "\n".join(
            f"{icon.get(item['status'], '[?]')} {item['id']}. {item['content']}"
            for item in self.todos
        )


class UpdateTodosTool(Tool):
    name = "update_todos"
    description = (
        "Create or replace the current task todo list. Send the complete list every time. "
        "Use one in_progress item at most."
    )

    def __init__(self, store: TodoStore) -> None:
        self.store = store

    @property
    def parameters(self) -> dict:
        return object_schema(
            {
                "todos": {
                    "type": "array",
                    "description": "Complete ordered todo list.",
                    "items": object_schema(
                        {
                            "id": {"type": "integer"},
                            "content": {"type": "string", "minLength": 1},
                            "status": {
                                "type": "string",
                                "enum": sorted(VALID_STATUSES),
                            },
                        },
                        required=["id", "content", "status"],
                    ),
                }
            },
            required=["todos"],
        )

    def execute(self, todos: list[dict[str, Any]]) -> str:
        return self.store.update(todos)


class RememberTool(Tool):
    name = "remember"
    description = "Append a durable note to long-term memory. category: preferences|events|decisions|constraints|cases|patterns|tools|skills|entities|open_tasks|profile"

    def __init__(self, memory_store) -> None:
        self.memory_store = memory_store

    @property
    def parameters(self) -> dict:
        return object_schema({
            "note": {"type": "string", "minLength": 1},
            "category": {"type": "string"},
            "title": {"type": "string"},
        }, required=["note"])

    def execute(self, note: str, category: str = "events", title: str | None = None) -> str:
        valid = {"profile", "preferences", "entities", "events",
                 "decisions", "constraints", "open_tasks",
                 "cases", "patterns", "tools", "skills"}
        if category not in valid:
            return f"Error: invalid category '{category}'. Valid: {', '.join(sorted(valid))}"
        if hasattr(self.memory_store, "remember_note"):
            uri = self.memory_store.remember_note(note, category=category, title=title)
            return f"Remembered: {uri}"
        # legacy fallback
        self.memory_store.append_memory(note)
        return "Remembered."


class LoadSkillTool(Tool):
    name = "load_skill"
    description = "Load the full content of a named skill into context when it is relevant."
    read_only = True

    def __init__(self, skills_loader) -> None:
        self.skills_loader = skills_loader

    @property
    def parameters(self) -> dict:
        return object_schema({"name": {"type": "string", "minLength": 1}}, required=["name"])

    def execute(self, name: str) -> str:
        return self.skills_loader.load(name)
