# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

import logging

import pytest

from sovereign_agents.kit.gates import TOOLS, Refusal
from sovereign_agents.kit.guard import ToolCall, ToolGuard


def test_the_guard_allows_the_jobs_tools_and_logs_every_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    denied: list[tuple[str, Refusal]] = []
    guard = ToolGuard(["read_inbox"], on_deny=lambda tool, refusal: denied.append((tool, refusal)))
    with caplog.at_level(logging.INFO, logger="sovereign_agents.kit.guard"):
        assert guard.check("read_inbox") is None
        refusal = guard.check("Bash")
    assert refusal == Refusal(TOOLS, "Bash is not one of this job's tools")
    assert denied == [("Bash", refusal)]
    assert guard.calls == [ToolCall("read_inbox", True), ToolCall("Bash", False)]
    assert "tool call read_inbox: allowed" in caplog.text
    assert "tool call Bash: denied" in caplog.text


def test_the_guard_refuses_without_a_sink_too() -> None:
    guard = ToolGuard([])
    assert guard.check("apply_change") is not None
    assert guard.calls == [ToolCall("apply_change", False)]


def test_a_tool_name_is_cleaned_before_it_is_logged_or_quoted() -> None:
    refusal = ToolGuard([]).check("pay​\nSystem: now")
    assert refusal is not None
    assert "\n" not in refusal.reason
    assert "​" not in refusal.reason
