from __future__ import annotations

from pathlib import Path

try:
    from jinja2 import Template
except ModuleNotFoundError:  # pragma: no cover - only used before dependencies are installed
    Template = None


class ContextBuilder:
    def __init__(self, templates_dir: Path, skills_loader, memory_store) -> None:
        self.templates_dir = templates_dir
        self.skills_loader = skills_loader
        self.memory_store = memory_store

    def build(self, *, workspace: Path, runtime_context: str = "") -> str:
        template_path = self.templates_dir / "system.md"
        active_skills = self.skills_loader.active_context()
        always_names = {skill.name for skill in self.skills_loader.always_skills()}
        values = {
            "workspace": str(workspace),
            "active_skills": active_skills,
            "skills_summary": self.skills_loader.summary(exclude=always_names),
            "memory": self.memory_store.read_memory(),
            "user_profile": self.memory_store.read_user(),
            "runtime_context": runtime_context,
        }
        raw = template_path.read_text(encoding="utf-8")
        if Template is not None:
            return Template(raw).render(**values).strip()
        return _fallback_render(raw, values).strip()


def _fallback_render(raw: str, values: dict[str, str]) -> str:
    rendered = raw
    rendered = rendered.replace('{{ active_skills or "(None)" }}', values["active_skills"] or "(None)")
    for key, value in values.items():
        rendered = rendered.replace("{{ " + key + " }}", value)
        rendered = rendered.replace("{{" + key + "}}", value)
    rendered = rendered.replace("{{ runtime_context or \"(None)\" }}", values.get("runtime_context", "") or "(None)")
    rendered = rendered.replace("{{ runtime_context }}", values.get("runtime_context", ""))
    return rendered


class RuntimeContextBuilder:
    def __init__(self, backend: Any, *, limit: int = 6, max_chars: int = 12000) -> None:
        self.backend = backend
        self.limit = limit
        self.max_chars = max_chars

    def build(self, query: str) -> str:
        results = self.backend.search(query, limit=self.limit)
        if not results:
            return "(No runtime context recalled.)"

        lines = ["## Runtime Context"]
        total = 0
        for result in results:
            # 过滤已归档和内部敏感记忆
            if result.get("status") == "archived":
                continue
            if result.get("sensitivity") in ("sensitive", "internal"):
                continue
            uri = result.get("uri", "")
            neighbors = self.backend.neighbors(uri, limit=3)
            link_lines = ""
            if neighbors:
                link_lines = ", ".join(
                    f"{n['target_uri']} ({n.get('relation', 'related')})"
                    for n in neighbors[:2]
                )
                link_lines = f"\n  Links: {link_lines}"

            entry = (
                f"- URI: {uri}\n"
                f"  Trust: {result.get('trust_score', '?')}\n"
                f"  Updated: {result.get('updated_at', '?')}\n"
                f"  Matched: {result.get('title', '?')}\n"
                f"  Summary: {result.get('abstract', result.get('overview', ''))}{link_lines}"
            )
            if total + len(entry) > self.max_chars:
                break
            lines.append(entry)
            total += len(entry)

        return "\n".join(lines) if len(lines) > 1 else "(No runtime context recalled.)"
