"""Supplier inbox + reorders: read supplier emails and stock, stage reorders and replies."""

from __future__ import annotations

from importlib.resources import files

from sovereign_agents.kit import Job, RunContext

from ...connectors.business import Business
from ..spec import JobSpec
from .apply import ReorderApplier
from .caps import NAME, ReorderCaps
from .tools import FENCE, build_tools

PROMPT = files(__name__).joinpath("prompt.md").read_text("utf-8")
TASK = "Go through the supplier inbox now and stage the reorders that are needed."


def build_job(ctx: RunContext, business: Business, caps: ReorderCaps | None = None) -> Job:
    return Job(
        name=NAME,
        system_prompt=f"{PROMPT.strip()}\n\n{FENCE.notice}",
        task_prompt=TASK,
        tools=build_tools(ctx, business, caps or ReorderCaps()),
        max_turns=20,
    )


SPEC = JobSpec(name=NAME, build=build_job, applier=ReorderApplier)

__all__ = ["FENCE", "NAME", "PROMPT", "SPEC", "TASK", "build_job"]
