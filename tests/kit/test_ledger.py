# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sovereign_agents.kit.ledger import (
    RECEIPT_MAX_NESTING,
    ChangeExpired,
    DayCapReached,
    Ledger,
    NotFound,
    PayloadTooDeep,
    WrongStatus,
    nesting,
)
from tests.helpers import finishes_within

T0 = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
TTL = timedelta(hours=48)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    book = Ledger(tmp_path / "ledger.sqlite3")
    yield book
    book.close()


def start(ledger: Ledger, run_id: str = "run-1", now: datetime = T0) -> None:
    ledger.begin_run(
        run_id=run_id,
        business="biz",
        job="job",
        kind="agent",
        engine="test-engine",
        now=now,
        reserve_micros=0,
        day_cap_micros=None,
    )


def stage(ledger: Ledger, key: str = "a" * 64, run_id: str = "run-1") -> str:
    change, _ = ledger.stage_change(
        run_id=run_id,
        business="biz",
        job="job",
        kind="order",
        idempotency_key=key,
        payload={"totalCents": 1000},
        summary="an order",
        now=T0,
        ttl=TTL,
    )
    return change.change_id


def test_a_run_starts_and_ends_with_engine_cost_and_duration(ledger: Ledger) -> None:
    run = ledger.begin_run(
        run_id="run-1",
        business="biz",
        job="job",
        kind="agent",
        engine="agent-sdk",
        now=T0,
        reserve_micros=750_000,
        day_cap_micros=3_000_000,
    )
    assert (run.engine, run.day, run.ended_at, run.cost_micros) == (
        "agent-sdk",
        "2026-10-16",
        None,
        None,
    )
    assert ledger.committed_micros("biz", "2026-10-16") == 750_000
    ended = ledger.end_run(
        "run-1", outcome="success", cost_micros=12_346, duration_ms=4_200, now=T0
    )
    assert (ended.outcome, ended.cost_micros, ended.duration_ms) == ("success", 12_346, 4_200)
    assert ended.ended_at == T0
    assert ledger.spent_micros("biz", "2026-10-16") == 12_346
    assert ledger.committed_micros("biz", "2026-10-16") == 12_346
    assert ledger.spent_micros("biz", "2026-10-17") == 0


def test_spend_adds_up_per_business_and_day(ledger: Ledger) -> None:
    for run_id in ("run-1", "run-2"):
        start(ledger, run_id)
        ledger.end_run(run_id, outcome="success", cost_micros=100, duration_ms=1, now=T0)
    assert ledger.spent_micros("biz", "2026-10-16") == 200
    assert ledger.spent_micros("other", "2026-10-16") == 0


def test_a_run_the_day_cap_cannot_cover_does_not_start(ledger: Ledger) -> None:
    with pytest.raises(DayCapReached) as refused:
        ledger.begin_run(
            run_id="run-1",
            business="biz",
            job="job",
            kind="agent",
            engine="agent-sdk",
            now=T0,
            reserve_micros=3_000_001,
            day_cap_micros=3_000_000,
        )
    assert (refused.value.committed_micros, refused.value.cap_micros) == (0, 3_000_000)
    with pytest.raises(NotFound):
        ledger.get_run("run-1")


def test_open_runs_hold_their_reservation_against_the_day_cap(ledger: Ledger) -> None:
    for run_id in ("run-1", "run-2"):
        ledger.begin_run(
            run_id=run_id,
            business="biz",
            job="job",
            kind="agent",
            engine="agent-sdk",
            now=T0,
            reserve_micros=1_500_000,
            day_cap_micros=3_000_000,
        )
    with pytest.raises(DayCapReached):
        ledger.begin_run(
            run_id="run-3",
            business="biz",
            job="job",
            kind="agent",
            engine="agent-sdk",
            now=T0,
            reserve_micros=1,
            day_cap_micros=3_000_000,
        )


def test_only_an_open_run_can_end(ledger: Ledger) -> None:
    start(ledger)
    ledger.end_run("run-1", outcome="success", cost_micros=1, duration_ms=1, now=T0)
    with pytest.raises(WrongStatus):
        ledger.end_run("run-1", outcome="success", cost_micros=1, duration_ms=1, now=T0)
    with pytest.raises(WrongStatus):
        ledger.end_run("run-x", outcome="success", cost_micros=1, duration_ms=1, now=T0)


