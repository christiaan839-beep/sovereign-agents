# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema.exceptions import SchemaError

from sovereign_agents.kit.clock import FixedClock
from sovereign_agents.kit.gates import INPUT, PROVENANCE, TOOLS, Refusal
from sovereign_agents.kit.job import RunContext
from sovereign_agents.kit.ledger import Ledger
from sovereign_agents.kit.tools import ToolResult, ToolSet, ToolSpec

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"word": {"type": "string", "maxLength": 10}},
    "required": ["word"],
    "additionalProperties": False,
}


async def echo(arguments: dict[str, Any]) -> ToolResult:
    return ToolResult(f"echo {arguments['word']}")


ECHO = ToolSpec("echo", "Echo a word.", SCHEMA, "read", echo)


def tool_set(sink: list[tuple[str, Refusal]] | None = None) -> ToolSet:
    if sink is None:
        return ToolSet("demo", (ECHO,))
    return ToolSet("demo", (ECHO,), on_refusal=lambda tool, refusal: sink.append((tool, refusal)))


def test_a_valid_call_reaches_the_handler() -> None:
    assert asyncio.run(tool_set().call("echo", {"word": "hi"})) == ToolResult("echo hi")
    assert tool_set().names() == ["echo"]


def test_a_tool_off_the_list_is_refused_and_recorded() -> None:
    sink: list[tuple[str, Refusal]] = []
    result = asyncio.run(tool_set(sink).call("apply_change", {}))
    assert result.is_error
    assert sink == [("apply_change", Refusal(TOOLS, "apply_change is not one of this job's tools"))]


def test_input_that_breaks_the_schema_is_refused_and_recorded() -> None:
    sink: list[tuple[str, Refusal]] = []
    result = asyncio.run(tool_set(sink).call("echo", {"word": "hi", "to": "someone@else.example"}))
    assert result.is_error
    assert sink[0][1].gate == INPUT
    assert "'to' was unexpected" in sink[0][1].reason


def test_a_refusal_without_a_sink_still_refuses() -> None:
    assert asyncio.run(tool_set().call("echo", {})).is_error


def test_check_input_records_the_refusal_call_would_give() -> None:
    sink: list[tuple[str, Refusal]] = []
    tools = tool_set(sink)
    assert tools.check_input("echo", {"word": "hi"}) is None
    off_list = tools.check_input("apply_change", {})
    bad_input = tools.check_input("echo", {"word": "hi", "to": "someone@else.example"})
    assert off_list == Refusal(TOOLS, "apply_change is not one of this job's tools")
    assert bad_input is not None
    assert bad_input.gate == INPUT
    assert sink == [("apply_change", off_list), ("echo", bad_input)]


def test_a_tool_set_holds_only_read_and_stage_tools() -> None:
    apply_tool = ToolSpec("apply_change", "Apply.", SCHEMA, "apply", echo)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="only reads or stages"):
        ToolSet("demo", (apply_tool,))


def test_tool_names_do_not_repeat() -> None:
    with pytest.raises(ValueError, match="repeat"):
        ToolSet("demo", (ECHO, ECHO))


def test_a_broken_schema_is_refused() -> None:
    broken = ToolSpec("bad", "Bad.", {"type": "nothing"}, "read", echo)
    with pytest.raises(SchemaError):
        ToolSet("demo", (broken,))


def test_a_run_context_records_refusals_in_the_ledger(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    clock = FixedClock(datetime(2026, 10, 16, 8, 0, tzinfo=UTC))
    ledger.begin_run(
        run_id="run-1",
        business="example-shop",
        job="example_job",
        kind="agent",
        engine="test-engine",
        now=clock.now(),
        reserve_micros=0,
        day_cap_micros=None,
    )
    ctx = RunContext("run-1", "example-shop", ledger, clock)
    result = ctx.refused("stage_order", Refusal(PROVENANCE, "message msg-9 was not read"))
    assert result == ToolResult(
        "Refused by the provenance gate: message msg-9 was not read", is_error=True
    )
    [record] = ledger.refusals(run_id="run-1")
    assert (record.tool, record.gate, record.business) == (
        "stage_order",
        PROVENANCE,
        "example-shop",
    )
    ledger.close()
