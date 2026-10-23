"""One real run of the supplier job on the sample business, with a real key.

It runs only when SOVEREIGN_AGENTS_LIVE=1 and ANTHROPIC_API_KEY are set, with the key put in
the environment by the host's secret store, and costs at most the run cap (set to 0.50 US
dollars here, plus at most one model call past it).
"""

import asyncio
import os
from pathlib import Path

import pytest

from sovereign_agents.connectors.outbox import Outbox
from sovereign_agents.engines.agent_sdk import AgentSdkEngine, sdk_allowed_tools
from sovereign_agents.jobs.supplier_reorders import build_job
from sovereign_agents.kit import Ledger, RunBudget, Runner, SystemClock, load_signer, new_keys
from sovereign_agents.kit.testing import assert_receipt_valid
from tests.helpers import SAMPLE, sample_business

LIVE = os.environ.get("SOVEREIGN_AGENTS_LIVE") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.mark.skipif(not LIVE, reason="set SOVEREIGN_AGENTS_LIVE=1 and ANTHROPIC_API_KEY to run")
def test_one_live_run_stays_within_its_tools_and_applies_nothing(tmp_path: Path) -> None:
    new_keys(tmp_path / "keys")
    signer = load_signer(SAMPLE, tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    business = sample_business(tmp_path / "outbox")
    runner = Runner(
        ledger=ledger,
        signer=signer,
        clock=SystemClock(),
        budget=RunBudget(
            run_cap_micros=500_000, day_cap_micros=1_000_000, overrun_margin_micros=250_000
        ),
        engine=AgentSdkEngine(
            config_root=tmp_path / "sdk-config",
            api_key_set=LIVE,
            model=os.environ.get("SOVEREIGN_AGENTS_MODEL") or None,
        ),
    )
    ctx = runner.begin("supplier_reorders", sample=True)
    job = build_job(ctx, business)
    result = asyncio.run(runner.run(ctx, job))

    allowed = set(sdk_allowed_tools(job.tools))
    assert all(call.tool in allowed for call in result.tool_calls if call.allowed)
    assert result.cost_micros > 0
    assert_receipt_valid(result.receipt, signer.public_pem)
    staged = ledger.changes(SAMPLE, run_id=result.run_id)
    assert all(change.payload["sourceMessageId"] != "msg-004" for change in staged)
    assert Outbox(tmp_path / "outbox" / "orders.jsonl").records() == []
    assert Outbox(tmp_path / "outbox" / "mail.jsonl").records() == []
    ledger.close()
