"""A reorder, read strictly from the agent's input, and the owner's limits on it."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sovereign_agents.kit import canonicalize

from ...connectors.suppliers import Supplier
from ...formatting import format_cents

NAME = "supplier_reorders"
MAX_ID_CHARS = 64
MAX_REPLY_CHARS = 2000


@dataclass(frozen=True)
class ReorderCaps:
    """The owner's limits. The defaults hold until the owner sets their own."""

    max_lines: int = 10
    max_order_total_cents: int = 500_000
    approval_window: timedelta = timedelta(hours=48)


@dataclass(frozen=True)
class ReorderLine:
    sku: str
    quantity: int
    unit_price_cents: int

    @property
    def total_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass(frozen=True)
class Reorder:
    supplier_id: str
    source_message_id: str
    lines: tuple[ReorderLine, ...]
    reply: str

    @property
    def total_cents(self) -> int:
        return sum(line.total_cents for line in self.lines)

    def payload(self) -> dict[str, Any]:
        return {
            "supplierId": self.supplier_id,
            "sourceMessageId": self.source_message_id,
            "lines": [
                {
                    "sku": line.sku,
                    "quantity": line.quantity,
                    "unitPriceCents": line.unit_price_cents,
                }
                for line in self.lines
            ],
            "totalCents": self.total_cents,
            "reply": self.reply,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Reorder:
        return parse_reorder(
            {
                "supplier_id": payload["supplierId"],
                "source_message_id": payload["sourceMessageId"],
                "lines": [
                    {
                        "sku": line["sku"],
                        "quantity": line["quantity"],
                        "unit_price_cents": line["unitPriceCents"],
                    }
                    for line in payload["lines"]
                ],
                "reply": payload["reply"],
            }
        )


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > limit:
        raise ValueError(f"{name} is longer than {limit} characters")
    return value


def _whole(value: Any, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number, not {value!r}")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def parse_reorder(arguments: Mapping[str, Any]) -> Reorder:
    """The reorder the agent asked to stage. Raises ValueError on anything malformed.

    Quantities and prices must be integers: a float such as ``3.0`` is refused here, even
    though JSON Schema's ``integer`` accepts it.
    """
    raw_lines = arguments.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError("lines must be a non-empty list")
    lines = []
    for index, raw in enumerate(raw_lines):
        if not isinstance(raw, dict):
            raise ValueError(f"lines[{index}] must be an object")
        lines.append(
            ReorderLine(
                sku=_text(raw.get("sku"), f"lines[{index}].sku", MAX_ID_CHARS),
                quantity=_whole(raw.get("quantity"), f"lines[{index}].quantity", 1),
                unit_price_cents=_whole(
                    raw.get("unit_price_cents"), f"lines[{index}].unit_price_cents", 0
                ),
            )
        )
    return Reorder(
        supplier_id=_text(arguments.get("supplier_id"), "supplier_id", MAX_ID_CHARS),
        source_message_id=_text(
            arguments.get("source_message_id"), "source_message_id", MAX_ID_CHARS
        ),
        lines=tuple(lines),
        reply=_text(arguments.get("reply"), "reply", MAX_REPLY_CHARS),
    )


def cap_violations(
    reorder: Reorder,
    caps: ReorderCaps,
    suppliers: Mapping[str, Supplier],
    currency: str,
) -> list[str]:
    """Every way ``reorder`` breaks the owner's limits; empty when it keeps them all."""
    violations = []
    supplier = suppliers.get(reorder.supplier_id)
    if supplier is None:
        violations.append(f"{reorder.supplier_id} is not one of the business's known suppliers")
    if len(reorder.lines) > caps.max_lines:
        violations.append(f"{len(reorder.lines)} lines is more than the cap of {caps.max_lines}")
    skus = [line.sku for line in reorder.lines]
    repeated = sorted({sku for sku in skus if skus.count(sku) > 1})
    if repeated:
        violations.append(f"{', '.join(repeated)} appears more than once")
    if supplier is not None:
        unsupplied = sorted(set(skus) - set(supplier.skus))
        if unsupplied:
            violations.append(f"{supplier.name} does not supply {', '.join(unsupplied)}")
    if reorder.total_cents > caps.max_order_total_cents:
        violations.append(
            f"the order total {format_cents(reorder.total_cents, currency)} is over the cap of "
            f"{format_cents(caps.max_order_total_cents, currency)} per order"
        )
    return violations


def idempotency_key(business: str, reorder: Reorder) -> str:
    """The same reorder, from the same message, in any line order, is one change."""
    identity = {
        "job": NAME,
        "business": business,
        "supplierId": reorder.supplier_id,
        "sourceMessageId": reorder.source_message_id,
        "lines": sorted(
            (
                {
                    "sku": line.sku,
                    "quantity": line.quantity,
                    "unitPriceCents": line.unit_price_cents,
                }
                for line in reorder.lines
            ),
            key=lambda line: str(line["sku"]),
        ),
    }
    return hashlib.sha256(canonicalize(identity).encode("utf-8")).hexdigest()