def test_the_same_change_staged_twice_is_one_change(ledger: Ledger) -> None:
    start(ledger)
    first, created = ledger.stage_change(
        run_id="run-1",
        business="biz",
        job="job",
        kind="order",
        idempotency_key="b" * 64,
        payload={"lines": [{"sku": "S1", "quantity": 2}], "totalCents": 77_000},
        summary="an order",
        now=T0,
        ttl=TTL,
    )
    assert created
    assert first.change_id.startswith("chg-")
    assert first.status == "staged"
    assert first.expires_at == T0 + TTL
    assert first.payload == {"lines": [{"sku": "S1", "quantity": 2}], "totalCents": 77_000}
    again, created_again = ledger.stage_change(
        run_id="run-1",
        business="biz",
        job="job",
        kind="order",
        idempotency_key="b" * 64,
        payload={"totalCents": 1},
        summary="the same order, worded differently",
        now=T0 + timedelta(hours=1),
        ttl=TTL,
    )
    assert not created_again
    assert again == first


def test_two_businesses_may_stage_under_the_same_key(ledger: Ledger) -> None:
    """The key is unique per business, so the change id must not be made from the key alone."""
    start(ledger)
    ledger.begin_run(
        run_id="run-2",
        business="other-biz",
        job="job",
        kind="agent",
        engine="test-engine",
        now=T0,
        reserve_micros=0,
        day_cap_micros=None,
    )
    ours = stage(ledger, key="c" * 64)
    theirs, created = ledger.stage_change(
        run_id="run-2",
        business="other-biz",
        job="job",
        kind="order",
        idempotency_key="c" * 64,
        payload={"totalCents": 1000},
        summary="an order",
        now=T0,
        ttl=TTL,
    )
    assert created
    assert theirs.change_id != ours
    assert [c.change_id for c in ledger.changes("biz")] == [ours]
    assert [c.change_id for c in ledger.changes("other-biz")] == [theirs.change_id]


def _nested(depth: int) -> list[Any]:
    """A list nested ``depth`` deep: ``[]`` is 1, ``[[]]`` is 2."""
    value: list[Any] = []
    for _ in range(depth - 1):
        value = [value]
    return value


def test_a_payload_too_deep_for_its_action_receipt_is_refused_at_staging(ledger: Ledger) -> None:
    # The payload is level 2 of the action receipt, so as its own level 1 it may nest 63 deep:
    # {"deep": <63 lists>} is 64 deep and would make the receipt 65.
    start(ledger)
    with pytest.raises(PayloadTooDeep, match="nests deeper than 63, the most the action receipt"):
        ledger.stage_change(
            run_id="run-1",
            business="biz",
            job="job",
            kind="order",
            idempotency_key="d" * 64,
            payload={"deep": _nested(63)},
            summary="an order",
            now=T0,
            ttl=TTL,
        )
    assert ledger.changes("biz") == []


def test_a_payload_with_a_float_is_refused(ledger: Ledger) -> None:
    start(ledger)
    with pytest.raises(TypeError, match="float"):
        ledger.stage_change(
            run_id="run-1",
            business="biz",
            job="job",
            kind="order",
            idempotency_key="c" * 64,
            payload={"total": 10.5},
            summary="an order",
            now=T0,
            ttl=TTL,
        )
    assert ledger.changes("biz") == []


def test_a_change_survives_reopening_the_ledger(tmp_path: Path) -> None:
    first = Ledger(tmp_path / "ledger.sqlite3")
    start(first)
    change_id = stage(first)
    first.close()
    second = Ledger(tmp_path / "ledger.sqlite3")
    assert second.get_change(change_id).status == "staged"
    second.close()


def test_unknown_ids_are_not_found(ledger: Ledger) -> None:
    with pytest.raises(NotFound):
        ledger.get_change("chg-missing")
    with pytest.raises(NotFound):
        ledger.get_run("run-missing")
    assert ledger.approval_for("chg-missing") is None


