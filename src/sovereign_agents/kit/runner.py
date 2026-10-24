# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""The runner: starts a run inside the day's spend cap, hands it to an engine, closes it, and
signs its receipt.

What it guarantees: a run starts only when the day's cap covers the run's own cap plus a
margin for the one model call a provider may run past it; every run that starts is closed in
the ledger with its engine, outcome, cost in micro-dollars and duration, even when the engine
fails; and every closed run gets a signed receipt that lists the tool calls, the changes it
staged, the flags it raised and the refusals the gates made, and, for an apply pass, the
changes it applied and those left unfinished. A run is ended only in the same ledger write
that stores its receipt; when that write fails the run stays open, and the next run of the
business closes it as abandoned once it has been open for ``ABANDON_AFTER``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .clock import Clock, iso
from .engine import Engine, EngineRun
from .guard import ToolCall, ToolGuard
from .job import Job, RunContext
from .ledger import Ledger, WrongStatus, new_id
from .receipts import Signer, issue_receipt

# How long a run may take. The runner stops the engine at this limit and ends the run as
# "timeout"; the apply pass claims no new change past it.
RUN_TIME_LIMIT = timedelta(minutes=30)
# How long a run may stay open before the next run of its business closes it as abandoned:
# four times RUN_TIME_LIMIT, so a live run has ended (or, for an apply pass, stopped claiming)
# long before. Only a single applier call that hangs for over 90 minutes is still live then;
# its action receipt is still stored, and its pass then fails loudly ending its run.
ABANDON_AFTER = timedelta(hours=2)


