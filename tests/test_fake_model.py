"""The fake model: a run through the Agent SDK engine's own hook and tool server, no network."""

import asyncio
import io
import json
import shutil
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, Message

from sovereign_agents.cli import main
from sovereign_agents.connectors.outbox import Outbox
from sovereign_agents.engines.fake_model import (
    FakeCall,
    FakeModel,
    FakeModelEngine,
    load_fake_calls,
)
from sovereign_agents.jobs.supplier_reorders import build_job
from sovereign_agents.kit import (
    CAPS,
    PROVENANCE,
    TOOLS,
    FixedClock,
    Ledger,
    RunBudget,
    Runner,
    load_signer,
    new_keys,
)
from sovereign_agents.kit.testing import assert_receipt_valid
from tests.helpers import GOOD_REORDER, SAMPLE, SAMPLES, sample_business

T0 = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
BUDGET = RunBudget(run_cap_micros=500_000, day_cap_micros=3_000_000, overrun_margin_micros=0)


def test_the_sample_carries_the_fake_models_calls() -> None:
    calls = load_fake_calls(SAMPLES, SAMPLE, "supplier_reorders")
    assert [call.tool for call in calls[:3]] == [
        "read_supplier_inbox",
        "get_stock_levels",
        "list_suppliers",
    ]
    assert calls[-1] == FakeCall(
        "flag_message",
        {
            "message_id": "msg-004",
            "note": (
                "Asks to pay an invoice of R18 400.00 and to write to another address. "
                "Not a supplier on the list."
            ),
        },
    )


def write_sample(tmp_path: Path, fake: object | None) -> Path:
    root = tmp_path / "samples"
    shutil.copytree(SAMPLES / SAMPLE, root / SAMPLE)
    path = root / SAMPLE / "fake-model.json"
    if fake is None:
        path.unlink()
    else:
        path.write_text(json.dumps(fake), "utf-8")
    return root


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (None, "has no fake-model.json; only a sample has one"),
        ({"jobs": {}}, "holds no calls for the job supplier_reorders"),
        ([], "holds no calls for the job supplier_reorders"),
        ({"jobs": {"supplier_reorders": ["read_supplier_inbox"]}}, "each call is"),
        ({"jobs": {"supplier_reorders": [{"tool": 1}]}}, "each call is"),
        ({"jobs": {"supplier_reorders": [{"tool": "x", "arguments": []}]}}, "each call is"),
    ],
)
def test_a_bad_fake_model_file_is_refused(tmp_path: Path, fake: object, message: str) -> None:
    root = write_sample(tmp_path, fake)
    with pytest.raises(ValueError, match=message):
        load_fake_calls(root, SAMPLE, "supplier_reorders")


