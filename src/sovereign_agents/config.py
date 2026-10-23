"""The kit's settings, read from the environment.

``SOVEREIGN_AGENTS_MODEL`` names the model; when it is unset no model is passed and the
engine's default applies. The spend caps are whole micro-dollars. Of ``ANTHROPIC_API_KEY``,
which the host's secret store puts in the environment, the settings keep only whether it is
set; the key itself stays in the environment, where the CLI reads it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sovereign_agents.kit import RunBudget

from .paths import Home

DEFAULT_RUN_CAP_MICROS = 500_000
DEFAULT_OVERRUN_MARGIN_MICROS = 250_000
DEFAULT_DAY_CAP_MICROS = 3_000_000


def _micros(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "")
    if raw == "":
        return default
    if not raw.isdigit():
        raise ValueError(f"{name} must be a whole number of micro-dollars, not {raw!r}")
    return int(raw)


@dataclass(frozen=True)
class KitConfig:
    home: Home
    model: str | None
    budget: RunBudget
    api_key_set: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> KitConfig:
        return cls(
            home=Home.from_env(env),
            model=env.get("SOVEREIGN_AGENTS_MODEL") or None,
            api_key_set=bool(env.get("ANTHROPIC_API_KEY")),
            budget=RunBudget(
                run_cap_micros=_micros(
                    env, "SOVEREIGN_AGENTS_RUN_CAP_MICROS", DEFAULT_RUN_CAP_MICROS
                ),
                day_cap_micros=_micros(
                    env, "SOVEREIGN_AGENTS_DAY_CAP_MICROS", DEFAULT_DAY_CAP_MICROS
                ),
                overrun_margin_micros=_micros(
                    env, "SOVEREIGN_AGENTS_MARGIN_MICROS", DEFAULT_OVERRUN_MARGIN_MICROS
                ),
            ),
        )