def test_changes_filter_by_status_and_run(ledger: Ledger) -> None:
    start(ledger, "run-1")
    start(ledger, "run-2")
    one = stage(ledger, "1" * 64, "run-1")
    two = stage(ledger, "2" * 64, "run-2")
    ledger.record_decision(one, decision="yes", decided_by="owner", channel="terminal", now=T0)
    assert [c.change_id for c in ledger.changes("biz")] == [one, two]
    assert [c.change_id for c in ledger.changes("biz", status="staged")] == [two]
    assert [c.change_id for c in ledger.changes("biz", run_id="run-1")] == [one]
    assert ledger.changes("biz", status="applied", run_id="run-2") == []


def test_a_yes_approves_and_a_no_refuses(ledger: Ledger) -> None:
    start(ledger)
    yes_id = stage(ledger, "1" * 64)
    no_id = stage(ledger, "2" * 64)
    approval = ledger.record_decision(
        yes_id, decision="yes", decided_by="owner", channel="terminal", now=T0
    )
    assert (approval.decision, approval.channel, approval.used_at) == ("yes", "terminal", None)
    assert ledger.approval_for(yes_id) == approval
    assert ledger.get_change(yes_id).status == "approved"
    ledger.record_decision(no_id, decision="no", decided_by="owner", channel="terminal", now=T0)
    assert ledger.get_change(no_id).status == "refused"


def test_a_decision_needs_a_staged_change(ledger: Ledger) -> None:
    start(ledger)
    change_id = stage(ledger)
    ledger.record_decision(
        change_id, decision="yes", decided_by="owner", channel="terminal", now=T0
    )
    with pytest.raises(WrongStatus):
        ledger.record_decision(
            change_id, decision="yes", decided_by="owner", channel="terminal", now=T0
        )


def test_a_decision_after_expiry_expires_the_change(ledger: Ledger) -> None:
    start(ledger)
    change_id = stage(ledger)
    with pytest.raises(ChangeExpired):
        ledger.record_decision(
            change_id, decision="yes", decided_by="owner", channel="terminal", now=T0 + TTL
        )
    assert ledger.get_change(change_id).status == "expired"
    assert ledger.approval_for(change_id) is None


def test_expire_due_marks_only_staged_changes_past_their_expiry(ledger: Ledger) -> None:
    start(ledger)
    early = stage(ledger, "1" * 64)
    approved = stage(ledger, "3" * 64)
    ledger.record_decision(approved, decision="yes", decided_by="owner", channel="terminal", now=T0)
    late, _ = ledger.stage_change(
        run_id="run-1",
        business="biz",
        job="job",
        kind="order",
        idempotency_key="2" * 64,
        payload={},
        summary="later",
        now=T0 + timedelta(hours=10),
        ttl=TTL,
    )
    assert ledger.expire_due("biz", T0 + TTL) == [early]
    assert ledger.get_change(early).status == "expired"
    assert ledger.get_change(approved).status == "approved"
    assert ledger.get_change(late.change_id).status == "staged"


def test_mark_expired_and_mark_refused_follow_the_status_rules(ledger: Ledger) -> None:
    start(ledger)
    staged = stage(ledger, "1" * 64)
    approved = stage(ledger, "2" * 64)
    ledger.record_decision(approved, decision="yes", decided_by="owner", channel="terminal", now=T0)
    assert ledger.mark_expired(staged, T0).status == "expired"
    with pytest.raises(WrongStatus):
        ledger.mark_expired(staged, T0)
    assert ledger.mark_refused(approved, T0).status == "refused"
    with pytest.raises(WrongStatus):
        ledger.mark_refused(approved, T0)


@pytest.mark.forged_receipts
def test_a_claim_uses_the_approval_once_before_the_change_is_applied(ledger: Ledger) -> None:
    start(ledger)
    change_id = stage(ledger)
    approval = ledger.record_decision(
        change_id, decision="yes", decided_by="owner", channel="terminal", now=T0
    )
    claimed = ledger.claim(change_id, approval_id=approval.approval_id, now=T0)
    assert claimed.status == "applying"
    used = ledger.approval_for(change_id)
    assert used is not None
    assert used.used_at == T0
    with pytest.raises(WrongStatus):
        ledger.claim(change_id, approval_id=approval.approval_id, now=T0)
    receipt = {"id": "rcp-1"}
    applied = ledger.mark_applied_with_receipt(
        change_id, receipt_id="rcp-1", business="biz", receipt=receipt, now=T0
    )
    assert applied.status == "applied"


