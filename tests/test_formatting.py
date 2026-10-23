from sovereign_agents.formatting import format_cents


def test_format_cents() -> None:
    assert format_cents(123456, "ZAR") == "R1 234.56"
    assert format_cents(5, "ZAR") == "R0.05"
    assert format_cents(-250, "ZAR") == "-R2.50"
    assert format_cents(100, "EUR") == "EUR 1.00"