@dataclass(frozen=True)
class RunBudget:
    """Spend caps in whole micro-dollars, set by whoever deploys the kit."""

    run_cap_micros: int
    day_cap_micros: int
    overrun_margin_micros: int

    def __post_init__(self) -> None:
        for name in ("run_cap_micros", "day_cap_micros", "overrun_margin_micros"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a whole number of micro-dollars, not {value!r}")
        if self.run_cap_micros == 0:
            raise ValueError("run_cap_micros must be more than 0")
        if self.day_cap_micros < self.reserve_micros:
            raise ValueError("the day's cap must cover at least one run and its margin")

    @property
    def reserve_micros(self) -> int:
        """What a run holds against the day's cap while it is open."""
        return self.run_cap_micros + self.overrun_margin_micros


@dataclass(frozen=True)
class RunResult:
    run_id: str
    outcome: str
    cost_micros: int
    duration_ms: int
    staged: tuple[str, ...]
    flags: tuple[str, ...]
    refusals: tuple[str, ...]
    tool_calls: tuple[ToolCall, ...]
    receipt: dict[str, Any]


def sign_run_receipt(
    *,
    ledger: Ledger,
    signer: Signer,
    run_id: str,
    sample: bool | None,
    tool_calls: Sequence[ToolCall] | None,
    now: datetime,
    applied: Sequence[str] | None = (),
    unfinished: Sequence[str] | None = (),
) -> dict[str, Any]:
    """Sign the receipt for a closed run, from what the ledger holds for it.

    ``applied`` and ``unfinished`` are an apply pass's: the changes it applied, and those left
    claimed but not applied when it ended. ``sample``, ``tool_calls``, ``applied`` and
    ``unfinished`` are known only to the process that ran the run; None signs them as null
    (unknown), as for an abandoned run.
    """
    run = ledger.get_run(run_id)
    if (
        run.ended_at is None
        or run.outcome is None
        or run.cost_micros is None
        or run.duration_ms is None
    ):
        raise ValueError(f"run {run_id} is still open; close it before signing its receipt")
    staged = [change.change_id for change in ledger.changes(run.business, run_id=run_id)]
    return issue_receipt(
        ledger=ledger,
        signer=signer,
        kind="run",
        subject_id=run_id,
        body={
            "runId": run.run_id,
            "job": run.job,
            "runKind": run.kind,
            "engine": run.engine,
            "sample": sample,
            "startedAt": iso(run.started_at),
            "endedAt": iso(run.ended_at),
            "outcome": run.outcome,
            "costMicros": run.cost_micros,
            "durationMs": run.duration_ms,
            "toolCalls": None
            if tool_calls is None
            else [{"tool": call.tool, "allowed": call.allowed} for call in tool_calls],
            "staged": staged,
            "applied": None if applied is None else list(applied),
            "unfinished": None if unfinished is None else list(unfinished),
            "flags": [flag.flag_id for flag in ledger.flags(run_id=run_id)],
            "refusals": [
                {"id": r.refusal_id, "tool": r.tool, "gate": r.gate, "changeId": r.change_id}
                for r in ledger.refusals(run_id=run_id)
            ],
        },
        now=now,
    )


def close_abandoned_runs(
    *, ledger: Ledger, signer: Signer, now: datetime, older_than: timedelta = ABANDON_AFTER
) -> list[dict[str, Any]]:
    """Close the business's runs open longer than ``older_than``, and sign their receipts.

    Such a run's process stopped, or failed to write its receipt. Each is closed with outcome
    ``abandoned`` and charged its whole reservation, since its real cost is unknown; its
    receipt signs what the ledger holds and leaves the rest null. Returns the receipts.
    """
    receipts: list[dict[str, Any]] = []
    for run in ledger.open_runs(signer.business):
        if now - run.started_at <= older_than:
            continue
        try:
            with ledger.atomic():
                ledger.end_run(
                    run.run_id,
                    outcome="abandoned",
                    cost_micros=run.reserved_micros,
                    duration_ms=int((now - run.started_at).total_seconds() * 1000),
                    now=now,
                )
                receipts.append(
                    sign_run_receipt(
                        ledger=ledger,
                        signer=signer,
                        run_id=run.run_id,
                        sample=run.sample,
                        tool_calls=None,
                        now=now,
                        applied=None,
                        unfinished=None,
                    )
                )
        except WrongStatus:
            continue  # its own process, or another, closed it after this one read it
    return receipts


class Runner:
    def __init__(
        self,
        *,
        ledger: Ledger,
        signer: Signer,
        clock: Clock,
        budget: RunBudget,
        engine: Engine,
        time_limit: timedelta = RUN_TIME_LIMIT,
    ) -> None:
        self._ledger = ledger
        self._signer = signer
        self._clock = clock
        self._budget = budget
        self._engine = engine
        self._time_limit = time_limit

    def begin(self, job_name: str, *, sample: bool = False) -> RunContext:
        """Open a run for the signer's business, or raise ``DayCapReached``.

        Runs of the business left open past ``ABANDON_AFTER`` are closed as abandoned first.
        """
        close_abandoned_runs(ledger=self._ledger, signer=self._signer, now=self._clock.now())
        run_id = new_id("run")
        self._ledger.begin_run(
            run_id=run_id,
            business=self._signer.business,
            job=job_name,
            kind="agent",
            engine=self._engine.name,
            now=self._clock.now(),
            reserve_micros=self._budget.reserve_micros,
            day_cap_micros=self._budget.day_cap_micros,
            sample=sample,
        )
        return RunContext(run_id, self._signer.business, self._ledger, self._clock, sample=sample)

    async def _run_engine(self, job: Job, guard: ToolGuard) -> EngineRun:
        # asyncio.wait, not wait_for: a TimeoutError the engine raises itself stays its own
        # error, and only the limit makes a "timeout".
        task = asyncio.ensure_future(
            self._engine.run(job, guard, budget_micros=self._budget.run_cap_micros)
        )
        done, _ = await asyncio.wait({task}, timeout=self._time_limit.total_seconds())
        if task in done:
            return task.result()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        limit_ms = int(self._time_limit.total_seconds() * 1000)
        return EngineRun("timeout", self._budget.reserve_micros, limit_ms)

    async def run(self, ctx: RunContext, job: Job) -> RunResult:
        """Run ``job`` in the open run ``ctx``, then close it and sign its receipt.

        When the engine raises, the run is still closed (outcome ``error: <type>``) and
        receipted, and the error is raised again. When the engine is still running at the time
        limit, it is stopped and the run is closed as ``timeout``, charged its reservation (the
        engine never reported its cost), and receipted with the tool calls made so far.
        """
        guard = ToolGuard(self._engine.tool_names(job.tools), on_deny=ctx.record_refusal)
        failure: Exception | None = None
        try:
            report = await self._run_engine(job, guard)
        except Exception as exc:
            failure = exc
            report = EngineRun(f"error: {type(exc).__name__}", 0, 0)
        now = self._clock.now()
        try:
            with self._ledger.atomic():  # a run is ended only together with its receipt
                self._ledger.end_run(
                    ctx.run_id,
                    outcome=report.outcome,
                    cost_micros=report.cost_micros,
                    duration_ms=report.duration_ms,
                    now=now,
                )
                receipt = sign_run_receipt(
                    ledger=self._ledger,
                    signer=self._signer,
                    run_id=ctx.run_id,
                    sample=ctx.sample,
                    tool_calls=guard.calls,
                    now=now,
                )
        except BaseException as write_error:
            if failure is not None:
                raise write_error from failure
            raise
        if failure is not None:
            raise failure
        return RunResult(
            run_id=ctx.run_id,
            outcome=report.outcome,
            cost_micros=report.cost_micros,
            duration_ms=report.duration_ms,
            staged=tuple(receipt["staged"]),
            flags=tuple(receipt["flags"]),
            refusals=tuple(r["id"] for r in receipt["refusals"]),
            tool_calls=tuple(guard.calls),
            receipt=receipt,
        )
