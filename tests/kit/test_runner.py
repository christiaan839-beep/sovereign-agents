# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

import asyncio
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sovereign_agents.kit.clock import FixedClock
from sovereign_agents.kit.engine import EngineRun
from sovereign_agents.kit.gates import TOOLS
from sovereign_agents.kit.guard import ToolGuard
from sovereign_agents.kit.job import Job
from sovereign_agents.kit.ledger import DayCapReached, Ledger
from sovereign_agents.kit.receipts import Signer, load_signer, new_keys
from sovereign_agents.kit.runner import (
    ABANDON_AFTER,
    RunBudget,
    Runner,
    close_abandoned_runs,
    sign_run_receipt,
)
from sovereign_agents.kit.testing import ScriptedCall, ScriptedEngine, assert_receipt_valid
from sovereign_agents.kit.tools import ToolSet
from tests.helpers import allow_receipt_writes, refuse_receipt_writes
from tests.kit.mail_job import Mailroom, build_mail_job

T0 = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
BUDGET = RunBudget(run_cap_micros=500_000, day_cap_micros=3_000_000, overrun_margin_micros=250_000)
STAGE = ScriptedCall(
    "stage_order",
    {"contact_id": "vendor-1", "source_message_id": "m-1", "amount_cents": 3000, "note": "Two."},
)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    book = Ledger(tmp_path / "ledger.sqlite3")
    yield book
    book.close()


@pytest.fixture
def signer(tmp_path: Path) -> Signer:
    new_keys(tmp_path / "keys")
    return load_signer("example-shop", tmp_path / "keys")


def runner(ledger: Ledger, signer: Signer, engine: ScriptedEngine) -> Runner:
    return Runner(ledger=ledger, signer=signer, clock=FixedClock(T0), budget=BUDGET, engine=engine)


def test_a_run_stages_records_and_signs(ledger: Ledger, signer: Signer) -> None:
    engine = ScriptedEngine(
        [ScriptedCall("read_inbox"), ScriptedCall("list_contacts"), STAGE],
        cost_micros=12_346,
    )
    kit = runner(ledger, signer, engine)
    ctx = kit.begin("example_mail", sample=True)
    result = asyncio.run(kit.run(ctx, build_mail_job(ctx, Mailroom())))
    assert (result.outcome, result.cost_micros, result.duration_ms) == ("success", 12_346, 0)
    assert len(result.staged) == 1
    assert engine.results[2].text.startswith("Staged as chg-")
    run = ledger.get_run(result.run_id)
    assert (run.engine, run.outcome, run.cost_micros) == ("scripted", "success", 12_346)
    assert ledger.committed_micros("example-shop", "2026-10-16") == 12_346
    receipt = result.receipt
    assert receipt["type"] == "sovereign-agents/run"
    assert (receipt["sample"], receipt["engine"], receipt["costMicros"]) == (
        True,
        "scripted",
        12_346,
    )
    assert receipt["toolCalls"][0] == {"tool": "read_inbox", "allowed": True}
    assert receipt["staged"] == list(result.staged)
    assert_receipt_valid(receipt, signer.public_pem)
    assert ledger.receipts("example-shop", subject_id=result.run_id) == [receipt]


def test_a_tool_off_the_list_is_refused_recorded_and_receipted(
    ledger: Ledger, signer: Signer
) -> None:
    engine = ScriptedEngine([ScriptedCall("apply_change", {"change_id": "chg-1"})])
    kit = runner(ledger, signer, engine)
    ctx = kit.begin("example_mail")
    result = asyncio.run(kit.run(ctx, build_mail_job(ctx, Mailroom())))
    assert engine.results[0].is_error
    [refusal] = ledger.refusals(run_id=result.run_id)
    assert (refusal.tool, refusal.gate) == ("apply_change", TOOLS)
    assert result.refusals == (refusal.refusal_id,)
    assert result.receipt["toolCalls"] == [{"tool": "apply_change", "allowed": False}]
    assert ledger.changes("example-shop") == []


def test_a_run_the_day_cap_cannot_cover_never_starts(ledger: Ledger, signer: Signer) -> None:
    kit = runner(ledger, signer, ScriptedEngine([]))
    for _ in range(4):
        kit.begin("example_mail")
    with pytest.raises(DayCapReached):
        kit.begin("example_mail")


class BrokenEngine(ScriptedEngine):
    async def run(self, job: Job, guard: ToolGuard, *, budget_micros: int) -> EngineRun:
        guard.check("read_inbox")
        raise RuntimeError("the engine could not start")


def test_an_engine_failure_still_closes_and_receipts_the_run(
    ledger: Ledger, signer: Signer
) -> None:
    kit = runner(ledger, signer, BrokenEngine([]))
    ctx = kit.begin("example_mail")
    with pytest.raises(RuntimeError, match="could not start"):
        asyncio.run(kit.run(ctx, build_mail_job(ctx, Mailroom())))
    run = ledger.get_run(ctx.run_id)
    assert (run.outcome, run.cost_micros) == ("error: RuntimeError", 0)
    assert ledger.committed_micros("example-shop", "2026-10-16") == 0
    [receipt] = ledger.receipts("example-shop", kind="run")
    assert receipt["outcome"] == "error: RuntimeError"
    assert_receipt_valid(receipt, signer.public_pem)


