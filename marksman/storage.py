"""JSON-backed database for tools, sessions and custom targets.

The whole dataset lives in a single human-readable JSON file (default
``marksman_data.json`` in the current directory).  It is small -- a few
hundred sessions is trivial -- so we load it whole, mutate, and save whole.
Saving is atomic (write to a temp file, then replace) to avoid corruption.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import Session, Tool, TargetSpec

DEFAULT_DB_PATH = "marksman_data.json"
SCHEMA_VERSION = 1


@dataclass
class Database:
    """In-memory view of the dataset, plus load/save helpers."""

    tools: Dict[str, Tool] = field(default_factory=dict)
    sessions: Dict[str, Session] = field(default_factory=dict)
    custom_targets: Dict[str, TargetSpec] = field(default_factory=dict)
    # Small bag of user preferences (e.g. the chosen "skin"/theme).  Kept as a
    # free-form dict so new preferences don't need a schema bump.
    settings: Dict[str, Any] = field(default_factory=dict)
    path: str = DEFAULT_DB_PATH

    # -- locations --------------------------------------------------------- #
    @property
    def recreations_dir(self) -> str:
        """Where recreation diagrams live: a ``recreations/`` dir by the DB.

        Keeping them beside the database (rather than next to the now-deleted
        source media) means they survive cleanup and travel with the data.
        """
        return os.path.join(os.path.dirname(os.path.abspath(self.path)),
                            "recreations")

    # -- tools ----------------------------------------------------------- #
    def add_tool(self, tool: Tool) -> None:
        if tool.id in self.tools:
            raise ValueError("Tool id %r already exists." % tool.id)
        self.tools[tool.id] = tool

    def get_tool(self, tool_id: str) -> Optional[Tool]:
        return self.tools.get(tool_id)

    def find_tool(self, needle: str) -> Optional[Tool]:
        """Resolve a tool by id, or by exact/substring name (case-insensitive)."""
        if needle in self.tools:
            return self.tools[needle]
        low = needle.strip().lower()
        for w in self.tools.values():
            if w.name.lower() == low:
                return w
        matches = [w for w in self.tools.values() if low in w.name.lower()]
        return matches[0] if len(matches) == 1 else None

    # -- sessions ---------------------------------------------------------- #
    def add_session(self, session: Session) -> None:
        if session.tool_id not in self.tools:
            raise ValueError(
                "Session references unknown tool id %r." % session.tool_id
            )
        self.sessions[session.id] = session

    def sessions_for_tool(self, tool_id: str) -> List[Session]:
        return [s for s in self.sessions.values() if s.tool_id == tool_id]

    def sessions_for_category(self, category: str) -> List[Session]:
        ids = set(w.id for w in self.tools.values() if w.category == category)
        return [s for s in self.sessions.values() if s.tool_id in ids]

    def all_sessions(self) -> List[Session]:
        return list(self.sessions.values())

    # -- custom targets ---------------------------------------------------- #
    def add_target(self, spec: TargetSpec) -> None:
        self.custom_targets[spec.name.lower()] = spec

    # -- persistence ------------------------------------------------------- #
    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "tools": [w.to_dict() for w in self.tools.values()],
            "sessions": [s.to_dict() for s in self.sessions.values()],
            "custom_targets": [t.to_dict() for t in self.custom_targets.values()],
            "settings": self.settings,
        }

    def save(self, path: Optional[str] = None) -> str:
        """Atomically write the database to disk; return the path written."""
        target = path or self.path
        data = json.dumps(self.to_dict(), indent=2)
        directory = os.path.dirname(os.path.abspath(target))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory or None, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.path = target
        return target

    @classmethod
    def from_dict(cls, d: dict, path: str = DEFAULT_DB_PATH) -> "Database":
        db = cls(path=path)
        for wd in d.get("tools", []):
            w = Tool.from_dict(wd)
            db.tools[w.id] = w
        for sd in d.get("sessions", []):
            s = Session.from_dict(sd)
            db.sessions[s.id] = s
        for td in d.get("custom_targets", []):
            t = TargetSpec.from_dict(td)
            db.custom_targets[t.name.lower()] = t
        db.settings = dict(d.get("settings", {}))
        return db

    @classmethod
    def load(cls, path: str = DEFAULT_DB_PATH) -> "Database":
        """Load from ``path``; return an empty database if it doesn't exist."""
        if not os.path.exists(path):
            return cls(path=path)
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return cls.from_dict(d, path=path)
