# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""The Engine interface: what runs a job's agent loop, whichever model service it uses.

An engine must:

- ask ``guard.check`` before every tool call, and run a tool only through
  ``job.tools.call`` when the guard allows it;
- give the model ``job.system_prompt`` as its system prompt and ``job.task_prompt`` as the
  message that starts the run, and nothing that lets it reach other tools or settings;
- stop the run at ``budget_micros``, knowing a provider may pass it by about one model call;
- report the cost rounded up to whole micro-dollars, the duration and how the run ended,
  rather than raise, when the run ends in an error result;
- raise :class:`EngineDidNotStart` (or a subclass) only when it failed before any model call,
  so nothing was spent. The runner charges a run whose engine raised anything else its whole
  reservation, since money may have been spent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .guard import ToolGuard
from .job import Job
from .tools import ToolSet


class EngineDidNotStart(Exception):
    """The engine failed before any model call (a missing API key, say): nothing was spent."""


@dataclass(frozen=True)
class EngineRun:
    outcome: str
    cost_micros: int
    duration_ms: int


class Engine(Protocol):
    name: str

    def tool_names(self, tools: ToolSet) -> list[str]:
        """The names this engine's model sees for ``tools``; the guard allows only these."""
        ...

    async def run(self, job: Job, guard: ToolGuard, *, budget_micros: int) -> EngineRun: ...
