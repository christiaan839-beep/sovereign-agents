# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

import pytest

from sovereign_agents.kit.money import format_micros, micros_to_usd, usd_to_micros


def test_cost_rounds_up_to_whole_micro_dollars() -> None:
    assert usd_to_micros(0.0123456789) == 12346
    assert usd_to_micros(0.1) == 100_000
    assert usd_to_micros(0.000_000_1) == 1
    assert usd_to_micros(0.0) == 0
    assert usd_to_micros(2) == 2_000_000


def test_no_cost_reported_counts_as_zero() -> None:
    assert usd_to_micros(None) == 0


@pytest.mark.parametrize("bad", [-0.01, float("nan"), float("inf")])
def test_cost_must_be_finite_and_not_negative(bad: float) -> None:
    with pytest.raises(ValueError):
        usd_to_micros(bad)


def test_cost_must_be_a_number() -> None:
    with pytest.raises(TypeError):
        usd_to_micros(True)
    with pytest.raises(TypeError):
        usd_to_micros("0.5")  # type: ignore[arg-type]


def test_budget_for_the_sdk() -> None:
    assert micros_to_usd(500_000) == 0.5


@pytest.mark.parametrize("bad", [0, -1, True])
def test_budget_must_be_positive_whole_micros(bad: int) -> None:
    with pytest.raises(ValueError):
        micros_to_usd(bad)


def test_format_micros() -> None:
    assert format_micros(12346) == "$0.012346"
    assert format_micros(3_000_000) == "$3.000000"
