# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""Model spend as whole micro-dollars.

The SDKs report a run's cost as a float of US dollars. The core rounds it up to whole
micro-dollars (millionths of a dollar) and keeps only that integer. The one float it makes is
the run budget it hands an engine that takes nothing else. The amounts a job stages are the
job's own, in whole minor units such as cents; the ledger and the receipts refuse floats.
"""

from __future__ import annotations

import math
from decimal import ROUND_CEILING, Decimal

MICROS_PER_USD = 1_000_000


def usd_to_micros(usd: float | None) -> int:
    """Round a cost in US dollars up to whole micro-dollars. ``None`` counts as nothing spent."""
    if usd is None:
        return 0
    if isinstance(usd, bool) or not isinstance(usd, int | float):
        raise TypeError(f"a cost must be a number, got {type(usd).__name__}")
    if not math.isfinite(usd) or usd < 0:
        raise ValueError(f"a cost must be a finite amount of zero or more, got {usd!r}")
    micros = Decimal(repr(usd)) * MICROS_PER_USD
    return int(micros.to_integral_value(rounding=ROUND_CEILING))


def micros_to_usd(micros: int) -> float:
    """For an engine's run budget only, which takes a float of US dollars."""
    if isinstance(micros, bool) or not isinstance(micros, int) or micros <= 0:
        raise ValueError(
            f"a budget must be a positive whole number of micro-dollars, got {micros!r}"
        )
    return micros / MICROS_PER_USD


def format_micros(micros: int) -> str:
    """``12346`` micro-dollars is ``$0.012346``."""
    whole, part = divmod(micros, MICROS_PER_USD)
    return f"${whole}.{part:06d}"
