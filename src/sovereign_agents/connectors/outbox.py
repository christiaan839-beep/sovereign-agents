"""A local outbox file: what a sample backend "sends" lands here, once per idempotency key."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Outbox:
    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text("utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def append(self, record: dict[str, Any], *, key: str) -> bool:
        """Write ``record`` under ``key`` unless that key is already there; True if written."""
        if any(existing.get("idempotencyKey") == key for existing in self.records()):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({**record, "idempotencyKey": key}, ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return True
