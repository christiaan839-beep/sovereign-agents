"""The business's stock levels."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .files import items, read_json, text, whole


@dataclass(frozen=True)
class StockLevel:
    sku: str
    name: str
    on_hand: int
    reorder_point: int


class Stock(Protocol):
    def levels(self) -> list[StockLevel]: ...


class SampleStock:
    """Reads ``stock.json``."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def levels(self) -> list[StockLevel]:
        where = str(self._path)
        return [
            StockLevel(
                sku=text(row, "sku", where),
                name=text(row, "name", where),
                on_hand=whole(row, "on_hand", where),
                reorder_point=whole(row, "reorder_point", where),
            )
            for row in items(read_json(self._path), "items", where)
        ]
