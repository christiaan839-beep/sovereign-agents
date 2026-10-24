# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""The owner's yes or no, and the host's apply pass.

What it guarantees: a decision is recorded only through one of the host's own approval
channels, on a staged change that has not expired. The apply pass is a run of its own in the
ledger. For each approved change it checks, in order, the expiry, the approval (a yes recorded
by the host for this very change and not yet used) and the job's caps as they stand now. A
change that passes is claimed before anything is done: in one ledger transaction its yes is
used up and it becomes ``applying``; a change whose action receipt could not be signed (a
payload nested too deep, from a ledger written before staging checked) is refused by the
input gate first. Only then does its job's applier carry it out; it is marked applied and
gets a signed action receipt. When the applier returns a result the receipt cannot carry (a
float, or nested too deep), the side effect has happened, so the change is still applied and
receipted: ``result`` is null and ``resultNotRecorded`` says why. A change another pass has
claimed is left
alone, so two passes at once apply it once. A change that fails a gate is refused or expired,
never applied, and the refusal is recorded. The pass always ends its run with a signed run
receipt, also when an applier raises; a change it claimed whose applier raised (so whether
the side effect happened is unknown) stays ``applying``, is listed as unfinished, and is
never applied again by the core.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sovereign_matrix.verifiable_receipts import canonicalize

from .clock import Clock, iso
from .gates import EXPIRY, HOST_CHANNELS, INPUT, Refusal, check_apply
from .job import Applier
from .ledger import ACTION_FIELD_MAX_NESTING, Approval, Change, Ledger, WrongStatus, nesting, new_id
from .receipts import Signer, issue_receipt
from .runner import sign_run_receipt


def pending(ledger: Ledger, business: str, clock: Clock) -> list[Change]:
    """The staged changes the owner can still decide on; it expires the staged rest first."""
    ledger.expire_due(business, clock.now())
    return ledger.changes(business, status="staged")


def decide(
    ledger: Ledger,
    change_id: str,
    *,
    yes: bool,
    decided_by: str,
    channel: str,
    clock: Clock,
) -> Approval:
    """Record the owner's decision. Refuses a channel that is not one of the host's own."""
    if channel not in HOST_CHANNELS:
        raise ValueError(f"{channel!r} is not one of the host's approval channels")
    return ledger.record_decision(
        change_id,
        decision="yes" if yes else "no",
        decided_by=decided_by,
        channel=channel,
        now=clock.now(),
    )


@dataclass(frozen=True)
class ApplyReport:
    run_id: str
    applied: tuple[tuple[str, str], ...]
    refused: tuple[tuple[str, Refusal], ...]
    skipped: tuple[str, ...]
    unfinished: tuple[str, ...]
    receipt: dict[str, Any]


def _action_body(
    change: Change, approval: Approval, result: Mapping[str, Any], apply_run_id: str, sample: bool
) -> dict[str, Any]:
    return {
        "runId": change.run_id,
        "applyRunId": apply_run_id,
        "job": change.job,
        "changeId": change.change_id,
        "kind": change.kind,
        "idempotencyKey": change.idempotency_key,
        "sample": sample,
        "action": change.payload,
        "result": dict(result),
        "approval": {
            "id": approval.approval_id,
            "decision": approval.decision,
            "by": approval.decided_by,
            "channel": approval.channel,
            "at": iso(approval.decided_at),
        },
        "stagedAt": iso(change.created_at),
    }


def _unsignable(body: Mapping[str, Any]) -> bool:
    try:
        canonicalize(body)
    except (TypeError, ValueError):
        return True
    return False


def _signing_refusal(body: Mapping[str, Any]) -> Refusal | None:
    """A refusal when the action receipt for ``body`` (with an empty result) cannot be signed."""
    if _unsignable(body):
        return Refusal(INPUT, "the payload cannot be signed in an action receipt")
    return None


