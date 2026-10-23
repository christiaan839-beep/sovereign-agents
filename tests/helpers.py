"""Helpers the kit's tests share: the sample business and the job rig."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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