def test_a_claim_with_another_changes_approval_changes_nothing(ledger: Ledger) -> None:
    start(ledger)
    one = stage(ledger, "1" * 64)
    two = stage(ledger, "2" * 64)
    ledger.record_decision(one, decision="yes", decided_by="owner", channel="terminal", now=T0)
    approval_two = ledger.record_decision(
        two, decision="yes", decided_by="owner", channel="terminal", now=T0
    )
    with pytest.raises(WrongStatus, match="not an unused yes"):
        ledger.claim(one, approval_id=approval_two.approval_id, now=T0)
    assert ledger.get_change(one).status == "approved"
    unused = ledger.approval_for(two)
    assert unused is not None
    assert unused.used_at is None


def test_refusals_are_recorded_per_run(ledger: Ledger) -> None:
    start(ledger)
    refusal = ledger.record_refusal(
        run_id="run-1",
        business="biz",
        tool="stage_order",
        gate="provenance",
        reason="message msg-9 was not read in this run",
        now=T0,
    )
    assert refusal.change_id is None
    assert ledger.refusals(run_id="run-1") == [refusal]
    assert ledger.refusals(run_id="run-2") == []


def test_flags_wait_until_the_owner_has_seen_them(ledger: Ledger) -> None:
    start(ledger)
    flag = ledger.add_flag(
        run_id="run-1", business="biz", source_id="msg-3", note="asks to pay", now=T0
    )
    assert ledger.flags(run_id="run-1") == [flag]
    assert ledger.open_flags("biz") == [flag]
    ledger.mark_flags_seen([flag.flag_id], T0)
    assert ledger.open_flags("biz") == []
    seen = ledger.flags(run_id="run-1")[0]
    assert seen.seen_at == T0


@pytest.mark.forged_receipts
def test_receipts_are_stored_and_filtered(ledger: Ledger) -> None:
    ledger.add_receipt(
        receipt_id="rcp-1",
        business="biz",
        kind="run",
        subject_id="run-1",
        receipt={"id": "rcp-1", "note": "café"},
        now=T0,
    )
    ledger.add_receipt(
        receipt_id="rcp-2",
        business="biz",
        kind="action",
        subject_id="chg-1",
        receipt={"id": "rcp-2"},
        now=T0,
    )
    assert [r["id"] for r in ledger.receipts("biz")] == ["rcp-1", "rcp-2"]
    assert ledger.receipts("biz", kind="action") == [{"id": "rcp-2"}]
    assert ledger.receipts("biz", subject_id="run-1") == [{"id": "rcp-1", "note": "café"}]
    assert ledger.receipts("other") == []


def test_the_schema_refuses_an_unknown_status(tmp_path: Path) -> None:
    book = Ledger(tmp_path / "ledger.sqlite3")
    start(book)
    change_id = stage(book)
    book.close()
    raw = sqlite3.connect(tmp_path / "ledger.sqlite3")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("UPDATE changes SET status = 'sent' WHERE change_id = ?", (change_id,))
    raw.close()


def test_records_made_at_the_same_time_keep_their_order(ledger: Ledger) -> None:
    start(ledger)
    made = [
        ledger.record_refusal(
            run_id="run-1", business="biz", tool=f"tool-{n}", gate="tools", reason="off", now=T0
        ).tool
        for n in range(6)
    ]
    assert [record.tool for record in ledger.refusals(run_id="run-1")] == made


def _claimed(ledger: Ledger, key: str) -> str:
    change_id = stage(ledger, key)
    approval = ledger.record_decision(
        change_id, decision="yes", decided_by="owner", channel="terminal", now=T0
    )
    ledger.claim(change_id, approval_id=approval.approval_id, now=T0)
    return change_id


