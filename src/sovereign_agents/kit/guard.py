# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""The tool guard: an engine asks it before every tool call the model makes.

It logs every call, and refuses any name that is not on the job's list, whatever the model
or the text it read asked for. Each refusal goes to ``on_deny``, which the runner points at
the ledger.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .fence import sanitize_label
from .gates import TOOLS, Refusal

logger = logging.getLogger("sovereign_agents.kit.guard")


@dataclass(frozen=True)
class ToolCall:
    tool: str
    allowed: bool


class ToolGuard:
    def __init__(
        self,
        allowed: Iterable[str],
        on_deny: Callable[[str, Refusal], None] | None = None,
    ) -> None:
        self._allowed = frozenset(allowed)
        self._on_deny = on_deny
        self.calls: list[ToolCall] = []

    def check(self, tool: str) -> Refusal | None:
        """None when ``tool`` is on the job's list; the refusal, recorded, when it is not."""
        allowed = tool in self._allowed
        self.calls.append(ToolCall(tool, allowed))
        logger.info(
            "tool call %s: %s", sanitize_label(tool, 120), "allowed" if allowed else "denied"
        )
        if allowed:
            return None
        refusal = Refusal(TOOLS, f"{sanitize_label(tool, 120)} is not one of this job's tools")
        if self._on_deny is not None:
            self._on_deny(tool, refusal)
        return refusal
