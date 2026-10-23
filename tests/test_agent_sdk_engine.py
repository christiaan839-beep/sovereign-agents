import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookInput,
    HookJSONOutput,
    Message,
    PreToolUseHookInput,
    ResultError,
    ResultMessage,
    UserPromptSubmitHookInput,
)
from claude_agent_sdk._internal.sdk_mcp_bridge import SdkMcpBridge
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import HookCallback, McpSdkServerConfig

from sovereign_agents.engines.agent_sdk import (
    AgentSdkEngine,
    ApiKeyMissing,
    QueryFn,
    guard_hook,
    sdk_allowed_tools,
    sdk_server,
    sdk_tool_name,
    sdk_tools,
)
from sovereign_agents.jobs.supplier_reorders import build_job
from sovereign_agents.kit import (
    INPUT,
    TOOLS,
    FixedClock,
    Job,
    Ledger,
    RunBudget,
    Runner,
    ToolGuard,
    ToolResult,
    ToolSet,
    ToolSpec,
    load_signer,
    new_keys,
)
from sovereign_agents.kit.testing import assert_receipt_valid
from tests.helpers import GOOD_REORDER, SAMPLE, T0, sample_business, supplier_rig

PREFIX = "mcp__supplier_reorders__"
NAMES = [
    "mcp__supplier_reorders__read_supplier_inbox",
    "mcp__supplier_reorders__get_stock_levels",
    "mcp__supplier_reorders__list_suppliers",
    "mcp__supplier_reorders__stage_reorder",
    "mcp__supplier_reorders__flag_message",
]
SMUGGLED = {**GOOD_REORDER, "to": "payments@elsewhere.example"}


def event(tool_name: str, tool_input: dict[str, Any] | None = None) -> PreToolUseHookInput:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input or {},
        "tool_use_id": "tool-1",
        "session_id": "session-1",
        "transcript_path": "",
        "cwd": "",
    }


def engine_for(tmp_path: Path, **settings: Any) -> AgentSdkEngine:
    return AgentSdkEngine(config_root=tmp_path / "sdk-config", api_key_set=True, **settings)


def options_for(tmp_path: Path, model: str | None = None) -> tuple[ClaudeAgentOptions, Job]:
    job = supplier_rig(tmp_path).job
    engine = engine_for(tmp_path, model=model)
    guard = ToolGuard(engine.tool_names(job.tools))
    options = engine.options(job, guard, budget_micros=500_000, config_dir=tmp_path / "cfg")
    return options, job


def test_the_agent_gets_only_its_jobs_tools_and_no_settings(tmp_path: Path) -> None:
    options, job = options_for(tmp_path)
    assert options.tools == []
    assert options.allowed_tools == NAMES
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.system_prompt == job.system_prompt
    assert options.max_budget_usd == 0.5
    assert options.max_turns == 20
    assert options.model is None
    assert isinstance(options.mcp_servers, dict)
    assert list(options.mcp_servers) == ["supplier_reorders"]
    assert options.hooks is not None
    [matcher] = options.hooks["PreToolUse"]
    assert matcher.matcher is None
    assert options.env == {
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
    }


def test_the_model_is_passed_only_when_set(tmp_path: Path) -> None:
    options, _ = options_for(tmp_path, model="a-model-name")
    assert options.model == "a-model-name"
    assert engine_for(tmp_path, model="")._model is None


