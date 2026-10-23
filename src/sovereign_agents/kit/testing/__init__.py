# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""Helpers for tests: receipt checks in Python and Go, a scripted engine, hostile texts.

They are part of the public API so that a kit built on the core can test itself the same way
the core does.
"""

from .hostile import HostileText, hostile_mail
from .receipts import (
    assert_ledger_receipts_valid,
    assert_receipt_rejected,
    assert_receipt_valid,
    go_verifier,
    go_verify,
)
from .scripted import ScriptedCall, ScriptedEngine

__all__ = [
    "HostileText",
    "ScriptedCall",
    "ScriptedEngine",
    "assert_ledger_receipts_valid",
    "assert_receipt_rejected",
    "assert_receipt_valid",
    "go_verifier",
    "go_verify",
    "hostile_mail",
]
