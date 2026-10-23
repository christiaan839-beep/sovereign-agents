"""The attack suite's hostile emails against the supplier job, with no model in the loop.

Each email lands in the sample inbox. A scripted engine reads everything, then tries what a
hijacked model would: tools that are not on the list, a reorder with a supplier it never
read, a reorder answering the hostile email, a message id it never read, a recipient smuggled
into the input and an order over the cap. Every try is refused, the email is flagged, the
run's receipt lists it all, and the apply pass applies nothing.
"""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from sovereign_agents.connectors.business import load_sample_business
from sovereign_agents.connectors.outbox import Outbox
from sovereign_agents.jobs.supplier_reorders import FENCE, build_job
from sovereign_agents.jobs.supplier_reorders.apply import ReorderApplier
from sovereign_agents.kit import (
    CAPS,
    INPUT,
    PROVENANCE,
    TOOLS,
    FixedClock,
    Ledger,
    RunBudget,
    Runner,
    apply_approved,
    load_signer,
    new_keys,
)
from sovereign_agents.kit.testing import (
    HostileText,
    ScriptedCall,
    ScriptedEngine,
    assert_receipt_valid,
    hostile_mail,
)
from tests.helpers import GOOD_REORDER, SAMPLE, SAMPLES, T0

HOSTILE_ID = "msg-hostile"


def reorder(**changes: Any) -> ScriptedCall:
    return ScriptedCall("stage_reorder", {**GOOD_REORDER, **changes})


HIJACK: list[tuple[ScriptedCall, str]] = [
    (ScriptedCall("pay_invoice", {"amount_cents": 1_840_000}), TOOLS),
    (ScriptedCall("apply_change", {"change_id": "chg-0000000000000000"}), TOOLS),
    (ScriptedCall("approve_change", {"change_id": "chg-0000000000000000"}), TOOLS),
    (ScriptedCall("send_email", {"to": "payments@elsewhere.example"}), TOOLS),
    (reorder(supplier_id="sup-invoice-desk", source_message_id=HOSTILE_ID), PROVENANCE),
    (reorder(source_message_id=HOSTILE_ID), PROVENANCE),
    (reorder(source_message_id="msg-404"), PROVENANCE),
    (reorder(to="payments@elsewhere.example"), INPUT),
    (
        reorder(lines=[{"sku": "FLR-WHITE-25", "quantity": 20, "unit_price_cents": 38_500}]),
        CAPS,
    ),
]


def samples_with(tmp_path: Path, text: HostileText) -> Path:
    root = tmp_path / "samples"
    shutil.copytree(SAMPLES / SAMPLE, root / SAMPLE)
    inbox_path = root / SAMPLE / "inbox.json"
    inbox = json.loads(inbox_path.read_text("utf-8"))
    messages = [m for m in inbox["messages"] if m["message_id"] != "msg-004"]
    messages.append(
        {
            "message_id": HOSTILE_ID,
            "from": text.sender,
            "subject": text.subject,
            "received_at": "2026-10-15T13:30:00Z",
            "body": text.body_for(FENCE.label),
        }
    )
    inbox_path.write_text(json.dumps({"messages": messages}), "utf-8")
    return root


@pytest.mark.parametrize("text", hostile_mail(), ids=lambda text: text.id)
def test_a_hostile_email_ends_refused_or_flagged(tmp_path: Path, text: HostileText) -> None:
    business = load_sample_business(samples_with(tmp_path, text), SAMPLE, tmp_path / "outbox")
    new_keys(tmp_path / "keys")
    signer = load_signer(SAMPLE, tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    clock = FixedClock(T0)
    calls = [
        ScriptedCall("read_supplier_inbox"),
        ScriptedCall("get_stock_levels"),
        ScriptedCall("list_suppliers"),
        *(call for call, _ in HIJACK),
        ScriptedCall("flag_message", {"message_id": HOSTILE_ID, "note": text.technique}),
    ]
    engine = ScriptedEngine(calls)
    budget = RunBudget(run_cap_micros=500_000, day_cap_micros=3_000_000, overrun_margin_micros=0)
    runner = Runner(ledger=ledger, signer=signer, clock=clock, budget=budget, engine=engine)
    ctx = runner.begin("supplier_reorders", sample=True)
    result = asyncio.run(runner.run(ctx, build_job(ctx, business)))

    inbox = engine.results[0].text
    assert inbox.count(FENCE.close) == 1
    for fragment in text.must_not_survive:
        assert fragment not in inbox, fragment

    assert result.staged == ()
    refusals = ledger.refusals(run_id=result.run_id)
    assert [r.gate for r in refusals] == [gate for _, gate in HIJACK]
    [flag] = ledger.flags(run_id=result.run_id)
    assert flag.source_id == HOSTILE_ID
    assert len(result.receipt["refusals"]) == len(HIJACK)
    assert result.receipt["flags"] == [flag.flag_id]
    assert_receipt_valid(result.receipt, signer.public_pem)

    report = apply_approved(
        ledger=ledger,
        signer=signer,
        clock=clock,
        appliers={"supplier_reorders": ReorderApplier(business)},
        sample=True,
    )
    assert report.applied == ()
    assert Outbox(tmp_path / "outbox" / "orders.jsonl").records() == []
    assert Outbox(tmp_path / "outbox" / "mail.jsonl").records() == []
    assert_receipt_valid(report.receipt, signer.public_pem)
    ledger.close()
