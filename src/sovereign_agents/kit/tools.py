# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""Each job's tools, defined once and served the same way by every engine.

A tool is a name, a description, a JSON Schema for its input, a kind and an async handler that
talks only to the core (gates, ledger) and the job's own connectors. A tool reads or stages;
there is no kind that applies, and a ToolSet refuses to hold one. ``ToolSet.call`` is the one
way in: it refuses a name off the list and input that breaks the schema, records each refusal
through ``on_refusal``, and only then runs the handler. An engine adapter wraps ``call``; it
never calls a handler itself. ``ToolSet.check_input`` runs the same two checks on their own,
for an engine whose tool server validates input before it calls a handler: the engine asks it
first, so that refusal is recorded like any other.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import Draft202012Validator

from .gates import INPUT, TOOLS, Refusal

ToolKind = Literal["read", "stage"]
TOOL_KINDS: frozenset[str] = frozenset({"read", "stage"})


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False


ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolResult]]
RefusalSink = Callable[[str, Refusal], None]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    kind: ToolKind
    handler: ToolHandler


class ToolSet:
    """One job's tools for one run."""

    def __init__(
        self,
        namespace: str,
        tools: tuple[ToolSpec, ...],
        on_refusal: RefusalSink | None = None,
    ) -> None:
        names = [spec.name for spec in tools]
        if len(set(names)) != len(names):
            raise ValueError(f"tool names repeat in {namespace}: {names}")
        for spec in tools:
            if spec.kind not in TOOL_KINDS:
                raise ValueError(f"{spec.name} is a {spec.kind} tool; a tool only reads or stages")
            Draft202012Validator.check_schema(spec.input_schema)
        self.namespace = namespace
        self.tools = tools
        self._by_name = {spec.name: spec for spec in tools}
        self._validators = {spec.name: Draft202012Validator(spec.input_schema) for spec in tools}
        self._on_refusal = on_refusal

    def names(self) -> list[str]:
        return [spec.name for spec in self.tools]

    def check_input(self, name: str, arguments: Mapping[str, Any]) -> Refusal | None:
        """The tools and input gates for one call, recorded through ``on_refusal``.

        None when ``name`` is on the list and ``arguments`` keep its schema; otherwise the
        refusal, which ``call`` would also give.
        """
        if name not in self._by_name:
            refusal = Refusal(TOOLS, f"{name} is not one of this job's tools")
        else:
            errors = sorted(
                error.message for error in self._validators[name].iter_errors(arguments)
            )
            if not errors:
                return None
            refusal = Refusal(INPUT, "; ".join(errors))
        if self._on_refusal is not None:
            self._on_refusal(name, refusal)
        return refusal

    async def call(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        refusal = self.check_input(name, arguments)
        if refusal is not None:
            return ToolResult(str(refusal), is_error=True)
        return await self._by_name[name].handler(dict(arguments))
