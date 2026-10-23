"""How a job plugs into the kit: it builds its agent for a run and its applier for the host."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sovereign_agents.kit import Applier, Job, RunContext

from ..connectors.business import Business


@dataclass(frozen=True)
class JobSpec:
    name: str
    build: Callable[[RunContext, Business], Job]
    applier: Callable[[Business], Applier]