def test_the_engine_refuses_to_start_without_an_api_key(tmp_path: Path) -> None:
    started: list[ClaudeAgentOptions] = []

    async def never(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        started.append(options)
        yield result_message()

    engine = AgentSdkEngine(config_root=tmp_path / "sdk-config", api_key_set=False, query_fn=never)
    with pytest.raises(ApiKeyMissing, match="ANTHROPIC_API_KEY is not set"):
        asyncio.run(engine.run(supplier_rig(tmp_path).job, ToolGuard([]), budget_micros=500_000))
    assert started == []
    assert not (tmp_path / "sdk-config").exists()


def test_each_run_gets_an_empty_config_folder_and_no_oauth_token(tmp_path: Path) -> None:
    seen: list[tuple[dict[str, str], list[Path]]] = []

    async def record(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        folder = Path(options.env["CLAUDE_CONFIG_DIR"])
        seen.append((dict(options.env), sorted(folder.iterdir())))
        yield result_message()

    engine = engine_for(tmp_path, query_fn=record)
    job = supplier_rig(tmp_path).job
    for _ in range(2):
        asyncio.run(engine.run(job, ToolGuard([]), budget_micros=500_000))
    for env, contents in seen:
        assert sorted(env) == ["CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"]
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
        assert Path(env["CLAUDE_CONFIG_DIR"]).parent == tmp_path / "sdk-config"
        assert contents == []
        assert not Path(env["CLAUDE_CONFIG_DIR"]).exists()
    [first, second] = [env["CLAUDE_CONFIG_DIR"] for env, _ in seen]
    assert first != second


def test_the_cli_gets_exactly_these_flags(tmp_path: Path) -> None:
    options, job = options_for(tmp_path)
    transport = SubprocessCLITransport(
        prompt="go", options=replace(options, cli_path="/nonexistent/claude")
    )
    argv = transport._build_command()

    def after(flag: str) -> str:
        return argv[argv.index(flag) + 1]

    assert after("--system-prompt") == job.system_prompt
    assert after("--tools") == ""
    assert after("--allowedTools") == ",".join(NAMES)
    assert after("--permission-mode") == "dontAsk"
    assert after("--max-budget-usd") == "0.5"
    assert "--setting-sources=" in argv
    assert "--strict-mcp-config" in argv
    assert "--model" not in argv
    servers = json.loads(after("--mcp-config"))["mcpServers"]
    assert servers == {"supplier_reorders": {"type": "sdk", "name": "supplier_reorders"}}


def test_a_job_without_a_system_prompt_is_refused(tmp_path: Path) -> None:
    job = replace(supplier_rig(tmp_path).job, system_prompt="  ")
    with pytest.raises(ValueError, match="no system prompt"):
        engine_for(tmp_path).options(
            job, ToolGuard([]), budget_micros=500_000, config_dir=tmp_path / "cfg"
        )


def ask(hook: HookCallback, hook_input: HookInput) -> HookJSONOutput:
    async def call() -> HookJSONOutput:
        return await hook(hook_input, "tool-1", {"signal": None})

    return asyncio.run(call())


def test_the_hook_asks_the_guard_and_the_input_gate_about_every_call(tmp_path: Path) -> None:
    rig = supplier_rig(tmp_path)
    guard = ToolGuard(NAMES)
    hook = guard_hook(guard, rig.job.tools)
    assert ask(hook, event(NAMES[0])) == {}
    assert ask(hook, event("Bash")) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Refused by the tools gate: Bash is not one of this job's tools"
            ),
        }
    }
    assert ask(hook, event(NAMES[3], SMUGGLED)) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Refused by the input gate: Additional properties are not allowed "
                "('to' was unexpected)"
            ),
        }
    }
    prompt_event: UserPromptSubmitHookInput = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "hi",
        "session_id": "session-1",
        "transcript_path": "",
        "cwd": "",
    }
    assert ask(hook, prompt_event) == {}
    assert [call.allowed for call in guard.calls] == [True, False, True]
    assert [r.gate for r in rig.ledger.refusals(run_id="run-1")] == [INPUT]


INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "acting-model", "version": "0"},
    },
}
INITIALIZED: dict[str, Any] = {"jsonrpc": "2.0", "method": "notifications/initialized"}


async def open_bridge(server: McpSdkServerConfig) -> SdkMcpBridge:
    """The SDK's own route from the CLI to an in-process server, after the MCP handshake."""
    bridge = SdkMcpBridge(server["name"], server["instance"])
    await bridge.handle(INITIALIZE)
    await bridge.handle(INITIALIZED)
    return bridge


def tool_call(number: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": number,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_the_sdk_server_refuses_bad_input_before_the_tool_set_sees_it(tmp_path: Path) -> None:
    rig = supplier_rig(tmp_path)
    rig.read_everything()

    async def call() -> dict[str, Any] | None:
        bridge = await open_bridge(sdk_server(rig.job.tools))
        try:
            return await bridge.handle(tool_call(1, "stage_reorder", SMUGGLED))
        finally:
            await bridge.aclose()

    reply = asyncio.run(call())
    assert reply is not None
    assert reply["result"]["isError"] is True
    assert reply["result"]["content"][0]["text"].startswith("Input validation error")
    assert rig.ledger.refusals(run_id="run-1") == []
    assert rig.ledger.changes(SAMPLE) == []


async def echo(arguments: dict[str, Any]) -> ToolResult:
    return ToolResult("hi")


def test_the_sdk_adapter_serves_the_same_tool_set() -> None:
    spec = ToolSpec("echo", "Echo.", {"type": "object", "properties": {}}, "read", echo)
    tools = ToolSet("demo", (spec,))
    assert sdk_tool_name("demo", "echo") == "mcp__demo__echo"
    assert sdk_allowed_tools(tools) == ["mcp__demo__echo"]
    [wrapped] = sdk_tools(tools)

    async def call() -> dict[str, Any]:
        return await wrapped.handler({})

    assert asyncio.run(call()) == {"content": [{"type": "text", "text": "hi"}], "is_error": False}
    assert sdk_server(tools)["name"] == "demo"


def result_message(**fields: Any) -> ResultMessage:
    base: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1234,
        "duration_api_ms": 1000,
        "is_error": False,
        "num_turns": 3,
        "session_id": "session-1",
        "total_cost_usd": 0.0123456789,
    }
    return ResultMessage(**{**base, **fields})


