# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sovereign_agents.kit.clock import FixedClock, SystemClock, day_key, iso, parse_iso


def test_fixed_clock_moves_only_when_told() -> None:
    clock = FixedClock(datetime(2026, 10, 16, 8, 30, 15, 999, tzinfo=UTC))
    assert clock.now() == datetime(2026, 10, 16, 8, 30, 15, tzinfo=UTC)
    clock.advance(timedelta(hours=2))
    assert clock.now() == datetime(2026, 10, 16, 10, 30, 15, tzinfo=UTC)


def test_fixed_clock_converts_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    clock = FixedClock(datetime(2026, 10, 16, 1, 0, tzinfo=plus_two))
    assert iso(clock.now()) == "2026-10-15T23:00:00Z"


def test_fixed_clock_refuses_a_naive_start() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 10, 16))


def test_system_clock_is_utc_with_whole_seconds() -> None:
    now = SystemClock().now()
    assert now.tzinfo is UTC
    assert now.microsecond == 0


def test_iso_round_trips() -> None:
    moment = datetime(2026, 10, 16, 8, 30, 15, tzinfo=UTC)
    assert iso(moment) == "2026-10-16T08:30:15Z"
    assert parse_iso("2026-10-16T08:30:15Z") == moment


def test_iso_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        iso(datetime(2026, 10, 16))


def test_day_key_is_the_utc_date() -> None:
    assert day_key(datetime(2026, 10, 16, 23, 59, 59, tzinfo=UTC)) == "2026-10-16"
    assert day_key(datetime(2026, 10, 17, 0, 30, tzinfo=timezone(timedelta(hours=2)))) == (
        "2026-10-16"
    )