def _recordable(body: dict[str, Any]) -> dict[str, Any]:
    """``body``, or, when its result cannot be signed, ``body`` with the result left out.

    The applier has already run, so the change is applied either way; the receipt then says
    the result was not recorded and why, in words that do not repeat the result.
    """
    if not _unsignable(body):
        return body
    if nesting(body["result"]) > ACTION_FIELD_MAX_NESTING:
        why = "it nests deeper than the action receipt allows"
    else:
        why = (
            "it holds a value a receipt cannot carry"
            " (a float, a lone surrogate, or a type JSON lacks)"
        )
    return {
        **body,
        "result": None,
        "resultNotRecorded": f"the applier's result could not be signed: {why}",
    }


def apply_approved(
    *,
    ledger: Ledger,
    signer: Signer,
    clock: Clock,
    appliers: Mapping[str, Applier],
    sample: bool = False,
) -> ApplyReport:
    """Apply every approved change of the signer's business that passes the gates.

    ``appliers`` maps a job name to its applier; a change of a job with no applier given is
    left approved and reported as skipped. ``unfinished`` lists the changes left
    ``applying`` when the pass ends: claimed by another pass still at work, or by a pass that
    stopped part way. When an applier raises, the run is still closed (outcome
    ``error: <type>``) and receipted, and the error is raised again.
    """
    business = signer.business
    started = clock.now()
    run_id = new_id("run")
    ledger.begin_run(
        run_id=run_id,
        business=business,
        job="apply",
        kind="apply",
        engine="host",
        now=started,
        reserve_micros=0,
        day_cap_micros=None,
    )
    applied: list[tuple[str, str]] = []
    refused: list[tuple[str, Refusal]] = []
    skipped: list[str] = []
    outcome = "success"
    try:
        for change in ledger.changes(business, status="approved"):
            applier = appliers.get(change.job)
            if applier is None:
                skipped.append(change.change_id)
                continue
            now = clock.now()
            approval = ledger.approval_for(change.change_id)
            refusal = check_apply(change, approval, applier.cap_violations(change), now)
            if refusal is None:
                assert approval is not None  # check_apply refuses a change without one
                refusal = _signing_refusal(_action_body(change, approval, {}, run_id, sample))
            if refusal is not None:
                ledger.record_refusal(
                    run_id=run_id,
                    business=business,
                    tool="apply",
                    gate=refusal.gate,
                    reason=refusal.reason,
                    now=now,
                    change_id=change.change_id,
                )
                if refusal.gate == EXPIRY:
                    ledger.mark_expired(change.change_id, now)
                else:
                    ledger.mark_refused(change.change_id, now)
                refused.append((change.change_id, refusal))
                continue
            assert approval is not None  # check_apply refuses a change without one
            try:
                ledger.claim(change.change_id, approval_id=approval.approval_id, now=now)
            except WrongStatus:
                continue  # another pass claimed it after this one read it
            result = applier.apply(change)
            body = _recordable(_action_body(change, approval, result, run_id, sample))
            ledger.mark_applied(change.change_id, now=now)
            receipt = issue_receipt(
                ledger=ledger,
                signer=signer,
                kind="action",
                subject_id=change.change_id,
                body=body,
                now=now,
            )
            applied.append((change.change_id, str(receipt["id"])))
    except BaseException as exc:
        outcome = f"error: {type(exc).__name__}"
        raise
    finally:
        ended = clock.now()
        unfinished = [change.change_id for change in ledger.changes(business, status="applying")]
        ledger.end_run(
            run_id,
            outcome=outcome,
            cost_micros=0,
            duration_ms=int((ended - started).total_seconds() * 1000),
            now=ended,
        )
        run_receipt = sign_run_receipt(
            ledger=ledger,
            signer=signer,
            run_id=run_id,
            sample=sample,
            tool_calls=[],
            now=ended,
            applied=[change_id for change_id, _ in applied],
            unfinished=unfinished,
        )
    return ApplyReport(
        run_id, tuple(applied), tuple(refused), tuple(skipped), tuple(unfinished), run_receipt
    )
