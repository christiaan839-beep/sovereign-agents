import secrets
from pathlib import Path

import pytest

from sovereign_agents.config import KitConfig


def test_defaults_hold_until_set(tmp_path: Path) -> None:
    config = KitConfig.from_env({"SOVEREIGN_AGENTS_HOME": str(tmp_path)})
    assert config.home.root == tmp_path
    assert config.model is None
    assert config.api_key_set is False
    assert (
        config.budget.run_cap_micros,
        config.budget.overrun_margin_micros,
        config.budget.day_cap_micros,
    ) == (500_000, 250_000, 3_000_000)


def test_settings_come_from_the_environment() -> None:
    dummy_key = "not-a-key-" + secrets.token_hex(4)
    config = KitConfig.from_env(
        {
            "ANTHROPIC_API_KEY": dummy_key,
            "SOVEREIGN_AGENTS_MODEL": "a-model-name",
            "SOVEREIGN_AGENTS_RUN_CAP_MICROS": "200000",
            "SOVEREIGN_AGENTS_DAY_CAP_MICROS": "1000000",
            "SOVEREIGN_AGENTS_MARGIN_MICROS": "100000",
        }
    )
    assert config.api_key_set is True
    assert dummy_key not in repr(config)
    assert config.model == "a-model-name"
    assert config.budget.reserve_micros == 300_000


@pytest.mark.parametrize("bad", ["0.5", "-1", "five"])
def test_a_cap_must_be_whole_micro_dollars(bad: str) -> None:
    with pytest.raises(ValueError, match="whole number of micro-dollars"):
        KitConfig.from_env({"SOVEREIGN_AGENTS_RUN_CAP_MICROS": bad})
