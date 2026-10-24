"""The Agent SDK engine: a job's agent loop on the Claude Agent SDK, run in this process.

The agent gets no built-in tools, only the job's own, served by an in-process MCP server that
calls ``ToolSet.call``. Anything else is denied twice: by the core's tool guard in a
``PreToolUse`` hook, and by the SDK's ``dontAsk`` permission mode. The same hook asks
``ToolSet.check_input`` before the SDK's server, which checks input itself and would refuse it
unrecorded. No settings file and no outside MCP config is read. The system prompt is always
the job's own: the SDK sends an empty one when it is None. The model comes from configuration
and is not passed when unset.

Claude runs on the client's own API key, never a claude.ai login: the engine refuses to start
when ``ANTHROPIC_API_KEY`` is not set, and gives the CLI, for each run, a new and empty config
folder and a blank ``CLAUDE_CODE_OAUTH_TOKEN``, so it finds no stored login to fall back on.
The key itself stays in the environment the CLI inherits; the engine never copies or logs it.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    Message,
    ResultError,
    ResultMessage,
    SdkMcpTool,
    create_sdk_mcp_server,
    query,
)
from claude_agent_sdk.types import HookCallback, McpSdkServerConfig

from sovereign_agents.kit import (
    EngineDidNotStart,
    EngineRun,
    Job,
    ToolGuard,
    ToolSet,
    ToolSpec,
    micros_to_usd,
    usd_to_micros,
)


class QueryFn(Protocol):
    def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]: ...


def sdk_tool_name(namespace: str, tool: str) -> str:
    """The name the SDK gives an in-process tool: ``mcp__<server key>__<tool>``."""
    return f"mcp__{namespace}__{tool}"


def sdk_allowed_tools(tools: ToolSet) -> list[str]:
    return [sdk_tool_name(tools.namespace, name) for name in tools.names()]


def sdk_tools(tools: ToolSet) -> list[SdkMcpTool[Any]]:
    """The job's tools as SDK tools, each a thin wrapper over ``ToolSet.call``."""

    def wrap(spec: ToolSpec) -> SdkMcpTool[Any]:
        async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            result = await tools.call(spec.name, arguments)
            return {"content": [{"type": "text", "text": result.text}], "is_error": result.is_error}

        return SdkMcpTool(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema,
            handler=handler,
        )

    return [wrap(spec) for spec in tools.tools]


def sdk_server(tools: ToolSet) -> McpSdkServerConfig:
    """The server to put in ``mcp_servers`` under the key ``tools.namespace``."""
    return create_sdk_mcp_server(name=tools.namespace, version="1.0.0", tools=sdk_tools(tools))


def cli_env(config_dir: Path) -> dict[str, str]:
    """What the engine lays over the host's environment for the CLI: no OAuth token and an
    empty config folder, so the CLI finds no claude.ai login and uses ``ANTHROPIC_API_KEY``."""
    return {"CLAUDE_CODE_OAUTH_TOKEN": "", "CLAUDE_CONFIG_DIR": str(config_dir)}


class ApiKeyMissing(EngineDidNotStart, ValueError):
    """The engine runs only on the client's own API key; without one it makes no call."""


def guard_hook(guard: ToolGuard, tools: ToolSet) -> HookCallback:
    """A PreToolUse hook: the core's guard checks every call's name, then
    ``ToolSet.check_input`` checks its input, before the SDK's server sees it. Both record what
    they refuse."""
    prefix = sdk_tool_name(tools.namespace, "")

    async def hook(
        hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if hook_input["hook_event_name"] != "PreToolUse":
            return {}
        name = hook_input["tool_name"]
        refusal = guard.check(name) or tools.check_input(
            name.removeprefix(prefix), hook_input["tool_input"]
        )
        if refusal is None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": str(refusal),
            }
        }

    return hook


def _outcome(result: ResultMessage | None, error: ResultError | None) -> str:
    if error is not None:
        return error.subtype if error.subtype not in (None, "success") else "error"
    if result is None:
        return "error: no result"
    if result.is_error:
        return result.subtype if result.subtype != "success" else "error"
    return result.subtype


def _cost_usd(result: ResultMessage | None, error: ResultError | None) -> float | None:
    if result is not None:
        return result.total_cost_usd
    raw = error.data.get("total_cost_usd") if error is not None else None
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None


class AgentSdkEngine:
    name = "agent-sdk"

    def __init__(
        self,
        *,
        config_root: Path,
        api_key_set: bool,
        model: str | None = None,
        query_fn: QueryFn = query,
    ) -> None:
        self._config_root = config_root
        self._api_key_set = api_key_set
        self._model = model or None
        self._query = query_fn

    def tool_names(self, tools: ToolSet) -> list[str]:
        return sdk_allowed_tools(tools)

    def options(
        self, job: Job, guard: ToolGuard, *, budget_micros: int, config_dir: Path
    ) -> ClaudeAgentOptions:
        if not job.system_prompt.strip():
            raise ValueError(f"job {job.name} has no system prompt; the SDK would send it empty")
        return ClaudeAgentOptions(
            tools=[],
            allowed_tools=sdk_allowed_tools(job.tools),
            permission_mode="dontAsk",
            setting_sources=[],
            strict_mcp_config=True,
            mcp_servers={job.tools.namespace: sdk_server(job.tools)},
            system_prompt=job.system_prompt,
            max_budget_usd=micros_to_usd(budget_micros),
            max_turns=job.max_turns,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[guard_hook(guard, job.tools)])]},
            env=cli_env(config_dir),
            model=self._model,
        )

    async def run(self, job: Job, guard: ToolGuard, *, budget_micros: int) -> EngineRun:
        if not self._api_key_set:
            raise ApiKeyMissing(
                "ANTHROPIC_API_KEY is not set; the kit runs only on the client's own API key, "
                "never on a claude.ai login. Put the key from the client's agent workspace in "
                "ANTHROPIC_API_KEY through the host's secret store"
            )
        self._config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="run-", dir=self._config_root) as config_dir:
            options = self.options(
                job, guard, budget_micros=budget_micros, config_dir=Path(config_dir)
            )
            started = time.monotonic()
            result: ResultMessage | None = None
            error: ResultError | None = None
            try:
                async for message in self._query(prompt=job.task_prompt, options=options):
                    if isinstance(message, ResultMessage):
                        result = message
            except ResultError as exc:
                error = exc
        duration_ms = (
            result.duration_ms if result is not None else int((time.monotonic() - started) * 1000)
        )
        return EngineRun(
            outcome=_outcome(result, error),
            cost_micros=usd_to_micros(_cost_usd(result, error)),
            duration_ms=duration_ms,
        )
