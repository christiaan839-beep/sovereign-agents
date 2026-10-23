# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sovereign_agents.kit.ledger import (
    ChangeExpired,
    DayCapReached,
    Ledger,
    NotFound,
    WrongStatus,
)

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


def test_a_claim_uses_the_approval_once_before_the_change_is_applied(ledger: Ledger) -> None:
    start(ledger)
    change_id = stage(ledger)
    approval = ledger.record_decision(
        change_id, decision="yes", decided_by="owner", channel="terminal", now=T0
    )
    with pytest.raises(WrongStatus):
        ledger.mark_applied(change_id, now=T0)
    claimed = ledger.claim(change_id, approval_id=approval.approval_id, now=T0)
    assert claimed.status == "applying"
    used = ledger.approval_for(change_id)
    assert used is not None
    assert used.used_at == T0
    with pytest.raises(WrongStatus):
        ledger.claim(change_id, approval_id=approval.approval_id, now=T0)
    assert ledger.mark_applied(change_id, now=T0).status == "applied"
    with pytest.raises(WrongStatus):
        ledger.mark_applied(change_id, now=T0)


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
