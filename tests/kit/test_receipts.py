# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from sovereign_agents.kit.ledger import Ledger
from sovereign_agents.kit.receipts import (
    PRIVATE_KEY_FILE,
    PUBLIC_KEY_FILE,
    KeyFileError,
    issue_receipt,
    load_public_pem,
    load_signer,
    new_keys,
    verify,
    verify_all,
)
from sovereign_agents.kit.testing import (
    assert_ledger_receipts_valid,
    assert_receipt_rejected,
    assert_receipt_valid,
    go_verifier,
)

T0 = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)


def test_new_keys_writes_a_private_key_only_its_owner_can_read(tmp_path: Path) -> None:
    keys_dir = tmp_path / "keys" / "example-shop"
    public_path = new_keys(keys_dir)
    assert public_path == keys_dir / PUBLIC_KEY_FILE
    private_path = keys_dir / PRIVATE_KEY_FILE
    assert private_path.stat().st_mode & 0o777 == 0o600
    assert keys_dir.stat().st_mode & 0o777 == 0o700
    assert public_path.read_bytes().startswith(b"-----BEGIN PUBLIC KEY-----")


def test_new_keys_never_overwrites(tmp_path: Path) -> None:
    new_keys(tmp_path / "k")
    before = (tmp_path / "k" / PRIVATE_KEY_FILE).read_bytes()
    with pytest.raises(KeyFileError, match="never overwrite"):
        new_keys(tmp_path / "k")
    assert (tmp_path / "k" / PRIVATE_KEY_FILE).read_bytes() == before


def test_new_keys_refuses_a_directory_inside_a_git_work_tree(tmp_path: Path) -> None:
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(KeyFileError, match="git work tree"):
        new_keys(tmp_path / "repo" / "keys")
    assert not (tmp_path / "repo" / "keys").exists()


def test_each_business_gets_its_own_new_key(tmp_path: Path) -> None:
    one = new_keys(tmp_path / "one").read_bytes()
    two = new_keys(tmp_path / "two").read_bytes()
    assert one != two


def test_the_signer_never_shows_its_private_key(tmp_path: Path) -> None:
    new_keys(tmp_path)
    signer = load_signer("example-shop", tmp_path)
    assert "private_key" not in repr(signer)
    assert signer.public_pem == (tmp_path / PUBLIC_KEY_FILE).read_bytes()


def test_load_signer_needs_a_key(tmp_path: Path) -> None:
    with pytest.raises(KeyFileError, match="make one with new_keys"):
        load_signer("example-shop", tmp_path)


def test_load_signer_refuses_a_key_others_can_read(tmp_path: Path) -> None:
    new_keys(tmp_path)
    os.chmod(tmp_path / PRIVATE_KEY_FILE, 0o640)
    with pytest.raises(KeyFileError, match="chmod 600"):
        load_signer("example-shop", tmp_path)


def test_load_signer_refuses_a_key_of_another_kind(tmp_path: Path) -> None:
    other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path = tmp_path / PRIVATE_KEY_FILE
    path.write_bytes(other)
    os.chmod(path, 0o600)
    with pytest.raises(KeyFileError, match="not an Ed25519"):
        load_signer("example-shop", tmp_path)


def test_load_signer_refuses_a_public_key_that_does_not_match(tmp_path: Path) -> None:
    new_keys(tmp_path / "a")
    new_keys(tmp_path / "b")
    (tmp_path / "a" / PUBLIC_KEY_FILE).write_bytes((tmp_path / "b" / PUBLIC_KEY_FILE).read_bytes())
    with pytest.raises(KeyFileError, match="does not match"):
        load_signer("example-shop", tmp_path / "a")


def test_load_signer_works_without_the_public_file(tmp_path: Path) -> None:
    new_keys(tmp_path)
    expected = (tmp_path / PUBLIC_KEY_FILE).read_bytes()
    (tmp_path / PUBLIC_KEY_FILE).unlink()
    assert load_signer("example-shop", tmp_path).public_pem == expected


def test_the_public_key_loads_only_once_it_is_made(tmp_path: Path) -> None:
    with pytest.raises(KeyFileError, match="no public key"):
        load_public_pem(tmp_path)
    new_keys(tmp_path)
    assert load_public_pem(tmp_path) == (tmp_path / PUBLIC_KEY_FILE).read_bytes()