def run_fake(tmp_path: Path, calls: list[FakeCall]) -> tuple[Runner, FakeModelEngine, Ledger]:
    new_keys(tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    engine = FakeModelEngine(config_root=tmp_path / "sdk-config", calls=calls)
    runner = Runner(
        ledger=ledger,
        signer=load_signer(SAMPLE, tmp_path / "keys"),
        clock=FixedClock(T0),
        budget=BUDGET,
        engine=engine,
    )
    return runner, engine, ledger


def test_a_fake_run_meets_the_same_hook_tools_and_gates_as_a_real_one(tmp_path: Path) -> None:
    runner, engine, ledger = run_fake(
        tmp_path, load_fake_calls(SAMPLES, SAMPLE, "supplier_reorders")
    )
    ctx = runner.begin("supplier_reorders", sample=True)
    result = asyncio.run(runner.run(ctx, build_job(ctx, sample_business(tmp_path / "outbox"))))
    assert (result.outcome, result.cost_micros) == ("success", 0)
    assert len(result.staged) == 2
    assert len(result.flags) == 1
    refused = [(reply.tool, reply.denied is not None) for reply in engine.model.replies]
    assert ("Bash", True) in refused
    gates = [refusal.gate for refusal in ledger.refusals(run_id=result.run_id)]
    assert gates[-1] == TOOLS
    assert set(gates[:-1]) <= {PROVENANCE, CAPS} and gates[:-1]
    assert result.receipt["engine"] == "fake-model"
    assert {"tool": "Bash", "allowed": False} in result.receipt["toolCalls"]
    assert_receipt_valid(result.receipt, load_signer(SAMPLE, tmp_path / "keys").public_pem)
    assert Outbox(tmp_path / "outbox" / "orders.jsonl").records() == []
    ledger.close()


def test_input_the_schema_refuses_never_reaches_the_tool_set(tmp_path: Path) -> None:
    smuggled = {**GOOD_REORDER, "to": "payments@elsewhere.example"}
    runner, engine, ledger = run_fake(
        tmp_path, [FakeCall("read_supplier_inbox"), FakeCall("stage_reorder", smuggled)]
    )
    ctx = runner.begin("supplier_reorders", sample=True)
    result = asyncio.run(runner.run(ctx, build_job(ctx, sample_business(tmp_path / "outbox"))))
    assert list(result.staged) == []
    [read, stage] = engine.model.replies
    assert (read.denied, read.is_error) == (None, False)
    assert read.text.startswith("<supplier_mail")
    assert stage.denied is not None and "('to' was unexpected)" in stage.denied
    ledger.close()


def no_messages() -> Iterator[Message]:
    return iter(())


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (ClaudeAgentOptions(), "needs the engine's hooks"),
        (
            ClaudeAgentOptions(hooks={"PreToolUse": []}, mcp_servers={}),
            "exactly one in-process server",
        ),
    ],
)
def test_the_fake_model_needs_the_engines_hook_and_one_server(
    options: ClaudeAgentOptions, message: str
) -> None:
    async def drain() -> None:
        stream: AsyncIterator[Message] = FakeModel([])(prompt="go", options=options)
        async for _ in stream:
            pass  # pragma: no cover

    with pytest.raises(ValueError, match=message):
        asyncio.run(drain())


def demo(home: Path, answers: list[str], *extra: str) -> tuple[int, str]:
    out = io.StringIO()
    replies = iter(answers)
    code = main(
        ["demo", "--samples", str(SAMPLES), *extra],
        env={"SOVEREIGN_AGENTS_HOME": str(home)},
        out=out,
        err=out,
        ask=lambda prompt: next(replies),
        clock=FixedClock(T0),
    )
    return code, out.getvalue()


def test_the_demo_runs_the_whole_loop_and_every_receipt_verifies(tmp_path: Path) -> None:
    code, shown = demo(tmp_path, ["y", "n"])
    assert code == 0, shown
    assert "Made a new Ed25519 key pair for sample-bakery." in shown
    assert "supplier_reorders: success" in shown
    assert "Refused calls: 2, listed in the run receipt" in shown
    assert "msg-004: Asks to pay an invoice" in shown
    assert shown.count("Applied chg-") == 1
    assert "No; nothing will be done." in shown
    assert "3 receipt(s), 0 failed." in shown
    orders = Outbox(tmp_path / "outbox" / SAMPLE / "orders.jsonl").records()
    assert [order["totalCents"] for order in orders] == [231_000]
    mail = Outbox(tmp_path / "outbox" / SAMPLE / "mail.jsonl").records()
    assert [message["to"] for message in mail] == ["orders@flour-mill.example"]

    code, again = demo(tmp_path, [])
    assert code == 0, again
    assert "already has a key pair; using it." in again
    # The same reorders again are the same changes: the one done and the one declined wait
    # for nothing, and nothing is ordered twice.
    assert "Nothing waits for your yes." in again
    assert "0 failed." in again
    assert len(Outbox(tmp_path / "outbox" / SAMPLE / "orders.jsonl").records()) == 1


def test_run_takes_the_fake_model_as_its_engine(tmp_path: Path) -> None:
    out = io.StringIO()
    env = {"SOVEREIGN_AGENTS_HOME": str(tmp_path)}
    assert main(["keys", "new", "--business", SAMPLE], env=env, out=out) == 0
    args = ["run", "--business", SAMPLE, "--job", "supplier_reorders", "--samples", str(SAMPLES)]
    code = main([*args, "--engine", "fake-model"], env=env, out=out, err=out, clock=FixedClock(T0))
    assert code == 0, out.getvalue()
    assert "supplier_reorders: success" in out.getvalue()
