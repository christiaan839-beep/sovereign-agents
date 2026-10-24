# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""The owner's yes or no, and the host's apply pass.

What it guarantees: a decision is recorded only through one of the host's own approval
channels, on a staged change that has not expired; a yes on a payload its action receipt could
not sign is refused. The apply pass is a run of its own in the ledger. For each approved change
it checks, in order, that the payload can be signed in an action receipt (a ledger written
before staging checked the depth can hold one that cannot), the expiry, the approval (a yes
recorded by the host for this very change and not yet used) and the job's caps as they stand
now. A change that fails a check is refused or expired, never applied, and the refusal is
recorded in the same write. A change that passes is claimed before anything is done: in one
ledger transaction its yes is used up and it becomes ``applying``. Only then does its job's
applier carry it out. The action receipt is signed next, and the change is marked applied and
the receipt stored in one write. When the applier returns a result the receipt cannot carry
(not a mapping, a float, nested too deep, cyclic), the side effect has happened, so the change
is still applied and receipted: ``result`` is null, ``resultNotRecorded`` says why without
repeating the result, and the report lists it as unrecorded. A change another pass has claimed
or refused is left alone, so two passes at once apply or refuse it once.

The pass always ends its run with a signed run receipt, in the same write that ends the run.
A change it claimed and could not mark applied stays ``applying`` and is listed as unfinished:
its applier raised, so whether the side effect happened is unknown, or its receipt could not be
stored. The core never applies it again.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sovereign_matrix.verifiable_receipts import canonicalize

from .clock import Clock, iso
from .gates import EXPIRY, HOST_CHANNELS, INPUT, Refusal, check_apply
from .job import Applier
from .ledger import (
    ACTION_FIELD_MAX_NESTING,
    Approval,
    Change,
    Ledger,
    WrongStatus,
    check_payload_depth,
    nesting,
    new_id,
)
from .receipts import Signer, sign_receipt
from .runner import close_abandoned_runs, sign_run_receipt


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
    if yes:
        check_payload_depth(ledger.get_change(change_id).payload)
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
    # Applied changes whose applier's result is not in the receipt, each with the reason.
    unrecorded: tuple[tuple[str, str], ...] = ()


def _action_body(
    change: Change,
    approval: Approval,
    result: dict[str, Any] | None,
    apply_run_id: str,
    sample: bool,
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
        "result": result,
        "approval": {
            "id": approval.approval_id,
            "decision": approval.decision,
            "by": approval.decided_by,
            "channel": approval.channel,
            "at": iso(approval.decided_at),
        },
        "stagedAt": iso(change.created_at),
    }


def _payload_refusal(change: Change) -> Refusal | None:
    """A refusal when the change's payload cannot be signed in its action receipt."""
    try:
        check_payload_depth(change.payload)
        canonicalize(change.payload)
    except (TypeError, ValueError):
        return Refusal(INPUT, "the payload cannot be signed in an action receipt")
    return None


def _result_problem(result: dict[str, Any] | None) -> str | None:
    """Why the applier's result (the one copy that will be signed) cannot go in the action
    receipt, or None when it can; None for ``result`` means the applier returned no mapping.

    The reason never repeats the result: the library's own messages quote the value.
    """
    if result is None:
        return "it is not a JSON object"
    if nesting(result) > ACTION_FIELD_MAX_NESTING:
        return "it nests deeper than the action receipt allows"
    try:
        canonicalize({"result": result})  # the result at its level in the receipt
    except (TypeError, ValueError):
        return (
            "it holds a value a receipt cannot carry"
            " (a float, a lone surrogate, or a type JSON lacks)"
        )
    return None


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
    close_abandoned_runs(ledger=ledger, signer=signer, now=started)
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
    unrecorded: list[tuple[str, str]] = []
    outcome = "success"
    try:
        for change in ledger.changes(business, status="approved"):
            applier = appliers.get(change.job)
            if applier is None:
                skipped.append(change.change_id)
                continue
            now = clock.now()
            approval = ledger.approval_for(change.change_id)
            # The payload first, so job code never sees one that cannot be signed.
            refusal = _payload_refusal(change) or check_apply(
                change, approval, applier.cap_violations(change), now
            )
            if refusal is not None:
                try:
                    ledger.refuse_change(
                        change.change_id,
                        run_id=run_id,
                        business=business,
                        gate=refusal.gate,
                        reason=refusal.reason,
                        now=now,
                        expire=refusal.gate == EXPIRY,
                    )
                except WrongStatus:
                    continue  # another pass refused or claimed it after this one read it
                refused.append((change.change_id, refusal))
                continue
            assert approval is not None  # check_apply refuses a change without one
            try:
                ledger.claim(change.change_id, approval_id=approval.approval_id, now=now)
            except WrongStatus:
                continue  # another pass claimed it after this one read it
            returned = applier.apply(change)
            # One copy: the one checked is the one signed.
            result = dict(returned) if isinstance(returned, Mapping) else None
            problem = _result_problem(result)
            body = _action_body(change, approval, None if problem else result, run_id, sample)
            if problem is not None:
                body["resultNotRecorded"] = f"the applier's result could not be signed: {problem}"
                unrecorded.append((change.change_id, body["resultNotRecorded"]))
            receipt = sign_receipt(signer=signer, kind="action", body=body, now=now)
            ledger.mark_applied_with_receipt(
                change.change_id,
                receipt_id=str(receipt["id"]),
                business=business,
                receipt=receipt,
                now=now,
            )
            applied.append((change.change_id, str(receipt["id"])))
    except BaseException as exc:
        outcome = f"error: {type(exc).__name__}"
        raise
    finally:
        ended = clock.now()
        unfinished = [change.change_id for change in ledger.changes(business, status="applying")]
        with ledger.atomic():  # the run is ended only together with its receipt
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
        run_id,
        tuple(applied),
        tuple(refused),
        tuple(skipped),
        tuple(unfinished),
        run_receipt,
        tuple(unrecorded),
    )
