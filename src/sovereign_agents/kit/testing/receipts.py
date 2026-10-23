# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""Check a receipt with the Python verifier always, and with the Go verifier when go is on PATH.

The Go verifier is built once per process from ``go_verifier/`` next to this file, which pins
verifiable-receipts' Go package by commit in its go.mod and go.sum.
"""

from __future__ import annotations

import atexit
import functools
import json
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from sovereign_matrix.verifiable_receipts import verify_v2_receipt

GO_VERIFIER_DIR = Path(__file__).resolve().parent / "go_verifier"


@functools.cache
def go_verifier() -> Path | None:
    """The Go verifier's binary, built on first use; None when go is not on PATH."""
    go = shutil.which("go")
    if go is None:
        return None
    out_dir = Path(tempfile.mkdtemp(prefix="go-verifier-"))
    atexit.register(shutil.rmtree, out_dir, True)
    binary = out_dir / "verify-go"
    subprocess.run(
        [go, "build", "-buildvcs=false", "-o", str(binary), "."],
        cwd=GO_VERIFIER_DIR,
        check=True,
        capture_output=True,
        timeout=600,
    )
    return binary


def go_verify(receipt: Mapping[str, Any], public_pem: bytes) -> tuple[bool, str] | None:
    """(accepted, output) from the Go verifier, or None when go is not on PATH."""
    binary = go_verifier()
    if binary is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        pem_path = Path(tmp) / "ed25519.pem"
        receipt_path = Path(tmp) / "receipt.json"
        pem_path.write_bytes(public_pem)
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), "utf-8")
        done = subprocess.run(
            [str(binary), str(pem_path), str(receipt_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    if done.returncode not in (0, 1):
        raise RuntimeError(f"the Go verifier failed to run: {done.stderr}")
    return done.returncode == 0, done.stdout.strip()


def assert_receipt_valid(receipt: Mapping[str, Any], public_pem: bytes) -> None:
    """Both verifiers accept the receipt (the Go one when go is on PATH)."""
    result = verify_v2_receipt(receipt, public_pem)
    assert result.ok, result.reason
    go = go_verify(receipt, public_pem)
    if go is not None:
        assert go[0], go[1]


def assert_receipt_rejected(receipt: Mapping[str, Any], public_pem: bytes) -> None:
    """Both verifiers reject the receipt (the Go one when go is on PATH)."""
    assert not verify_v2_receipt(receipt, public_pem).ok
    go = go_verify(receipt, public_pem)
    if go is not None:
        assert not go[0], go[1]


def assert_ledger_receipts_valid(ledger_path: Path, public_pems: Sequence[bytes]) -> None:
    """Every receipt in the ledger file verifies under one of ``public_pems``, in both verifiers.

    The file is opened read-only, so the check changes nothing a test wrote.
    """
    with closing(sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT receipt_id, receipt FROM receipts ORDER BY rowid").fetchall()
    for receipt_id, text in rows:
        receipt = json.loads(text)
        keys = [pem for pem in public_pems if verify_v2_receipt(receipt, pem).ok]
        assert keys, (
            f"receipt {receipt_id} verifies under none of the {len(public_pems)} public key(s)"
        )
        assert_receipt_valid(receipt, keys[0])
