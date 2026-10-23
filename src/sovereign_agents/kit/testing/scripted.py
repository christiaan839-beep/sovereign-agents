# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""An engine with no model: it plays a fixed list of tool calls through the guard, in order.

The attack suite and a kit's tests use it to act out what a hijacked model would try, with
the same guard, tools, gates and ledger a real engine meets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..engine import EngineRun
from ..guard import ToolGuard
from ..job import Job
from ..tools import ToolResult, ToolSet


@dataclass(frozen=True)
class ScriptedCall:
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


class ScriptedEngine:
    name = "scripted"

    def __init__(
        self,
        calls: Sequence[ScriptedCall],
        *,
        cost_micros: int = 0,
        outcome: str = "success",
    ) -> None:
        self._calls = tuple(calls)
        self._cost_micros = cost_micros
        self._outcome = outcome
        self.results: list[ToolResult] = []

    def tool_names(self, tools: ToolSet) -> list[str]:
        return tools.names()

    async def run(self, job: Job, guard: ToolGuard, *, budget_micros: int) -> EngineRun:
        for call in self._calls:
            refusal = guard.check(call.tool)
            if refusal is not None:
                self.results.append(ToolResult(str(refusal), is_error=True))
                continue
            self.results.append(await job.tools.call(call.tool, call.arguments))
        return EngineRun(self._outcome, self._cost_micros, 0)
