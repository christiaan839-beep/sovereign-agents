# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sovereign_agents.kit.approvals import ApplyReport, apply_approved, decide, pending
from sovereign_agents.kit.clock import FixedClock
from sovereign_agents.kit.gates import APPROVAL, CAPS, EXPIRY, INPUT, TERMINAL
from sovereign_agents.kit.ledger import Change, Ledger, PayloadTooDeep
from sovereign_agents.kit.receipts import Signer, load_signer, new_keys
from sovereign_agents.kit.runner import RunBudget, Runner
from sovereign_agents.kit.testing import ScriptedCall, ScriptedEngine, assert_receipt_valid
from tests.helpers import finishes_within, refuse_receipt_writes
from tests.kit.mail_job import MailApplier, Mailroom, OrderCaps, build_mail_job

T0 = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
BUDGET = RunBudget(run_cap_micros=500_000, day_cap_micros=3_000_000, overrun_margin_micros=250_000)


@dataclass
class Rig:
    ledger: Ledger
    signer: Signer
    clock: FixedClock
    room: Mailroom

    def stage(self, amount_cents: int = 3000) -> str:
        engine = ScriptedEngine(
            [
                ScriptedCall("read_inbox"),
                ScriptedCall("list_contacts"),
                ScriptedCall(
                    "stage_order",
                    {
                        "contact_id": "vendor-1",
                        "source_message_id": "m-1",
                        "amount_cents": amount_cents,
                        "note": "Two widgets.",
                    },
                ),
            ]
        )
        runner = Runner(
            ledger=self.ledger, signer=self.signer, clock=self.clock, budget=BUDGET, engine=engine
        )
        ctx = runner.begin("example_mail")
        result = asyncio.run(runner.run(ctx, build_mail_job(ctx, self.room)))
        [change_id] = result.staged
        return change_id

    def apply(self, caps: OrderCaps | None = None) -> Any:
        applier = MailApplier(self.room, caps or OrderCaps())
        return apply_approved(
            ledger=self.ledger,
            signer=self.signer,
            clock=self.clock,
            appliers={"example_mail": applier},
            sample=True,
        )

    def yes(self, change_id: str) -> None:
        decide(
            self.ledger, change_id, yes=True, decided_by="owner", channel=TERMINAL, clock=self.clock
        )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    new_keys(tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    yield Rig(ledger, load_signer("example-shop", tmp_path / "keys"), FixedClock(T0), Mailroom())
    ledger.close()


def test_a_yes_applies_once_with_a_signed_action_receipt(rig: Rig) -> None:
    change_id = rig.stage()
    assert [c.change_id for c in pending(rig.ledger, "example-shop", rig.clock)] == [change_id]
    rig.yes(change_id)
    report = rig.apply()
    [(applied_id, receipt_id)] = report.applied
    assert applied_id == change_id
    assert rig.room.sent[0]["to"] == "orders@vendor.example"
    [action] = rig.ledger.receipts("example-shop", kind="action")
    assert action["id"] == receipt_id
    assert action["approval"]["channel"] == TERMINAL
    assert action["applyRunId"] == report.run_id
    assert action["action"]["amountCents"] == 3000
    assert_receipt_valid(action, rig.signer.public_pem)
    assert report.receipt["runKind"] == "apply"
    assert report.receipt["applied"] == [change_id]
    assert (report.unfinished, report.receipt["unfinished"]) == ((), [])
    assert_receipt_valid(report.receipt, rig.signer.public_pem)
    assert rig.ledger.get_change(change_id).status == "applied"
    again = rig.apply()
    assert again.applied == ()
    assert len(rig.room.sent) == 1


def test_a_no_applies_nothing(rig: Rig) -> None:
    change_id = rig.stage()
    decide(rig.ledger, change_id, yes=False, decided_by="owner", channel=TERMINAL, clock=rig.clock)
    assert rig.apply().applied == ()
    assert rig.room.sent == []
    assert rig.ledger.get_change(change_id).status == "refused"


def test_only_a_host_channel_records_a_decision(rig: Rig) -> None:
    change_id = rig.stage()
    with pytest.raises(ValueError, match="not one of the host's approval channels"):
        decide(
            rig.ledger, change_id, yes=True, decided_by="agent", channel="model", clock=rig.clock
        )
    assert rig.ledger.get_change(change_id).status == "staged"


def test_pending_leaves_out_changes_past_their_expiry(rig: Rig) -> None:
    change_id = rig.stage()
    rig.clock.advance(timedelta(hours=24))
    assert pending(rig.ledger, "example-shop", rig.clock) == []
    assert rig.ledger.get_change(change_id).status == "expired"


def test_an_expired_approval_is_refused_at_apply_time(rig: Rig) -> None:
    change_id = rig.stage()
    rig.yes(change_id)
    rig.clock.advance(timedelta(hours=25))
    report = rig.apply()
    assert report.applied == ()
    [(refused_id, refusal)] = report.refused
    assert (refused_id, refusal.gate) == (change_id, EXPIRY)
    assert rig.ledger.get_change(change_id).status == "expired"
    assert rig.room.sent == []
    [record] = rig.ledger.refusals(run_id=report.run_id)
    assert (record.change_id, record.gate, record.tool) == (change_id, EXPIRY, "apply")
    assert report.receipt["refusals"][0]["gate"] == EXPIRY
    assert_receipt_valid(report.receipt, rig.signer.public_pem)


def test_caps_are_checked_again_at_apply_time(rig: Rig) -> None:
    change_id = rig.stage(amount_cents=3000)
    rig.yes(change_id)
    report = rig.apply(OrderCaps(max_order_cents=2000))
    assert report.refused[0][1].gate == CAPS
    assert rig.ledger.get_change(change_id).status == "refused"
    assert rig.room.sent == []


def test_a_forged_approval_is_refused(rig: Rig, tmp_path: Path) -> None:
    change_id = rig.stage()
    raw = sqlite3.connect(tmp_path / "ledger.sqlite3", isolation_level=None)
    raw.execute("UPDATE changes SET status = 'approved' WHERE change_id = ?", (change_id,))
    raw.execute(
        "INSERT INTO approvals (approval_id, change_id, decision, decided_by, channel, decided_at)"
        " VALUES ('apr-forged', ?, 'yes', 'agent', 'model', '2026-10-16T08:00:00Z')",
        (change_id,),
    )
    raw.close()
    report = rig.apply()
    assert report.refused[0][1].gate == APPROVAL
    assert "not one of the host's approval channels" in report.refused[0][1].reason
    assert rig.room.sent == []


class FailingApplier(MailApplier):
    def apply(self, change: Change) -> dict[str, Any]:
        raise RuntimeError("the outbox is down")


def test_an_applier_failure_still_closes_and_receipts_the_pass(rig: Rig) -> None:
    # The applier raised, so whether its side effect happened is unknown: the change stays
    # applying and the pass lists it as unfinished, never applying it again.
    change_id = rig.stage()
    rig.yes(change_id)
    with pytest.raises(RuntimeError, match="outbox is down"):
        apply_approved(
            ledger=rig.ledger,
            signer=rig.signer,
            clock=rig.clock,
            appliers={"example_mail": FailingApplier(rig.room)},
        )
    assert rig.ledger.get_change(change_id).status == "applying"
    assert rig.ledger.receipts("example-shop", kind="action") == []
    pass_receipt = rig.ledger.receipts("example-shop", kind="run")[-1]
    assert (pass_receipt["runKind"], pass_receipt["outcome"]) == ("apply", "error: RuntimeError")
    assert (pass_receipt["applied"], pass_receipt["unfinished"]) == ([], [change_id])
    assert_receipt_valid(pass_receipt, rig.signer.public_pem)
    later = rig.apply()
    assert (later.applied, later.unfinished) == ((), (change_id,))
    assert rig.room.sent == []


@dataclass
class RacingApplier(MailApplier):
    """Runs a second apply pass in the middle of this one: while it checks the caps (before
    the claim) or while it applies (after the claim)."""

    second_pass: Callable[[], ApplyReport] | None = None
    during: str = "apply"
    reports: list[ApplyReport] = field(default_factory=list)

    def _race(self, moment: str) -> None:
        if self.during == moment and self.second_pass is not None:
            run_second, self.second_pass = self.second_pass, None
            self.reports.append(run_second())

    def cap_violations(self, change: Change) -> list[str]:
        self._race("caps")
        return super().cap_violations(change)

    def apply(self, change: Change) -> dict[str, Any]:
        self._race("apply")
        return super().apply(change)


@pytest.mark.parametrize("during", ["caps", "apply"])
def test_two_passes_at_once_apply_a_change_once(rig: Rig, tmp_path: Path, during: str) -> None:
    change_id = rig.stage()
    rig.yes(change_id)

    def second_pass() -> ApplyReport:
        other = Ledger(tmp_path / "ledger.sqlite3")
        try:
            return apply_approved(
                ledger=other,
                signer=rig.signer,
                clock=rig.clock,
                appliers={"example_mail": MailApplier(rig.room)},
                sample=True,
            )
        finally:
            other.close()

    racing = RacingApplier(rig.room, second_pass=second_pass, during=during)
    first = apply_approved(
        ledger=rig.ledger,
        signer=rig.signer,
        clock=rig.clock,
        appliers={"example_mail": racing},
        sample=True,
    )
    [second] = racing.reports
    assert len(rig.room.sent) == 1
    assert [c for c, _ in first.applied + second.applied] == [change_id]
    assert len(rig.ledger.receipts("example-shop", kind="action")) == 1
    assert rig.ledger.get_change(change_id).status == "applied"
    if during == "apply":
        assert (first.applied[0][0], second.unfinished) == (change_id, (change_id,))
    else:
        assert (second.applied[0][0], first.unfinished) == (change_id, ())


def test_a_change_with_no_applier_is_skipped(rig: Rig) -> None:
    change_id = rig.stage()
    rig.yes(change_id)
    report = apply_approved(ledger=rig.ledger, signer=rig.signer, clock=rig.clock, appliers={})
    assert report.skipped == (change_id,)
    assert rig.ledger.get_change(change_id).status == "approved"


def _nested(depth: int) -> list[Any]:
    """A list nested ``depth`` deep: ``[]`` is 1, ``[[]]`` is 2."""
    value: list[Any] = []
    for _ in range(depth - 1):
        value = [value]
    return value


@dataclass
class EchoApplier:
    """Applies a change of the "deep" job by recording it, and returns ``result``."""

    result: Any = field(default_factory=lambda: {"ref": "done-1"})
    done: list[str] = field(default_factory=list)
    cap_checks: list[str] = field(default_factory=list)

    def cap_violations(self, change: Change) -> list[str]:
        self.cap_checks.append(change.change_id)
        return []

    def apply(self, change: Change) -> dict[str, Any]:
        self.done.append(change.change_id)
        return self.result  # type: ignore[no-any-return]


def _stage_deep(rig: Rig, payload: dict[str, Any], key: str = "e" * 64) -> str:
    rig.ledger.begin_run(
        run_id=f"run-{key[:4]}",
        business="example-shop",
        job="deep",
        kind="agent",
        engine="test-engine",
        now=T0,
        reserve_micros=0,
        day_cap_micros=None,
    )
    change, _ = rig.ledger.stage_change(
        run_id=f"run-{key[:4]}",
        business="example-shop",
        job="deep",
        kind="deep",
        idempotency_key=key,
        payload=payload,
        summary="a deep change",
        now=T0,
        ttl=timedelta(hours=48),
    )
    rig.ledger.end_run(f"run-{key[:4]}", outcome="success", cost_micros=0, duration_ms=0, now=T0)
    rig.yes(change.change_id)
    return change.change_id


def _apply_deep(rig: Rig, applier: EchoApplier) -> ApplyReport:
    return apply_approved(
        ledger=rig.ledger, signer=rig.signer, clock=rig.clock, appliers={"deep": applier}
    )


def _store_payload(tmp_path: Path, change_id: str, payload: Any) -> None:
    """Rewrite a change's stored payload, as a ledger from before the depth check could hold."""
    with closing(sqlite3.connect(tmp_path / "ledger.sqlite3")) as db, db:
        db.execute(
            "UPDATE changes SET payload = ? WHERE change_id = ?", (json.dumps(payload), change_id)
        )


def test_a_payload_at_the_depth_allowance_stages_applies_and_signs(rig: Rig) -> None:
    # 63 deep as its own level 1, so 64 deep at level 2 of the action receipt: the limit.
    payload = {"deep": _nested(62)}
    change_id = _stage_deep(rig, payload)
    applier = EchoApplier()
    report = _apply_deep(rig, applier)
    assert applier.done == [change_id]
    [action] = rig.ledger.receipts("example-shop", kind="action")
    assert action["action"] == payload
    assert_receipt_valid(action, rig.signer.public_pem)
    assert rig.ledger.get_change(change_id).status == "applied"
    assert (report.unfinished, report.unrecorded) == ((), ())


def test_a_stored_payload_too_deep_to_sign_is_refused_before_job_code_sees_it(
    rig: Rig, tmp_path: Path
) -> None:
    change_id = _stage_deep(rig, {"deep": []})
    _store_payload(tmp_path, change_id, {"deep": _nested(63)})
    applier = EchoApplier()
    report = _apply_deep(rig, applier)
    assert (applier.cap_checks, applier.done) == ([], [])
    [(refused_id, refusal)] = report.refused
    assert (refused_id, refusal.gate) == (change_id, INPUT)
    assert "action receipt" in refusal.reason
    assert rig.ledger.get_change(change_id).status == "refused"
    assert rig.ledger.receipts("example-shop", kind="action") == []
    assert_receipt_valid(report.receipt, rig.signer.public_pem)


def test_a_yes_on_a_payload_too_deep_to_sign_is_refused_at_decision_time(
    rig: Rig, tmp_path: Path
) -> None:
    rig.ledger.begin_run(
        run_id="run-1",
        business="example-shop",
        job="deep",
        kind="agent",
        engine="test-engine",
        now=T0,
        reserve_micros=0,
        day_cap_micros=None,
    )
    change, _ = rig.ledger.stage_change(
        run_id="run-1",
        business="example-shop",
        job="deep",
        kind="deep",
        idempotency_key="f" * 64,
        payload={"deep": []},
        summary="a deep change",
        now=T0,
        ttl=timedelta(hours=48),
    )
    _store_payload(tmp_path, change.change_id, {"deep": _nested(63)})
    with pytest.raises(PayloadTooDeep):
        rig.yes(change.change_id)
    assert rig.ledger.get_change(change.change_id).status == "staged"
    decide(
        rig.ledger,
        change.change_id,
        yes=False,
        decided_by="owner",
        channel=TERMINAL,
        clock=rig.clock,
    )
    assert rig.ledger.get_change(change.change_id).status == "refused"


SENTINEL = "sk-ant-sentinel-0000"


def _cyclic() -> dict[str, Any]:
    value: dict[str, Any] = {"token": SENTINEL}
    value["self"] = value
    return value


@pytest.mark.parametrize(
    ("result", "why"),
    [
        ({"token": SENTINEL, "deep": _nested(63)}, "nests deeper than the action receipt allows"),
        ({"token": SENTINEL, "total": 1.5}, "holds a value a receipt cannot carry"),
        (_cyclic(), "nests deeper than the action receipt allows"),
        ([SENTINEL], "is not a JSON object"),
    ],
    ids=["too-deep", "float", "cyclic", "not-a-mapping"],
)
def test_a_result_the_receipt_cannot_carry_is_receipted_as_not_recorded(
    rig: Rig, result: Any, why: str
) -> None:
    # The applier returned, so its side effect happened: the change is applied and its
    # action receipt says the result could not be recorded, instead of leaving no receipt.
    change_id = _stage_deep(rig, {"deep": []})
    applier = EchoApplier(result=result)
    with finishes_within(5):
        report = _apply_deep(rig, applier)
    assert applier.done == [change_id]
    [action] = rig.ledger.receipts("example-shop", kind="action")
    assert report.applied == ((change_id, action["id"]),)
    assert action["result"] is None
    assert why in action["resultNotRecorded"]
    assert SENTINEL not in json.dumps(action)
    assert report.unrecorded == ((change_id, action["resultNotRecorded"]),)
    assert_receipt_valid(action, rig.signer.public_pem)
    assert rig.ledger.get_change(change_id).status == "applied"
    assert (report.unfinished, report.receipt["outcome"]) == ((), "success")
    assert_receipt_valid(report.receipt, rig.signer.public_pem)


def test_a_failed_action_receipt_write_leaves_the_change_applying(rig: Rig, tmp_path: Path) -> None:
    change_id = _stage_deep(rig, {"deep": []})
    refuse_receipt_writes(tmp_path / "ledger.sqlite3", "action")
    with pytest.raises(sqlite3.IntegrityError, match="disk full"):
        _apply_deep(rig, EchoApplier())
    assert rig.ledger.get_change(change_id).status == "applying"
    assert rig.ledger.receipts("example-shop", kind="action") == []
    pass_receipt = rig.ledger.receipts("example-shop", kind="run")[-1]
    assert (pass_receipt["outcome"], pass_receipt["applied"]) == ("error: IntegrityError", [])
    assert pass_receipt["unfinished"] == [change_id]
    assert_receipt_valid(pass_receipt, rig.signer.public_pem)


def test_a_failed_run_receipt_write_leaves_the_run_open(rig: Rig, tmp_path: Path) -> None:
    # A run is marked ended only together with its signed run receipt.
    refuse_receipt_writes(tmp_path / "ledger.sqlite3", "run")
    with pytest.raises(sqlite3.IntegrityError, match="disk full"):
        _apply_deep(rig, EchoApplier())
    with closing(sqlite3.connect(tmp_path / "ledger.sqlite3")) as db:
        rows = db.execute("SELECT ended_at FROM runs WHERE kind = 'apply'").fetchall()
    assert rows == [(None,)]


@dataclass
class RefusingRacer:
    """Refuses every change on the caps, after running a second pass that refuses it first."""

    second_pass: Callable[[], ApplyReport] | None = None
    reports: list[ApplyReport] = field(default_factory=list)

    def cap_violations(self, change: Change) -> list[str]:
        if self.second_pass is not None:
            run_second, self.second_pass = self.second_pass, None
            self.reports.append(run_second())
        return ["over the cap"]

    def apply(self, change: Change) -> dict[str, Any]:
        raise AssertionError("a refused change is never applied")


def test_two_passes_that_refuse_a_change_record_one_refusal(rig: Rig, tmp_path: Path) -> None:
    change_id = _stage_deep(rig, {"deep": []})

    def second_pass() -> ApplyReport:
        other = Ledger(tmp_path / "ledger.sqlite3")
        try:
            return apply_approved(
                ledger=other, signer=rig.signer, clock=rig.clock, appliers={"deep": RefusingRacer()}
            )
        finally:
            other.close()

    racer = RefusingRacer(second_pass=second_pass)
    first = apply_approved(
        ledger=rig.ledger, signer=rig.signer, clock=rig.clock, appliers={"deep": racer}
    )
    [second] = racer.reports
    assert [c for c, _ in second.refused] == [change_id]
    assert first.refused == ()
    assert rig.ledger.get_change(change_id).status == "refused"
    refusals = rig.ledger.refusals(run_id=second.run_id) + rig.ledger.refusals(run_id=first.run_id)
    assert [r.change_id for r in refusals] == [change_id]
    assert_receipt_valid(first.receipt, rig.signer.public_pem)


class ChangingMapping(Mapping[str, Any]):
    """A mapping that is not a dict, and gives a float from its second read on."""

    def __init__(self) -> None:
        self.reads = 0

    def __getitem__(self, key: str) -> Any:
        self.reads += 1
        return 1 if self.reads == 1 else 1.5

    def __iter__(self) -> Iterator[str]:
        return iter(["n"])

    def __len__(self) -> int:
        return 1


def test_the_result_signed_is_the_copy_that_was_checked(rig: Rig) -> None:
    change_id = _stage_deep(rig, {"deep": []})
    report = _apply_deep(rig, EchoApplier(result=ChangingMapping()))
    [action] = rig.ledger.receipts("example-shop", kind="action")
    assert action["result"] == {"n": 1}
    assert "resultNotRecorded" not in action
    assert report.applied == ((change_id, action["id"]),)
    assert_receipt_valid(action, rig.signer.public_pem)
