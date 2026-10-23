from importlib.metadata import version

import sovereign_agents


def test_the_version_is_the_one_in_pyproject() -> None:
    assert sovereign_agents.__version__ == version("sovereign-agents") == "0.1.0"
