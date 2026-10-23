# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""Time, injected so tests can move it.

Every time the kit stores or signs is UTC with whole seconds, written ``YYYY-MM-DDTHH:MM:SSZ``.
The spend day is the UTC date.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """The wall clock, in UTC, cut to whole seconds."""

    def now(self) -> datetime:
        return datetime.now(UTC).replace(microsecond=0)


class FixedClock:
    """A clock that moves only when told to."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware start")
        self._now = start.astimezone(UTC).replace(microsecond=0)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


def iso(moment: datetime) -> str:
    """``moment`` in UTC as ``YYYY-MM-DDTHH:MM:SSZ``."""
    if moment.tzinfo is None:
        raise ValueError("iso() needs a timezone-aware datetime")
    return moment.astimezone(UTC).strftime(ISO_FORMAT)


def parse_iso(text: str) -> datetime:
    """The inverse of :func:`iso`."""
    return datetime.strptime(text, ISO_FORMAT).replace(tzinfo=UTC)


def day_key(moment: datetime) -> str:
    """The spend day ``moment`` falls on: its UTC date, ``YYYY-MM-DD``."""
    return iso(moment)[:10]
