# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""The trust core: the parts of the kit that decide what may happen and prove what did.

An agent reads and stages. The owner says yes or no. Only then does the host apply a change,
and every applied action and every run leaves a receipt signed with the business's own key.
This package holds no business specifics: jobs supply their own tools, prompts and caps.
Everything listed in ``__all__`` is the public API; ``sovereign_agents.kit.testing`` holds the
helpers for tests and the attack suite.
"""

from sovereign_matrix.verifiable_receipts import VerifyResult, canonicalize

from .approvals import ApplyReport, apply_approved, decide, pending
from .clock import Clock, FixedClock, SystemClock, day_key, iso, parse_iso
from .engine import Engine, EngineRun
from .fence import MAX_FENCED_CHARS, Fence, sanitize_label, truncate_display
from .gates import (
    APPROVAL,
    CAPS,
    EXPIRY,
    HOST_CHANNELS,
    INPUT,
    PROVENANCE,
    TERMINAL,
    TOOLS,
    Reads,
    Refusal,
    check_apply,
    check_approval,
    check_caps,
    check_expiry,
    check_read,
)
from .guard import ToolCall, ToolGuard
from .hosted import HostedEndpoint, HostedToolServer
from .job import Applier, Job, RunContext
from .ledger import (
    Approval,
    Change,
    ChangeExpired,
    ChangeStatus,
    DayCapReached,
    Decision,
    Flag,
    Ledger,
    LedgerError,
    NotFound,
    RefusalRecord,
    Run,
    WrongStatus,
    new_id,
)
from .money import format_micros, micros_to_usd, usd_to_micros
from .names import check_slug
from .receipts import (
    PRIVATE_KEY_FILE,
    PUBLIC_KEY_FILE,
    KeyFileError,
    Signer,
    issue_receipt,
    load_public_pem,
    load_signer,
    new_keys,
    verify,
    verify_all,
)
from .runner import RunBudget, Runner, RunResult, sign_run_receipt
from .tools import ToolKind, ToolResult, ToolSet, ToolSpec

__version__ = "0.1.0"

__all__ = [
    "APPROVAL",
    "CAPS",
    "EXPIRY",
    "HOST_CHANNELS",
    "INPUT",
    "MAX_FENCED_CHARS",
    "PRIVATE_KEY_FILE",
    "PROVENANCE",
    "PUBLIC_KEY_FILE",
    "TERMINAL",
    "TOOLS",
    "Applier",
    "ApplyReport",
    "Approval",
    "Change",
    "ChangeExpired",
    "ChangeStatus",
    "Clock",
    "DayCapReached",
    "Decision",
    "Engine",
    "EngineRun",
    "Fence",
    "FixedClock",
    "Flag",
    "HostedEndpoint",
    "HostedToolServer",
    "Job",
    "KeyFileError",
    "Ledger",
    "LedgerError",
    "NotFound",
    "Reads",
    "Refusal",
    "RefusalRecord",
    "Run",
    "RunBudget",
    "RunContext",
    "RunResult",
    "Runner",
    "Signer",
    "SystemClock",
    "ToolCall",
    "ToolGuard",
    "ToolKind",
    "ToolResult",
    "ToolSet",
    "ToolSpec",
    "VerifyResult",
    "WrongStatus",
    "apply_approved",
    "canonicalize",
    "check_apply",
    "check_approval",
    "check_caps",
    "check_expiry",
    "check_read",
    "check_slug",
    "day_key",
    "decide",
    "format_micros",
    "iso",
    "issue_receipt",
    "load_public_pem",
    "load_signer",
    "micros_to_usd",
    "new_id",
    "new_keys",
    "parse_iso",
    "pending",
    "sanitize_label",
    "sign_run_receipt",
    "truncate_display",
    "usd_to_micros",
    "verify",
    "verify_all",
]
