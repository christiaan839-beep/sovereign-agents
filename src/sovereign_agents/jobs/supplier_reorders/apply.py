"""The host's side of a reorder, run only after the owner's yes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sovereign_agents.kit import Change

from ...connectors.business import Business
from ...connectors.mailbox import OutgoingMail
from ...connectors.suppliers import OrderLine, PurchaseOrder
from .caps import Reorder, ReorderCaps, cap_violations


@dataclass(frozen=True)
class ReorderApplier:
    business: Business
    caps: ReorderCaps = field(default_factory=ReorderCaps)

    def cap_violations(self, change: Change) -> list[str]:
        """The caps in force now, checked again at apply time."""
        suppliers = {s.supplier_id: s for s in self.business.suppliers.list_suppliers()}
        reorder = Reorder.from_payload(change.payload)
        return cap_violations(reorder, self.caps, suppliers, self.business.currency)

    def apply(self, change: Change) -> dict[str, Any]:
        """Place the order and send the reply, both to the supplier's known address only."""
        reorder = Reorder.from_payload(change.payload)
        supplier = next(
            s
            for s in self.business.suppliers.list_suppliers()
            if s.supplier_id == reorder.supplier_id
        )
        order_ref = self.business.suppliers.place_order(
            PurchaseOrder(
                idempotency_key=change.idempotency_key,
                supplier_id=supplier.supplier_id,
                lines=tuple(
                    OrderLine(line.sku, line.quantity, line.unit_price_cents)
                    for line in reorder.lines
                ),
                total_cents=reorder.total_cents,
                currency=self.business.currency,
            )
        )
        reply_ref = self.business.mailbox.send(
            OutgoingMail(
                idempotency_key=change.idempotency_key,
                to=supplier.email,
                subject=f"Order {order_ref}",
                body=reorder.reply,
                in_reply_to=reorder.source_message_id,
            )
        )
        return {"orderRef": order_ref, "replyRef": reply_ref, "to": supplier.email}
