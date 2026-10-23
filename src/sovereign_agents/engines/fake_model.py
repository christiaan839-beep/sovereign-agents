"""A stand-in for the model, for the demo and the tests: no network, no API key, no cost.

``FakeModel`` takes the place of the SDK's ``query`` function. It plays a fixed list of tool
calls the way the CLI does: each call goes first to the engine's ``PreToolUse`` hook and,
when the hook allows it, through the SDK's own bridge to the job's in-process tool server.
So a fake run meets the same options, guard, input checks, tools, gates and ledger as a real
one. Only the choice of calls is fixed.

``FakeModelEngine`` is the Agent SDK engine with that stand-in. Its name, ``fake-model``, is
what the run receipt records, so a fake run never passes for a real one.

The bridge is a private SDK class (``claude_agent_sdk._internal.sdk_mcp_bridge``); the SDK is
pinned exactly in ``pyproject.toml``, and the engine's tests check the bridge's behaviour.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk import ClaudeAgentOptions, Message, ResultMessage
from claude_agent_sdk._internal.sdk_mcp_bridge import SdkMcpBridge
from claude_agent_sdk.types import McpSdkServerConfig, PreToolUseHookInput

from .agent_sdk import AgentSdkEngine, sdk_tool_name

_INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "fake-model", "version": "0"},
    },
}
_INITIALIZED: dict[str, Any] = {"jsonrpc": "2.0", "method": "notifications/initialized"}


@dataclass(frozen=True)
class FakeCall:
    """One tool call the fake model makes. ``tool`` is the job's own name for the tool
    (``stage_reorder``) or, for a call off the job's list, any other name (``Bash``)."""

    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FakeReply:
    """What came back for one call: the hook's refusal, or the tool server's reply."""

    tool: str
    denied: str | None
    text: str
    is_error: bool


class FakeModel:
    """A ``QueryFn`` that plays ``calls`` through the hook and the in-process server."""

    def __init__(self, calls: Sequence[FakeCall]) -> None:
        self.calls = tuple(calls)
        self.replies: list[FakeReply] = []

    async def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        if options.hooks is None or not isinstance(options.mcp_servers, dict):
            raise ValueError("the fake model needs the engine's hooks and in-process server")
        if len(options.mcp_servers) != 1:
            raise ValueError("the fake model expects exactly one in-process server, the job's")
        hook = options.hooks["PreToolUse"][0].hooks[0]
        [namespace] = options.mcp_servers
        server = cast(McpSdkServerConfig, options.mcp_servers[namespace])
        bridge = SdkMcpBridge(server["name"], server["instance"])
        await bridge.handle(_INITIALIZE)
        await bridge.handle(_INITIALIZED)
        try:
            for number, call in enumerate(self.calls, start=1):
                qualified = sdk_tool_name(namespace, call.tool)
                name = qualified if qualified in options.allowed_tools else call.tool
                event: PreToolUseHookInput = {
                    "hook_event_name": "PreToolUse",
                    "tool_name": name,
                    "tool_input": call.arguments,
                    "tool_use_id": f"fake-{number}",
                    "session_id": "fake-session",
                    "transcript_path": "",
                    "cwd": "",
                }
                decision = await hook(event, f"fake-{number}", {"signal": None})
                output = decision.get("hookSpecificOutput")
                if isinstance(output, dict) and output.get("permissionDecision") == "deny":
                    reason = str(output.get("permissionDecisionReason", "denied"))
                    self.replies.append(FakeReply(call.tool, reason, reason, True))
                    continue
                reply = await bridge.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": number,
                        "method": "tools/call",
                        "params": {"name": call.tool, "arguments": call.arguments},
                    }
                )
                result = (reply or {}).get("result", {})
                content = result.get("content") or [{"text": ""}]
                self.replies.append(
                    FakeReply(
                        call.tool,
                        None,
                        str(content[0].get("text", "")),
                        bool(result.get("isError")),
                    )
                )
        finally:
            await bridge.aclose()
        yield ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=len(self.calls),
            session_id="fake-session",
            total_cost_usd=0.0,
            result="A fake run: the calls were fixed in advance and no model was asked.",
        )


class FakeModelEngine(AgentSdkEngine):
    """The Agent SDK engine with ``FakeModel`` in place of the CLI. It needs no API key,
    because it never calls the API, and it records itself as ``fake-model``."""

    name = "fake-model"

    def __init__(self, *, config_root: Path, calls: Sequence[FakeCall]) -> None:
        self.model = FakeModel(calls)
        super().__init__(config_root=config_root, api_key_set=True, query_fn=self.model)


FAKE_MODEL_FILE = "fake-model.json"


def load_fake_calls(samples_root: Path, business: str, job: str) -> list[FakeCall]:
    """The calls a sample business's fake model makes for one job, from
    ``<samples_root>/<business>/fake-model.json``: ``{"jobs": {"<job>": [{"tool": ...,
    "arguments": {...}}, ...]}}``. Only a sample business has one."""
    path = samples_root / business / FAKE_MODEL_FILE
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{business} has no {FAKE_MODEL_FILE}; only a sample has one") from exc
    calls = data.get("jobs", {}).get(job) if isinstance(data, dict) else None
    if not isinstance(calls, list):
        raise ValueError(f"{path} holds no calls for the job {job}")
    out: list[FakeCall] = []
    for entry in calls:
        tool = entry.get("tool") if isinstance(entry, dict) else None
        arguments = entry.get("arguments", {}) if isinstance(entry, dict) else None
        if not isinstance(tool, str) or not isinstance(arguments, dict):
            raise ValueError(f'{path}: each call is {{"tool": str, "arguments": object}}')
        out.append(FakeCall(tool, arguments))
    return out