def test_the_engine_gets_the_run_cap_as_its_budget(ledger: Ledger, signer: Signer) -> None:
    seen: list[int] = []

    class Recording(ScriptedEngine):
        async def run(self, job: Job, guard: ToolGuard, *, budget_micros: int) -> EngineRun:
            seen.append(budget_micros)
            return EngineRun("success", 0, 0)

    kit = runner(ledger, signer, Recording([]))
    ctx = kit.begin("example_mail")
    asyncio.run(kit.run(ctx, Job("example_mail", "s", "t", ToolSet("example_mail", ()), 1)))
    assert seen == [500_000]


def test_an_open_run_has_no_receipt_yet(ledger: Ledger, signer: Signer) -> None:
    ctx = runner(ledger, signer, ScriptedEngine([])).begin("example_mail")
    with pytest.raises(ValueError, match="still open"):
        sign_run_receipt(
            ledger=ledger, signer=signer, run_id=ctx.run_id, sample=False, tool_calls=[], now=T0
        )


@pytest.mark.parametrize(
    ("run_cap", "day_cap", "margin", "message"),
    [
        (0, 1, 0, "more than 0"),
        (-1, 1, 0, "whole number"),
        (True, 1, 0, "whole number"),
        (1.5, 3, 0, "whole number"),
        (500_000, 600_000, 250_000, "cover at least one run"),
    ],
)
def test_a_budget_is_whole_micro_dollars_that_fit(
    run_cap: int, day_cap: int, margin: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RunBudget(run_cap_micros=run_cap, day_cap_micros=day_cap, overrun_margin_micros=margin)


def test_a_budget_reserves_the_run_cap_and_its_margin() -> None:
    assert BUDGET.reserve_micros == 750_000


def test_a_run_whose_receipt_cannot_be_written_stays_open(
    ledger: Ledger, signer: Signer, tmp_path: Path
) -> None:
    # A run is ended only together with its receipt; the engine's error is kept as the cause.
    refuse_receipt_writes(tmp_path / "ledger.sqlite3", "run")
    kit = runner(ledger, signer, BrokenEngine([]))
    ctx = kit.begin("example_mail")
    with pytest.raises(sqlite3.IntegrityError, match="disk full") as caught:
        asyncio.run(kit.run(ctx, build_mail_job(ctx, Mailroom())))
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert ledger.get_run(ctx.run_id).ended_at is None
    assert ledger.receipts("example-shop", kind="run") == []
    assert [run.run_id for run in ledger.open_runs("example-shop")] == [ctx.run_id]


def test_an_abandoned_run_is_closed_charged_and_receipted(
    ledger: Ledger, signer: Signer, tmp_path: Path
) -> None:
    one_run = RunBudget(
        run_cap_micros=500_000, day_cap_micros=750_000, overrun_margin_micros=250_000
    )
    clock = FixedClock(T0)
    kit = Runner(
        ledger=ledger, signer=signer, clock=clock, budget=one_run, engine=ScriptedEngine([])
    )
    refuse_receipt_writes(tmp_path / "ledger.sqlite3", "run")
    ctx = kit.begin("example_mail")
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(kit.run(ctx, build_mail_job(ctx, Mailroom())))
    allow_receipt_writes(tmp_path / "ledger.sqlite3", "run")
    # Not yet abandoned: the open run holds its reservation.
    with pytest.raises(DayCapReached):
        kit.begin("example_mail")
    assert ledger.get_run(ctx.run_id).ended_at is None
    clock.advance(ABANDON_AFTER + timedelta(seconds=1))
    # Abandoned: closed and charged its reservation, which fills this day's cap as before.
    with pytest.raises(DayCapReached):
        kit.begin("example_mail")
    run = ledger.get_run(ctx.run_id)
    assert (run.outcome, run.cost_micros) == ("abandoned", 750_000)
    assert ledger.spent_micros("example-shop", "2026-10-16") == 750_000
    [receipt] = ledger.receipts("example-shop", kind="run")
    assert (receipt["runId"], receipt["outcome"], receipt["costMicros"]) == (
        ctx.run_id,
        "abandoned",
        750_000,
    )
    assert receipt["sample"] is None  # the ledger does not know; the receipt does not guess
    assert_receipt_valid(receipt, signer.public_pem)
    clock.advance(timedelta(days=1))
    assert kit.begin("example_mail").run_id != ctx.run_id


def test_a_run_another_process_closed_meanwhile_is_left_alone(
    ledger: Ledger, signer: Signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = runner(ledger, signer, ScriptedEngine([])).begin("example_mail")
    seen_open = ledger.get_run(ctx.run_id)
    ledger.end_run(ctx.run_id, outcome="success", cost_micros=0, duration_ms=0, now=T0)
    monkeypatch.setattr(ledger, "open_runs", lambda business: [seen_open])
    later = T0 + 2 * ABANDON_AFTER
    assert close_abandoned_runs(ledger=ledger, signer=signer, now=later) == []
    assert ledger.get_run(ctx.run_id).outcome == "success"
