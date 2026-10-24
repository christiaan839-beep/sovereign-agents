"""What the job's tests share: the sample bakery and a rig for the supplier reorders job."""

from __future__ import annotations

import asyncio
import signal
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

from sovereign_agents.connectors.business import Business, load_sample_business
from sovereign_agents.jobs.supplier_reorders import build_job
from sovereign_agents.kit import FixedClock, Job, Ledger, RunContext, ToolResult

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
SAMPLE = "sample-bakery"


def sample_business(outbox_dir: Path, samples_root: Path = SAMPLES) -> Business:
    """The made-up sample bakery, with its outbox in ``outbox_dir``."""
    return load_sample_business(samples_root, SAMPLE, outbox_dir)


@contextmanager
def finishes_within(seconds: float) -> Iterator[None]:
    """Raise TimeoutError in the block once it has run ``seconds`` (SIGALRM, so Unix only)."""

    def expire(signum: int, frame: FrameType | None) -> None:
        raise TimeoutError(f"still running after {seconds} s")

    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def refuse_receipt_writes(ledger_path: Path, kind: str, action: str = "ABORT") -> None:
    """Make the ledger file refuse every new receipt of ``kind``, as a full disk would.

    ``action`` is the trigger's RAISE action: ABORT fails the statement, ROLLBACK also ends
    the transaction, as SQLite does itself on some errors.
    """
    with closing(sqlite3.connect(ledger_path)) as db, db:
        db.execute(
            f"CREATE TRIGGER no_{kind}_receipts BEFORE INSERT ON receipts"
            f" WHEN NEW.kind = '{kind}' BEGIN SELECT RAISE({action}, 'disk full'); END"
        )


def allow_receipt_writes(ledger_path: Path, kind: str) -> None:
    with closing(sqlite3.connect(ledger_path)) as db, db:
        db.execute(f"DROP TRIGGER no_{kind}_receipts")


T0 = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
GOOD_REORDER: dict[str, Any] = {
    "supplier_id": "sup-flour",
    "source_message_id": "msg-001",
    "lines": [{"sku": "FLR-WHITE-25", "quantity": 6, "unit_price_cents": 38_500}],
    "reply": "Please send 6 bags of white bread flour on Monday. Thank you.",
}


@dataclass
class JobRig:
    """The supplier reorders job on the sample bakery, in one open run, with no model."""

    ledger: Ledger
    clock: FixedClock
    business: Business
    ctx: RunContext
    job: Job

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        return asyncio.run(self.job.tools.call(tool, arguments or {}))

    def read_everything(self) -> None:
        for tool in ("read_supplier_inbox", "get_stock_levels", "list_suppliers"):
            assert not self.call(tool).is_error


def supplier_rig(tmp_path: Path, run_id: str = "run-1") -> JobRig:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    clock = FixedClock(T0)
    ledger.begin_run(
        run_id=run_id,
        business=SAMPLE,
        job="supplier_reorders",
        kind="agent",
        engine="test",
        now=clock.now(),
        reserve_micros=0,
        day_cap_micros=None,
    )
    business = sample_business(tmp_path / "outbox")
    ctx = RunContext(run_id, SAMPLE, ledger, clock, sample=True)
    return JobRig(ledger, clock, business, ctx, build_job(ctx, business))
