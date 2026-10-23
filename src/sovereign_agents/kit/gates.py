# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""The gates: plain code, outside the model's reach, that every stage and apply passes.

Staging passes the input, provenance and caps gates: the input is well formed, every id it
names was read through a tool in this run, and the job's caps hold. Applying passes expiry,
approval and the caps again, under the caps in force at apply time: the change has not
expired, the owner's yes was recorded by the host for this very change and has not been used,
and the caps still hold. A tool call off the job's list meets the tools gate. A gate that
holds returns a :class:`Refusal` naming itself; nothing is raised and nothing is applied.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from .clock import iso
from .fence import sanitize_label
from .ledger import Approval, Change

INPUT = "input"
PROVENANCE = "provenance"
CAPS = "caps"
EXPIRY = "expiry"
APPROVAL = "approval"
TOOLS = "tools"

# Where a yes may come from: the host's own approval surfaces, never the model.
TERMINAL = "terminal"
HOST_CHANNELS = frozenset({TERMINAL})


@dataclass(frozen=True)
class Refusal:
    gate: str
    reason: str

    def __str__(self) -> str:
        return f"Refused by the {self.gate} gate: {self.reason}"


@dataclass
class Reads:
    """The ids the agent has read through its tools in this run, by kind."""

    seen: dict[str, set[str]] = field(default_factory=dict)

    def add(self, kind: str, ids: Iterable[str]) -> None:
        self.seen.setdefault(kind, set()).update(ids)

    def has(self, kind: str, item_id: str) -> bool:
        return item_id in self.seen.get(kind, set())


def _ids(ids: Iterable[str]) -> str:
    return ", ".join(sanitize_label(item, 80) for item in ids)


def check_read(reads: Reads, kind: str, ids: Iterable[str]) -> Refusal | None:
    """Provenance: every id must have come back from a read tool in this run."""
    unread = sorted({item for item in ids if not reads.has(kind, item)})
    if not unread:
        return None
    return Refusal(
        PROVENANCE,
        f"{kind} {_ids(unread)} was not read in this run; read it first and use ids "
        "from the results",
    )


def check_caps(violations: Sequence[str]) -> Refusal | None:
    if not violations:
        return None
    return Refusal(CAPS, "; ".join(violations))


def check_expiry(change: Change, now: datetime) -> Refusal | None:
    if now < change.expires_at:
        return None
    return Refusal(EXPIRY, f"change {change.change_id} expired at {iso(change.expires_at)}")


def check_approval(change: Change, approval: Approval | None) -> Refusal | None:
    """Only a yes recorded by the host for this very change, not yet used."""
    if change.status != "approved":
        return Refusal(APPROVAL, f"change {change.change_id} is {change.status}, not approved")
    if approval is None:
        return Refusal(APPROVAL, f"no approval is recorded for change {change.change_id}")
    if approval.change_id != change.change_id:
        return Refusal(
            APPROVAL,
            f"approval {approval.approval_id} is for change {approval.change_id}, "
            f"not {change.change_id}",
        )
    if approval.decision != "yes":
        return Refusal(APPROVAL, f"the owner said {approval.decision} to {change.change_id}")
    if approval.channel not in HOST_CHANNELS:
        return Refusal(
            APPROVAL,
            f"approval {approval.approval_id} came through {sanitize_label(approval.channel, 40)},"
            " which is not one of the host's approval channels",
        )
    if approval.used_at is not None:
        return Refusal(APPROVAL, f"approval {approval.approval_id} has already been used")
    return None


def check_apply(
    change: Change,
    approval: Approval | None,
    cap_violations: Sequence[str],
    now: datetime,
) -> Refusal | None:
    """Everything apply needs, in order: not expired, approved by the host, within caps."""
    return (
        check_expiry(change, now) or check_approval(change, approval) or check_caps(cap_violations)
    )
