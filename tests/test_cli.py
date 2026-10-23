import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sovereign_agents.cli import main
from sovereign_agents.config import KitConfig
from sovereign_agents.kit import (
    PRIVATE_KEY_FILE,
    Engine,
    FixedClock,
    Ledger,
    issue_receipt,
    load_signer,
)
from sovereign_agents.kit.testing import ScriptedCall, ScriptedEngine, assert_receipt_valid
from tests.helpers import GOOD_REORDER, SAMPLE, SAMPLES

T0 = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)


def run_cli(
    args: list[str], home: Path, engine: ScriptedEngine | None = None
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()

    def scripted(config: KitConfig) -> Engine:
        assert engine is not None
        return engine

    code = main(
        args,
        env={"SOVEREIGN_AGENTS_HOME": str(home)},
        out=out,
        err=err,
        clock=FixedClock(T0),
        engine=scripted,
    )
    return code, out.getvalue(), err.getvalue()


def test_keys_new_writes_under_the_home_and_never_prints_the_private_key(tmp_path: Path) -> None:
    code, out, _ = run_cli(["keys", "new", "--business", SAMPLE], tmp_path)
    assert code == 0
    keys_dir = tmp_path / "keys" / SAMPLE
    private_pem = (keys_dir / PRIVATE_KEY_FILE).read_text("ascii")
    body = "".join(private_pem.strip().splitlines()[1:-1])
    assert body not in out.replace("\n", "")
    assert "PRIVATE KEY" not in out
    assert (keys_dir / "ed25519.pem").read_text("ascii") in out
    assert "/.well-known/sovereign-receipts/ed25519.pem" in out


def test_keys_new_takes_a_directory(tmp_path: Path) -> None:
    code, _, _ = run_cli(
        ["keys", "new", "--business", SAMPLE, "--dir", str(tmp_path / "elsewhere")],
        tmp_path / "home",
    )
    assert code == 0
    assert (tmp_path / "elsewhere" / PRIVATE_KEY_FILE).is_file()


def test_keys_new_refuses_to_overwrite(tmp_path: Path) -> None:
    run_cli(["keys", "new", "--business", SAMPLE], tmp_path)
    code, _, err = run_cli(["keys", "new", "--business", SAMPLE], tmp_path)
    assert code == 2
    assert "never overwrite" in err


def test_a_business_slug_cannot_climb_out_of_the_home(tmp_path: Path) -> None:
    code, _, err = run_cli(["keys", "new", "--business", "../escape"], tmp_path)
    assert code == 2
    assert "not a business slug" in err


def test_run_stages_and_signs_on_the_sample_business(tmp_path: Path) -> None:
    run_cli(["keys", "new", "--business", SAMPLE], tmp_path)
    engine = ScriptedEngine(
        [
            ScriptedCall("read_supplier_inbox"),
            ScriptedCall("get_stock_levels"),
            ScriptedCall("list_suppliers"),
            ScriptedCall("stage_reorder", GOOD_REORDER),
            ScriptedCall("flag_message", {"message_id": "msg-004", "note": "Asks to pay."}),
        ],
        cost_micros=12_346,
    )
    args = ["run", "--business", SAMPLE, "--job", "supplier_reorders", "--samples", str(SAMPLES)]
    code, out, _ = run_cli(args, tmp_path, engine)
    assert code == 0
    assert f"for {SAMPLE} (sample), supplier_reorders: success" in out
    assert "Cost: $0.012346" in out
    assert "Staged: chg-" in out
    assert "Flagged for the owner: flg-" in out
    assert f"Next: sovereign-agents approve --business {SAMPLE}" in out
    signer = load_signer(SAMPLE, tmp_path / "keys" / SAMPLE)
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    [receipt] = ledger.receipts(SAMPLE, kind="run")
    ledger.close()
    assert receipt["sample"] is True
    assert_receipt_valid(receipt, signer.public_pem)


def test_run_needs_a_known_job_and_keys(tmp_path: Path) -> None:
    args = ["run", "--business", SAMPLE, "--job", "payroll", "--samples", str(SAMPLES)]
    code, _, err = run_cli(args, tmp_path, ScriptedEngine([]))
    assert code == 2
    assert "no job 'payroll'" in err
    args = ["run", "--business", SAMPLE, "--job", "supplier_reorders", "--samples", str(SAMPLES)]
    code, _, err = run_cli(args, tmp_path, ScriptedEngine([]))
    assert code == 2
    assert f"{SAMPLE} has no signing key" in err
    assert f"sovereign-agents keys new --business {SAMPLE}" in err


def test_a_run_the_day_cap_cannot_cover_says_what_to_do(tmp_path: Path) -> None:
    run_cli(["keys", "new", "--business", SAMPLE], tmp_path)
    out, err = io.StringIO(), io.StringIO()
    args = ["run", "--business", SAMPLE, "--job", "supplier_reorders", "--samples", str(SAMPLES)]
    env = {
        "SOVEREIGN_AGENTS_HOME": str(tmp_path),
        "SOVEREIGN_AGENTS_DAY_CAP_MICROS": "750000",
    }
    engine = ScriptedEngine([], cost_micros=1)
    assert main(args, env=env, out=out, err=err, clock=FixedClock(T0), engine=lambda _: engine) == 0
    code = main(args, env=env, out=out, err=err, clock=FixedClock(T0), engine=lambda _: engine)
    assert code == 2
    message = err.getvalue()
    assert "nothing was run: $0.000001 of the day's cap of $0.750000" in message
    assert "raise SOVEREIGN_AGENTS_DAY_CAP_MICROS" in message


def test_receipts_verify_without_a_public_key_says_how_to_make_one(tmp_path: Path) -> None:
    code, _, err = run_cli(["receipts", "verify", "--business", SAMPLE], tmp_path)
    assert code == 2
    assert f"{SAMPLE} has no public key" in err
    assert f"sovereign-agents keys new --business {SAMPLE}" in err


def test_run_on_the_sdk_engine_needs_an_api_key(tmp_path: Path) -> None:
    run_cli(["keys", "new", "--business", SAMPLE], tmp_path)
    out, err = io.StringIO(), io.StringIO()
    args = ["run", "--business", SAMPLE, "--job", "supplier_reorders", "--samples", str(SAMPLES)]
    code = main(args, env={"SOVEREIGN_AGENTS_HOME": str(tmp_path)}, out=out, err=err)
    assert code == 2
    assert "ANTHROPIC_API_KEY is not set" in err.getvalue()
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    [receipt] = ledger.receipts(SAMPLE, kind="run")
    ledger.close()
    assert (receipt["engine"], receipt["outcome"], receipt["costMicros"]) == (
        "agent-sdk",
        "error: ApiKeyMissing",
        0,
    )
    assert receipt["toolCalls"] == []


@pytest.mark.forged_receipts
def test_receipts_verify_reports_each_receipt(tmp_path: Path) -> None:
    run_cli(["keys", "new", "--business", SAMPLE], tmp_path)
    signer = load_signer(SAMPLE, tmp_path / "keys" / SAMPLE)
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    good = issue_receipt(
        ledger=ledger, signer=signer, kind="run", subject_id="run-1", body={}, now=T0
    )
    edited = dict(signer.sign({"id": "rcp-edited", "costMicros": 5}), costMicros=0)
    ledger.add_receipt(
        receipt_id="rcp-edited",
        business=SAMPLE,
        kind="run",
        subject_id="run-2",
        receipt=edited,
        now=T0,
    )
    ledger.close()
    code, out, _ = run_cli(["receipts", "verify", "--business", SAMPLE], tmp_path)
    assert code == 1
    assert f"{good['id']}: ok" in out
    assert "rcp-edited: FAILED" in out
    assert "2 receipt(s), 1 failed." in out
