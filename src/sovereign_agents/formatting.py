"""How amounts are written for the owner."""

from __future__ import annotations

# How a currency code is written before an amount; anything else is written as its code.
CURRENCY_SYMBOLS = {"ZAR": "R", "USD": "$"}


def format_cents(cents: int, currency: str) -> str:
    """``123456`` in ZAR is ``R1 234.56``: a space between thousands, a point before cents."""
    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}{symbol}{whole:,}.{part:02d}".replace(",", " ")
