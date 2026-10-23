from pathlib import Path

import pytest

from sovereign_agents.paths import Home


def test_home_comes_from_the_environment(tmp_path: Path) -> None:
    home = Home.from_env({"SOVEREIGN_AGENTS_HOME": str(tmp_path)})
    assert home.ledger_path == tmp_path / "ledger.sqlite3"
    assert home.keys_dir("sample-bakery") == tmp_path / "keys" / "sample-bakery"
    assert home.outbox_dir("sample-bakery") == tmp_path / "outbox" / "sample-bakery"
    assert home.sdk_config_root == tmp_path / "sdk-config"


def test_home_defaults_to_the_users_home() -> None:
    assert Home.from_env({}).root == Path.home() / ".sovereign-agents"


def test_a_slug_cannot_climb_out_of_the_home(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a business slug"):
        Home(tmp_path).keys_dir("../escape")
