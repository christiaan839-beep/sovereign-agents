# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

import pytest

from sovereign_agents.kit.names import check_slug


@pytest.mark.parametrize("bad", ["", "Shop", "../x", "a/b", "-lead", "a" * 64])
def test_a_slug_is_safe_in_a_path(bad: str) -> None:
    with pytest.raises(ValueError, match="not a business slug"):
        check_slug(bad)


def test_a_good_slug_passes() -> None:
    assert check_slug("example-shop") == "example-shop"
    assert check_slug("a" * 63) == "a" * 63
