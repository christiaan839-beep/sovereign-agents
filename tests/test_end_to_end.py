"""The whole loop through the command line, with a scripted engine and no model."""

import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sovereign_agents.cli import main
from sovereign_agents.config import KitConfig
from sovereign_agents.connectors.outbox import Outbox
from sovereign_agents.kit import TERMINAL, Engine, FixedClock, Ledger, decide, load_signer
from sovereign_agents.kit.testing import ScriptedCall, ScriptedEngine, assert_receipt_valid
from tests.helpers import GOOD_REORDER, SAMPLE, SAMPLES

T0 = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
RUN = ["run", "--business", SAMPLE, "--job", "supplier_reorders", "--samples", str(SAMPLES)]
APPROVE = ["approve", "--business", SAMPLE, "--samples", str(SAMPLES), "--owner", "the owner"]


def cli(
    args: list[str],
    home: Path,
    clock: FixedClock,
    answers: Callable[[str], str] = lambda prompt: "n",
    reorder: dict[str, Any] = GOOD_REORDER,
) -> str:
    out = io.StringIO()

    def scripted(config: KitConfig) -> Engine:
        return ScriptedEngine(
            [
                ScriptedCall("read_supplier_inbox"),
                ScriptedCall("get_stock_levels"),
                ScriptedCall("list_suppliers"),
                ScriptedCall("stage_reorder", reorder),
                ScriptedCall("flag_message", {"message_id": "msg-004", "note": "Asks to pay."}),
            ],
            cost_micros=12_346,
        )

    code = main(
        args,
        env={"SOVEREIGN_AGENTS_HOME": str(home)},
        out=out,
        err=out,
        ask=answers,
        clock=clock,
        engine=scripted,
    )
    assert code == 0, out.getvalue()
    return out.getvalue()


def check_every_receipt(home: Path) -> None:
    signer = load_signer(SAMPLE, home / "keys" / SAMPLE)
    ledger = Ledger(home / "ledger.sqlite3")
    receipts = ledger.receipts(SAMPLE)
    ledger.close()
    assert receipts
    for receipt in receipts:
        assert_receipt_valid(receipt, signer.public_pem)


def outbox(home: Path, name: str) -> list[dict[str, object]]:
    return Outbox(home / "outbox" / SAMPLE / name).records()


def test_a_yes_orders_and_replies_once_and_every_receipt_verifies(tmp_path: Path) -> None:
    clock = FixedClock(T0)
    cli(["keys", "new", "--business", SAMPLE], tmp_path, clock)
    cli(RUN, tmp_path, clock)
    shown = cli(APPROVE, tmp_path, clock, answers=lambda prompt: "y")
    assert "msg-004: Asks to pay." in shown
    assert "Reorder from Sample Flour Mill (sup-flour)" in shown
    assert "Total: R2 310.00" in shown
    assert "Applied chg-" in shown
    assert [order["totalCents"] for order in outbox(tmp_path, "orders.jsonl")] == [231_000]
    assert [mail["to"] for mail in outbox(tmp_path, "mail.jsonl")] == ["orders@flour-mill.example"]
    again = cli(APPROVE, tmp_path, clock, answers=lambda prompt: "y")
    assert "Nothing waits for your yes." in again
    assert "msg-004" not in again
    assert len(outbox(tmp_path, "orders.jsonl")) == 1
    check_every_receipt(tmp_path)
    verified = cli(["receipts", "verify", "--business", SAMPLE], tmp_path, clock)
    assert "0 failed." in verified


def test_a_no_sends_and_orders_nothing(tmp_path: Path) -> None:
    clock = FixedClock(T0)
    cli(["keys", "new", "--business", SAMPLE], tmp_path, clock)
    cli(RUN, tmp_path, clock)
    shown = cli(APPROVE, tmp_path, clock, answers=lambda prompt: "")
    assert "No; nothing will be done." in shown
    assert outbox(tmp_path, "orders.jsonl") == []
    assert outbox(tmp_path, "mail.jsonl") == []
    check_every_receipt(tmp_path)


def test_a_change_that_expires_while_the_owner_reads_it_is_not_done(tmp_path: Path) -> None:
    clock = FixedClock(T0)
    cli(["keys", "new", "--business", SAMPLE], tmp_path, clock)
    cli(RUN, tmp_path, clock)

    def slow_yes(prompt: str) -> str:
        clock.advance(timedelta(hours=49))
        return "y"

    shown = cli(APPROVE, tmp_path, clock, answers=slow_yes)
    assert "It expired before your answer" in shown
    assert outbox(tmp_path, "orders.jsonl") == []


def test_an_expired_approval_is_refused_at_apply_time(tmp_path: Path) -> None:
    clock = FixedClock(T0)
    cli(["keys", "new", "--business", SAMPLE], tmp_path, clock)
    cli(RUN, tmp_path, clock)
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    [change] = ledger.changes(SAMPLE)
    decide(
        ledger, change.change_id, yes=True, decided_by="the owner", channel=TERMINAL, clock=clock
    )
    ledger.close()
    clock.advance(timedelta(hours=49))
    shown = cli(APPROVE, tmp_path, clock)
    assert f"Not applied {change.change_id}: Refused by the expiry gate" in shown
    assert outbox(tmp_path, "orders.jsonl") == []
    check_every_receipt(tmp_path)


def test_the_reply_sent_is_byte_for_byte_the_reply_shown(tmp_path: Path) -> None:
    clock = FixedClock(T0)
    cli(["keys", "new", "--business", SAMPLE], tmp_path, clock)
    long_line = "Please deliver to the back door, where the flour store is. " * 5
    reply = f"Six bags,​ please.\n{long_line.strip()}\nThank you."
    cli(RUN, tmp_path, clock, reorder={**GOOD_REORDER, "reply": reply})
    shown = cli(APPROVE, tmp_path, clock, answers=lambda prompt: "y")
    printed = shown.split("Reply to orders@flour-mill.example:\n", 1)[1].split("\nYes.\n", 1)[0]
    [mail] = outbox(tmp_path, "mail.jsonl")
    assert str(mail["body"]).encode("utf-8") == printed.encode("utf-8")
    assert printed == reply.replace("​", "")
    assert max(len(line) for line in printed.splitlines()) > 200


def test_a_change_a_stopped_pass_left_is_listed_and_not_redone(tmp_path: Path) -> None:
    clock = FixedClock(T0)
    cli(["keys", "new", "--business", SAMPLE], tmp_path, clock)
    cli(RUN, tmp_path, clock)
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    [change] = ledger.changes(SAMPLE)
    approval = decide(
        ledger, change.change_id, yes=True, decided_by="the owner", channel=TERMINAL, clock=clock
    )
    ledger.claim(change.change_id, approval_id=approval.approval_id, now=clock.now())
    ledger.close()
    shown = cli(APPROVE, tmp_path, clock)
    assert f"Not finished {change.change_id}:" in shown
    assert outbox(tmp_path, "orders.jsonl") == []
    check_every_receipt(tmp_path)
