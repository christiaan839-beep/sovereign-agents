"""What every test shares: afterwards, each receipt it left in a ledger verifies."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sovereign_agents.kit import PUBLIC_KEY_FILE
from sovereign_agents.kit.testing import assert_ledger_receipts_valid


@pytest.fixture(autouse=True)
def every_receipt_verifies(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[None]:
    """After the test, check each receipt in each ledger under ``tmp_path`` with the Python
    and Go verifiers, under the public keys the test wrote there. A test that stores receipts
    that must fail is marked ``forged_receipts``.
    """
    yield
    if request.node.get_closest_marker("forged_receipts") is not None:
        return
    public_pems = [path.read_bytes() for path in sorted(tmp_path.rglob(PUBLIC_KEY_FILE))]
    for ledger_path in sorted(tmp_path.rglob("*.sqlite3")):
        assert_ledger_receipts_valid(ledger_path, public_pems)
