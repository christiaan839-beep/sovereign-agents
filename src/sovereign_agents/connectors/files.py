"""Strict readers for the sample backends' JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def text(record: dict[str, Any], key: str, where: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: {key} must be a non-empty string")
    return value


def whole(record: dict[str, Any], key: str, where: str) -> int:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where}: {key} must be a whole number")
    return value


def items(document: Any, key: str, where: str) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or not isinstance(document.get(key), list):
        raise ValueError(f"{where}: expected an object with a {key} list")
    rows = document[key]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{where}: every entry in {key} must be an object")
    return list(rows)
