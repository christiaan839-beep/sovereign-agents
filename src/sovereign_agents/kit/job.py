# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""What a job is to the core, and what one run gives a job's tools.

A job supplies its prompts, its tools and, for the host's apply step, an :class:`Applier`.
The core knows nothing else about it: no job names, no prices, no caps of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .clock import Clock
from .gates import Reads, Refusal
from .ledger import Change, Ledger
from .tools import ToolResult, ToolSet


@dataclass(frozen=True)
class Job:
    """One job's agent: an explicit system prompt, the message that starts a run, and tools."""

    name: str
    system_prompt: str
    task_prompt: str
    tools: ToolSet
    max_turns: int


@dataclass
class RunContext:
    """One run: its id, the business it is for, and what its tools have read so far.

    A job's tools stage through ``ledger``, check provenance against ``reads`` and record every
    refusal with :meth:`refused`, so the run receipt can list it.
    """

    run_id: str
    business: str
    ledger: Ledger
    clock: Clock
    sample: bool = False
    reads: Reads = field(default_factory=Reads)

    def record_refusal(self, tool: str, refusal: Refusal) -> None:
        self.ledger.record_refusal(
            run_id=self.run_id,
            business=self.business,
            tool=tool,
            gate=refusal.gate,
            reason=refusal.reason,
            now=self.clock.now(),
        )

    def refused(self, tool: str, refusal: Refusal) -> ToolResult:
        """Record ``refusal`` in the ledger and return it as the tool's error result."""
        self.record_refusal(tool, refusal)
        return ToolResult(str(refusal), is_error=True)


class Applier(Protocol):
    """The host's side of a job, run only after the owner's yes.

    ``cap_violations`` re-checks a change against the caps in force now. ``apply`` performs it
    through the job's connectors and returns what to put in the action receipt (references,
    never secrets). It is idempotent on the change's idempotency key.
    """

    def cap_violations(self, change: Change) -> list[str]: ...

    def apply(self, change: Change) -> dict[str, Any]: ...