@pytest.mark.forged_receipts
def test_an_applied_change_and_its_receipt_are_one_write(ledger: Ledger) -> None:
    start(ledger)
    approved = stage(ledger, "1" * 64)
    ledger.record_decision(approved, decision="yes", decided_by="owner", channel="terminal", now=T0)
    with pytest.raises(WrongStatus):
        ledger.mark_applied_with_receipt(
            approved, receipt_id="rcp-1", business="biz", receipt={"id": "rcp-1"}, now=T0
        )
    assert ledger.receipts("biz") == []
    # The receipt's write fails (its id is taken): the change is not marked applied either.
    change_id = _claimed(ledger, "2" * 64)
    ledger.add_receipt(
        receipt_id="rcp-2", business="biz", kind="run", subject_id="run-1", receipt={}, now=T0
    )
    with pytest.raises(sqlite3.IntegrityError):
        ledger.mark_applied_with_receipt(
            change_id, receipt_id="rcp-2", business="biz", receipt={"id": "rcp-2"}, now=T0
        )
    assert ledger.get_change(change_id).status == "applying"
    assert ledger.receipts("biz", kind="action") == []
    receipt = {"id": "rcp-3"}
    ledger.mark_applied_with_receipt(
        change_id, receipt_id="rcp-3", business="biz", receipt=receipt, now=T0
    )
    assert ledger.get_change(change_id).status == "applied"
    assert ledger.receipts("biz", kind="action", subject_id=change_id) == [receipt]
    with pytest.raises(WrongStatus):
        ledger.mark_applied_with_receipt(
            change_id, receipt_id="rcp-4", business="biz", receipt={"id": "rcp-4"}, now=T0
        )
    assert ledger.receipts("biz", kind="action") == [receipt]


def test_a_refusal_and_its_status_are_one_write(ledger: Ledger) -> None:
    start(ledger)
    refused = stage(ledger, "1" * 64)
    expired = stage(ledger, "2" * 64)
    for change_id in (refused, expired):
        ledger.record_decision(
            change_id, decision="yes", decided_by="owner", channel="terminal", now=T0
        )
    record = ledger.refuse_change(
        refused, run_id="run-1", business="biz", gate="caps", reason="over the cap", now=T0
    )
    assert (record.change_id, record.gate, record.tool) == (refused, "caps", "apply")
    assert ledger.get_change(refused).status == "refused"
    ledger.refuse_change(
        expired, run_id="run-1", business="biz", gate="expiry", reason="late", now=T0, expire=True
    )
    assert ledger.get_change(expired).status == "expired"
    # Refused again (by a second pass that read it as approved): no second refusal is kept.
    with pytest.raises(WrongStatus):
        ledger.refuse_change(
            refused, run_id="run-1", business="biz", gate="caps", reason="over", now=T0
        )
    assert [r.change_id for r in ledger.refusals(run_id="run-1")] == [refused, expired]


@pytest.mark.forged_receipts
def test_writes_inside_atomic_commit_together_or_not_at_all(ledger: Ledger) -> None:
    start(ledger)
    with pytest.raises(RuntimeError, match="stop"), ledger.atomic():
        ledger.end_run("run-1", outcome="success", cost_micros=1, duration_ms=1, now=T0)
        raise RuntimeError("stop")
    assert ledger.get_run("run-1").ended_at is None
    with ledger.atomic():
        ledger.end_run("run-1", outcome="success", cost_micros=1, duration_ms=1, now=T0)
        ledger.add_receipt(
            receipt_id="rcp-1", business="biz", kind="run", subject_id="run-1", receipt={}, now=T0
        )
    assert ledger.get_run("run-1").ended_at == T0
    assert ledger.receipts("biz", kind="run") == [{}]


def test_nesting_counts_lists_and_dicts() -> None:
    shared: list[Any] = [[[]]]  # 3 deep, reached at level 2 and at level 4
    assert nesting({"a": shared, "b": [[shared]]}) == 6
    assert nesting({"a": shared, "b": shared}) == 4  # the second is not walked again
    assert (nesting(5), nesting({}), nesting([{}])) == (0, 1, 2)


def test_nesting_stops_on_a_cycle() -> None:
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    cyclic["again"] = [cyclic, cyclic]
    with finishes_within(5):
        assert nesting(cyclic) > RECEIPT_MAX_NESTING
