"""The business's known suppliers, and placing an order with one."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .files import items, read_json, text
from .outbox import Outbox


@dataclass(frozen=True)
class Supplier:
    supplier_id: str
    name: str
    email: str
    skus: tuple[str, ...]


@dataclass(frozen=True)
class OrderLine:
    sku: str
    quantity: int
    unit_price_cents: int


@dataclass(frozen=True)
class PurchaseOrder:
    idempotency_key: str
    supplier_id: str
    lines: tuple[OrderLine, ...]
    total_cents: int
    currency: str


class Suppliers(Protocol):
    def list_suppliers(self) -> list[Supplier]: ...

    def place_order(self, order: PurchaseOrder) -> str: ...


def suppliers_by_id(suppliers: Suppliers) -> dict[str, Supplier]:
    """The known suppliers, by ``supplier_id``."""
    return {supplier.supplier_id: supplier for supplier in suppliers.list_suppliers()}


class SampleSuppliers:
    """Reads ``suppliers.json``; placing an order writes to the outbox's ``orders.jsonl``."""

    def __init__(self, path: Path, outbox_dir: Path) -> None:
        self._path = path
        self.outbox = Outbox(outbox_dir / "orders.jsonl")

    def list_suppliers(self) -> list[Supplier]:
        where = str(self._path)
        suppliers = []
        for row in items(read_json(self._path), "suppliers", where):
            skus = row.get("skus")
            if not isinstance(skus, list) or not all(isinstance(s, str) for s in skus):
                raise ValueError(f"{where}: skus must be a list of strings")
            suppliers.append(
                Supplier(
                    supplier_id=text(row, "supplier_id", where),
                    name=text(row, "name", where),
                    email=text(row, "email", where),
                    skus=tuple(skus),
                )
            )
        return suppliers

    def place_order(self, order: PurchaseOrder) -> str:
        reference = f"po-{order.idempotency_key[:12]}"
        self.outbox.append(
            {
                "reference": reference,
                "supplierId": order.supplier_id,
                "lines": [
                    {
                        "sku": line.sku,
                        "quantity": line.quantity,
                        "unitPriceCents": line.unit_price_cents,
                    }
                    for line in order.lines
                ],
                "totalCents": order.total_cents,
                "currency": order.currency,
            },
            key=order.idempotency_key,
        )
        return reference
