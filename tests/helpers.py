"""Shared test helpers for context/memory tests."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


def make_temp_dir() -> Path:
    """Create a temporary directory that lives for the test duration."""
    return Path(tempfile.mkdtemp())


def make_contextfs_root(tmp: Path) -> Path:
    """Create a memory/context directory structure."""
    root = tmp / "memory" / "context"
    root.mkdir(parents=True)
    (root / "index.jsonl").touch()
    (root / "diffs.jsonl").touch()
    (root / "links.jsonl").touch()
    return root


def make_memory_object(
    uri: str,
    *,
    title: str = "Test Memory",
    abstract: str = "Test abstract.",
    overview: str = "Test overview.",
    content: str = "Full test content.",
    context_type: str = "memory",
    trust_score: float = 0.8,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "uri": uri,
        "context_type": context_type,
        "title": title,
        "abstract": abstract,
        "overview": overview,
        "source": "test",
        "trust_score": trust_score,
        "sensitivity": "public",
        "status": "active",
        "tags": tags or [],
        "metadata": {},
        "digest": "fake-digest",
        "created_at": "2026-05-24T10:00:00+08:00",
        "updated_at": "2026-05-24T10:00:00+08:00",
        "ttl": None,
        "content_path": "",
    }


def make_fake_model_client(responses: list[Any] | None = None):
    """Return a fake model client that returns predetermined responses.

    Each response is a list of content blocks. Call .create_message() pops
    the first response from the list.
    """
    class FakeUsage:
        input_tokens = 100
        output_tokens = 50

    class FakeResponse:
        def __init__(self, content):
            self.content = content
            self.usage = FakeUsage()
            self.stop_reason = "stop"

    class FakeClient:
        def __init__(self, resps):
            self.resps = list(resps or [])
            self.requests = []

        def create_message(self, **kwargs):
            self.requests.append(kwargs)
            if not self.resps:
                raise RuntimeError("No more fake responses")
            return FakeResponse(self.resps.pop(0))

    return FakeClient(list(responses or []))
