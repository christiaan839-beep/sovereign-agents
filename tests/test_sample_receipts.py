"""The published sample receipts verify, and only a public key is published with them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sovereign_agents.kit import verify

FOLDER = Path(__file__).resolve().parent.parent / "samples" / "sample-bakery-receipts"


def _receipts() -> list[dict[str, object]]:
    receipts: list[dict[str, object]] = json.loads((FOLDER / "receipts.json").read_text("utf-8"))
    return receipts


def test_the_folder_holds_the_receipts_and_a_public_key_only() -> None:
    assert sorted(path.name for path in FOLDER.iterdir()) == [
        "README.md",
        "ed25519.pem",
        "receipts.json",
    ]
    assert (FOLDER / "ed25519.pem").read_text("ascii").startswith("-----BEGIN PUBLIC KEY-----")


@pytest.mark.parametrize("receipt", _receipts(), ids=lambda r: str(r["id"]))
def test_each_published_receipt_verifies(receipt: dict[str, object]) -> None:
    assert verify(receipt, (FOLDER / "ed25519.pem").read_bytes()).ok


@pytest.mark.forged_receipts
def test_a_changed_published_receipt_fails() -> None:
    receipt = dict(_receipts()[1])
    receipt["canonical"] = str(receipt["canonical"]).replace('"job"', '"jab"', 1)
    assert not verify(receipt, (FOLDER / "ed25519.pem").read_bytes()).ok
