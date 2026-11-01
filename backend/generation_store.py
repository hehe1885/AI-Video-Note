"""Verified, task-owned checkpoints. Only an explicit continuation reads older work."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def policy_revision() -> str:
    """Invalidate old prose when generation rules change, independently of UI edits."""
    if getattr(sys, "frozen", False):
        # PyInstaller stores Python modules in its archive rather than loose .py files.
        from .runtime_revision import CODE_REVISION
        return CODE_REVISION
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ("llm_summarizer.py", "transcript.py", "note_options.py", "note_styles.py", "prompt_language.py", "prompt_rules.py", "note_context.py", "transcript_review.py", "generation_store.py"):
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


class GenerationStore:
    def __init__(self, directory: Path, identity: dict[str, Any], sources: list[Path] | None = None):
        self.directory = Path(directory)
        self.identity = fingerprint({"schema": 1, "policy": policy_revision(), **identity})
        self.sources = [Path(path) for path in sources or []]
        self.directory.mkdir(parents=True, exist_ok=True)
        # Store hashes, never credentials, endpoint queries, prompts or request bodies.
        self._write(self.directory / "manifest.json", json.dumps({"schema": 1, "identity": self.identity}))

    @staticmethod
    def _write(path: Path, text: str) -> None:
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _name(key: str) -> str:
        if not re.fullmatch(r"[a-z0-9-]{1,100}", key):
            raise ValueError("Invalid checkpoint key")
        return key

    def load(self, key: str) -> str | None:
        return self._load(key, "completed")

    def load_incomplete(self, key: str) -> str | None:
        """Incomplete prose is available only to the explicit continuation path."""
        return self._load(key, "incomplete")

    def _load(self, key: str, status: str) -> str | None:
        name = self._name(key)
        for root in [self.directory, *self.sources]:
            try:
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                entry = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
                if manifest.get("identity") != self.identity or entry.get("identity") != self.identity:
                    continue
                content = (root / f"{name}.md").read_text(encoding="utf-8")
                if not content.strip() or fingerprint(content) != entry.get("content_hash"):
                    continue
                if status == "incomplete" and entry.get("status") == "completed":
                    return None
                if entry.get("status") != status:
                    continue
                if root != self.directory:
                    self._save(key, content, status)
                return content
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        return None

    def save(self, key: str, content: str, *, degraded: bool = False) -> None:
        self._save(key, content, "degraded" if degraded else "completed")

    def save_incomplete(self, key: str, content: str) -> None:
        self._save(key, content, "incomplete")

    def _save(self, key: str, content: str, status: str) -> None:
        name = self._name(key)
        if not content.strip():
            return
        self._write(self.directory / f"{name}.md", content)
        # The receipt is committed last; a partial or modified body never counts as done.
        entry = {"identity": self.identity, "status": status, "content_hash": fingerprint(content)}
        self._write(self.directory / f"{name}.json", json.dumps(entry))


def continuation_sources(task_id: str | None, directory, read_manifest, tasks: dict) -> list[Path]:
    """Follow interrupted attempts, but do not reuse prose from a completed note."""
    result: list[Path] = []
    visited: set[str] = set()
    while task_id and task_id not in visited and len(visited) < 20:
        visited.add(task_id)
        manifest = read_manifest(task_id)
        status = tasks.get(task_id, {}).get("status") or (manifest.get("runtime") or {}).get("status")
        if status == "completed" or tasks.get(task_id, {}).get("_deleting"):
            break
        result.append(directory(task_id) / "generation")
        task_id = manifest.get("reused_task_id")
    return result
