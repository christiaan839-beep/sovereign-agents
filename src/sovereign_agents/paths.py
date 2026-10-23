"""Where the kit keeps its state on the host, outside the repo.

``SOVEREIGN_AGENTS_HOME`` (default ``~/.sovereign-agents``) holds the ledger, each business's
keys under ``keys/<business>/``, the sample backends' outboxes under ``outbox/<business>/``,
and under ``sdk-config/`` the empty config folder the Agent SDK engine gives the CLI for each
run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sovereign_agents.kit import check_slug


@dataclass(frozen=True)
class Home:
    root: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Home:
        raw = env.get("SOVEREIGN_AGENTS_HOME")
        return cls(Path(raw).expanduser() if raw else Path.home() / ".sovereign-agents")

    @property
    def ledger_path(self) -> Path:
        return self.root / "ledger.sqlite3"

    @property
    def sdk_config_root(self) -> Path:
        return self.root / "sdk-config"

    def keys_dir(self, business: str) -> Path:
        return self.root / "keys" / check_slug(business)

    def outbox_dir(self, business: str) -> Path:
        return self.root / "outbox" / check_slug(business)