def replay(*messages: Message, error: ResultError | None = None) -> QueryFn:
    async def fake(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        for message in messages:
            yield message
        if error is not None:
            raise error

    return fake


OVER_BUDGET = ResultError("over", {"subtype": "error_max_budget_usd", "total_cost_usd": 0.51})
API_ERROR = ResultError("api", {"subtype": "success", "total_cost_usd": 0.002})
FAILED = ResultError("failed", {"subtype": "error_during_execution"})
ENDINGS: list[tuple[QueryFn, str, int, int | None]] = [
    (replay(result_message()), "success", 12_346, 1234),
    (
        replay(
            result_message(subtype="error_max_budget_usd", is_error=True, total_cost_usd=0.51),
            error=OVER_BUDGET,
        ),
        "error_max_budget_usd",
        510_000,
        1234,
    ),
    (replay(error=API_ERROR), "error", 2000, None),
    (replay(error=FAILED), "error_during_execution", 0, None),
    (replay(result_message(is_error=True)), "error", 12_346, 1234),
    (
        replay(result_message(subtype="error_max_turns", is_error=True)),
        "error_max_turns",
        12_346,
        1234,
    ),
    (replay(), "error: no result", 0, None),
]


@pytest.mark.parametrize(("query_fn", "outcome", "cost", "duration"), ENDINGS)
def test_every_ending_is_reported_with_its_cost_in_micro_dollars(
    tmp_path: Path, query_fn: QueryFn, outcome: str, cost: int, duration: int | None
) -> None:
    job = supplier_rig(tmp_path).job
    report = asyncio.run(
        engine_for(tmp_path, query_fn=query_fn).run(job, ToolGuard([]), budget_micros=500_000)
    )
    assert (report.outcome, report.cost_micros) == (outcome, cost)
    if duration is not None:
        assert report.duration_ms == duration


class ActingModel:
    """Stands in for the CLI: each call goes through the hook and, when the hook allows it,
    through the SDK's own bridge to the job's in-process server, as the CLI's calls do."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.calls = calls
        self.replies: list[dict[str, Any] | None] = []

    async def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        assert options.hooks is not None and isinstance(options.mcp_servers, dict)
        hook = options.hooks["PreToolUse"][0].hooks[0]
        server = cast(McpSdkServerConfig, options.mcp_servers["supplier_reorders"])
        bridge = await open_bridge(server)
        for number, (name, arguments) in enumerate(self.calls, start=1):
            decision = await hook(event(name, arguments), f"tool-{number}", {"signal": None})
            if "hookSpecificOutput" in decision:
                continue
            call = tool_call(number, name.removeprefix(PREFIX), arguments)
            self.replies.append(await bridge.handle(call))
        await bridge.aclose()
        yield result_message()


def test_a_run_on_the_sdk_engine_stages_refuses_and_signs(tmp_path: Path) -> None:
    new_keys(tmp_path / "keys")
    signer = load_signer(SAMPLE, tmp_path / "keys")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    model = ActingModel(
        [(name, {}) for name in NAMES[:3]]
        + [("Bash", {"command": "curl https://elsewhere.example"})]
        + [(NAMES[3], SMUGGLED), (NAMES[3], GOOD_REORDER)]
    )
    budget = RunBudget(run_cap_micros=500_000, day_cap_micros=3_000_000, overrun_margin_micros=0)
    runner = Runner(
        ledger=ledger,
        signer=signer,
        clock=FixedClock(T0),
        budget=budget,
        engine=engine_for(tmp_path, query_fn=model),
    )
    ctx = runner.begin("supplier_reorders", sample=True)
    job = build_job(ctx, sample_business(tmp_path / "outbox"))
    result = asyncio.run(runner.run(ctx, job))
    assert (result.outcome, result.cost_micros, result.duration_ms) == ("success", 12_346, 1234)
    assert len(result.staged) == 1
    assert [r.gate for r in ledger.refusals(run_id=result.run_id)] == [TOOLS, INPUT]
    assert len(model.replies) == 4
    last = model.replies[-1]
    assert last is not None
    assert last["result"]["isError"] is False
    assert last["result"]["content"][0]["text"].startswith("Staged as chg-")
    assert result.receipt["engine"] == "agent-sdk"
    assert result.receipt["sample"] is True
    assert {"tool": "Bash", "allowed": False} in result.receipt["toolCalls"]
    assert [r["gate"] for r in result.receipt["refusals"]] == [TOOLS, INPUT]
    assert_receipt_valid(result.receipt, signer.public_pem)
    ledger.close()