def test_an_issued_receipt_verifies_and_is_stored(tmp_path: Path) -> None:
    new_keys(tmp_path / "keys")
    signer = load_signer("example-shop", tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    receipt = issue_receipt(
        ledger=ledger,
        signer=signer,
        kind="action",
        subject_id="chg-1",
        body={"changeId": "chg-1", "totalCents": 77_000, "note": "Bestelling — café"},
        now=T0,
    )
    assert receipt["type"] == "sovereign-agents/action"
    assert receipt["business"] == "example-shop"
    assert receipt["createdAt"] == "2026-10-16T08:00:00Z"
    assert receipt["totalCents"] == 77_000
    assert verify(receipt, signer.public_pem).ok
    assert_receipt_valid(receipt, signer.public_pem)
    assert ledger.receipts("example-shop", subject_id="chg-1") == [receipt]
    ledger.close()


def test_a_body_cannot_overwrite_the_receipt_header(tmp_path: Path) -> None:
    new_keys(tmp_path / "keys")
    signer = load_signer("example-shop", tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    receipt = issue_receipt(
        ledger=ledger,
        signer=signer,
        kind="run",
        subject_id="run-1",
        body={"business": "someone-else", "type": "forged"},
        now=T0,
    )
    assert (receipt["business"], receipt["type"]) == ("example-shop", "sovereign-agents/run")
    ledger.close()


def test_a_float_never_reaches_a_receipt(tmp_path: Path) -> None:
    new_keys(tmp_path / "keys")
    signer = load_signer("example-shop", tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    with pytest.raises(TypeError, match="float"):
        issue_receipt(
            ledger=ledger,
            signer=signer,
            kind="run",
            subject_id="run-1",
            body={"costUsd": 0.01},
            now=T0,
        )
    assert ledger.receipts("example-shop") == []
    ledger.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [("totalCents", 1), ("changeId", "chg-2"), ("business", "someone-else")],
)
def test_an_edited_receipt_fails_verification(tmp_path: Path, field: str, value: object) -> None:
    new_keys(tmp_path / "keys")
    signer = load_signer("example-shop", tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    receipt = issue_receipt(
        ledger=ledger,
        signer=signer,
        kind="action",
        subject_id="chg-1",
        body={"changeId": "chg-1", "totalCents": 77_000},
        now=T0,
    )
    edited = dict(receipt, **{field: value})
    assert_receipt_rejected(edited, signer.public_pem)
    canonical_edited = dict(receipt, canonical=receipt["canonical"].replace("77000", "77001"))
    assert_receipt_rejected(canonical_edited, signer.public_pem)
    ledger.close()


def test_a_receipt_with_an_empty_content_hash_fails_verification(tmp_path: Path) -> None:
    # The signature still verifies; only contentHash is emptied. A present contentHash must
    # be the hash of the canonical, so "" is a mismatch, not an absent field.
    new_keys(tmp_path / "keys")
    signer = load_signer("example-shop", tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    receipt = issue_receipt(
        ledger=ledger, signer=signer, kind="run", subject_id="run-1", body={}, now=T0
    )
    emptied = dict(receipt, contentHash="")
    result = verify(emptied, signer.public_pem)
    assert "contentHash mismatch" in (result.reason or "")
    assert_receipt_rejected(emptied, signer.public_pem)
    ledger.close()


@pytest.mark.parametrize("body", [{}, {"contentHash": ""}, {"note": ""}])
def test_the_kit_never_writes_an_empty_content_hash(tmp_path: Path, body: dict[str, str]) -> None:
    new_keys(tmp_path / "keys")
    signer = load_signer("example-shop", tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    receipt = issue_receipt(
        ledger=ledger, signer=signer, kind="run", subject_id="run-1", body=body, now=T0
    )
    digest = hashlib.sha256(receipt["canonical"].encode("utf-8")).hexdigest()
    assert receipt["contentHash"] == f"sha256:{digest}"
    assert "contentHash" not in json.loads(receipt["canonical"])
    assert ledger.receipts("example-shop") == [receipt]
    ledger.close()


@pytest.mark.forged_receipts
def test_verify_all_checks_every_stored_receipt(tmp_path: Path) -> None:
    new_keys(tmp_path / "keys")
    signer = load_signer("example-shop", tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    issue_receipt(ledger=ledger, signer=signer, kind="run", subject_id="run-1", body={}, now=T0)
    ledger.add_receipt(
        receipt_id="rcp-forged",
        business="example-shop",
        kind="run",
        subject_id="run-2",
        receipt={"id": "rcp-forged", "canonical": "{}", "signature": "v2=AAAA"},
        now=T0,
    )
    outcome = {
        receipt_id: result.ok
        for receipt_id, result in verify_all(ledger, "example-shop", signer.public_pem)
    }
    assert len(outcome) == 2
    assert outcome.pop("rcp-forged") is False
    assert list(outcome.values()) == [True]
    ledger.close()


def test_go_verifier_agrees_with_python(tmp_path: Path) -> None:
    if go_verifier() is None:
        pytest.skip("go is not on PATH; the Go verifier check is skipped")
    new_keys(tmp_path / "keys")
    signer = load_signer("example-shop", tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    receipt = issue_receipt(
        ledger=ledger,
        signer=signer,
        kind="run",
        subject_id="run-1",
        body={"runId": "run-1", "costMicros": 12_346, "staged": ["chg-1"]},
        now=T0,
    )
    assert_receipt_valid(receipt, signer.public_pem)
    assert_receipt_rejected(dict(receipt, costMicros=0), signer.public_pem)
    ledger.close()


def test_load_signer_checks_the_slug(tmp_path: Path) -> None:
    new_keys(tmp_path)
    with pytest.raises(ValueError, match="not a business slug"):
        load_signer("../other", tmp_path)


def test_the_ledger_check_needs_a_key_each_receipt_verifies_under(tmp_path: Path) -> None:
    new_keys(tmp_path / "keys")
    signer = load_signer("example-shop", tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    issue_receipt(ledger=ledger, signer=signer, kind="run", subject_id="run-1", body={}, now=T0)
    ledger.close()
    other = new_keys(tmp_path / "other").read_bytes()
    assert_ledger_receipts_valid(tmp_path / "ledger.sqlite3", [other, signer.public_pem])
    with pytest.raises(AssertionError, match="verifies under none of the 1 public key"):
        assert_ledger_receipts_valid(tmp_path / "ledger.sqlite3", [other])
