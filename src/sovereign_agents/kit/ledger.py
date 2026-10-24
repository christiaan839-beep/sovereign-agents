# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""The ledger: one SQLite file, through the standard library, that the host alone writes.

It holds:

- runs, each with its engine, outcome, cost in whole micro-dollars and duration, and the
  spend per business per day;
- staged changes, each with an idempotency key, an expiry and a status that only moves
  forward: staged -> approved -> applying -> applied, staged -> refused, approved ->
  refused, and staged or approved -> expired;
- the owner's decisions, at most one per change, each usable once: the host's claim on a
  change uses its yes and marks it applying in one transaction, before anything is done;
- every refusal a gate made, every flag an agent raised, and every signed receipt.

What it refuses: a run the day's spend cap cannot cover (counting the reservations of runs
still open); a payload with a float in it, or nested too deep for the action receipt that
will sign it (see ``ACTION_FIELD_MAX_NESTING``); a decision on a change that is not staged or has
expired; a claim without an unused yes for that very change, or on a change that is not
approved; marking a change applied that was not claimed; any status the schema does not know.
A status and the record that explains it are one write: a change becomes applied together with
its action receipt, and refused or expired together with its refusal; ``atomic`` joins other
writes the same way. Amounts are integers. Times come from the injected clock.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sovereign_matrix.verifiable_receipts import canonicalize

from .clock import day_key, iso, parse_iso

ChangeStatus = Literal["staged", "approved", "applying", "refused", "applied", "expired"]
Decision = Literal["yes", "no"]
RunKind = Literal["agent", "apply"]
ReceiptKind = Literal["action", "run"]

# VAOS 2.0 section 7.2: every verifier rejects a receipt nested deeper than 64.
RECEIPT_MAX_NESTING = 64
# The action receipt (approvals._action_body) signs a change's payload as its "action" field
# and the applier's result as its "result" field. The receipt object is level 1, so both
# values sit at level 2.
ACTION_FIELD_LEVEL = 2
# So each of them, counted as its own level 1, may nest this deep: 63. A payload of
# {"deep": <62 nested lists>} is the deepest that fits; one more list and the receipt is 65.
ACTION_FIELD_MAX_NESTING = RECEIPT_MAX_NESTING - (ACTION_FIELD_LEVEL - 1)


class PayloadTooDeep(ValueError):
    """A payload nested deeper than the action receipt that would sign it can carry."""


def nesting(value: Any) -> int:
    """How deep ``value`` nests: 0 for a scalar, 1 for a list or dict, one more for each inside.

    Past ``RECEIPT_MAX_NESTING`` it stops and returns ``RECEIPT_MAX_NESTING + 1``, so a cyclic
    value ends too. A list or dict met again at the same level is not walked again, so a value
    that shares one many times over is walked once per level. Only ``dict`` and ``list`` count:
    a tuple or another kind of Mapping is a leaf, which is safe because ``canonicalize`` refuses
    both, so a value holding one is refused before it is signed.
    """
    deepest = 0
    walked: set[tuple[int, int]] = set()
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, dict):
            children: Iterable[Any] = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        if level > RECEIPT_MAX_NESTING:
            return level
        if (id(item), level) in walked:
            continue
        walked.add((id(item), level))
        deepest = max(deepest, level)
        stack.extend((child, level + 1) for child in children)
    return deepest


def check_payload_depth(payload: Any) -> None:
    """Raise :class:`PayloadTooDeep` when ``payload`` nests deeper than its receipt allows."""
    if nesting(payload) > ACTION_FIELD_MAX_NESTING:
        raise PayloadTooDeep(
            f"payload nests deeper than {ACTION_FIELD_MAX_NESTING}, the most the action receipt"
            f" can sign: it signs the payload at level {ACTION_FIELD_LEVEL}, and verifiers"
            f" reject a receipt nested deeper than {RECEIPT_MAX_NESTING}"
        )


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    business TEXT NOT NULL,
    job TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('agent', 'apply')),
    engine TEXT NOT NULL,
    day TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT,
    reserved_micros INTEGER NOT NULL CHECK (reserved_micros >= 0),
    cost_micros INTEGER CHECK (cost_micros >= 0),
    duration_ms INTEGER CHECK (duration_ms >= 0)
);
CREATE TABLE IF NOT EXISTS spend (
    business TEXT NOT NULL,
    day TEXT NOT NULL,
    micros INTEGER NOT NULL CHECK (micros >= 0),
    PRIMARY KEY (business, day)
);
CREATE TABLE IF NOT EXISTS changes (
    change_id TEXT PRIMARY KEY,
    business TEXT NOT NULL,
    job TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('staged', 'approved', 'applying', 'refused', 'applied', 'expired')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (business, idempotency_key)
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    change_id TEXT NOT NULL UNIQUE REFERENCES changes (change_id),
    decision TEXT NOT NULL CHECK (decision IN ('yes', 'no')),
    decided_by TEXT NOT NULL,
    channel TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS refusals (
    refusal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    business TEXT NOT NULL,
    change_id TEXT,
    tool TEXT NOT NULL,
    gate TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flags (
    flag_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    business TEXT NOT NULL,
    source_id TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seen_at TEXT
);
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id TEXT PRIMARY KEY,
    business TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('action', 'run')),
    subject_id TEXT NOT NULL,
    receipt TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class LedgerError(Exception):
    """A ledger rule refused the call."""


class NotFound(LedgerError):
    pass


class WrongStatus(LedgerError):
    pass


class ChangeExpired(LedgerError):
    pass


class DayCapReached(LedgerError):
    def __init__(self, committed_micros: int, cap_micros: int) -> None:
        self.committed_micros = committed_micros
        self.cap_micros = cap_micros
        super().__init__(
            f"the day's spend cap cannot cover another run: {committed_micros} micro-dollars "
            f"committed of {cap_micros}"
        )


@dataclass(frozen=True)
class Run:
    run_id: str
    business: str
    job: str
    kind: RunKind
    engine: str
    day: str
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None
    reserved_micros: int
    cost_micros: int | None
    duration_ms: int | None


@dataclass(frozen=True)
class Change:
    change_id: str
    business: str
    job: str
    run_id: str
    kind: str
    idempotency_key: str
    payload: dict[str, Any]
    summary: str
    status: ChangeStatus
    created_at: datetime
    expires_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Approval:
    approval_id: str
    change_id: str
    decision: Decision
    decided_by: str
    channel: str
    decided_at: datetime
    used_at: datetime | None


@dataclass(frozen=True)
class RefusalRecord:
    refusal_id: str
    run_id: str
    business: str
    change_id: str | None
    tool: str
    gate: str
    reason: str
    created_at: datetime


@dataclass(frozen=True)
class Flag:
    flag_id: str
    run_id: str
    business: str
    source_id: str
    note: str
    created_at: datetime
    seen_at: datetime | None


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def _maybe_time(text: str | None) -> datetime | None:
    return None if text is None else parse_iso(text)


class Ledger:
    def __init__(self, path: Path | str, *, timeout: float = 5.0) -> None:
        # ``timeout`` is how long a write waits for another connection's lock, in seconds.
        # The journal stays SQLite's default rollback journal, not WAL: the ledger is one file
        # that is copied whole and opened read-only (mode=ro) to check it, which WAL's -wal
        # and -shm side files would complicate. One host process writes at a time, so the
        # readers WAL would let through are rare, and a COMMIT they block now rolls back.
        self._db = sqlite3.connect(str(path), isolation_level=None, timeout=timeout)
        self._tx_depth = 0
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        # The outermost block is a transaction: it commits, or rolls back when an error leaves
        # it, the COMMIT's own included. A block inside another is a savepoint: an error that
        # leaves it undoes its writes only, so a caller that catches the error keeps the rest.
        # When SQLite has already rolled the transaction back itself, there is nothing to undo
        # and the error is raised as it is.
        if self._tx_depth:
            savepoint = f"tx_{self._tx_depth}"
            self._db.execute(f"SAVEPOINT {savepoint}")
            self._tx_depth += 1
            try:
                yield self._db
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute(f"ROLLBACK TO {savepoint}")
                    self._db.execute(f"RELEASE {savepoint}")
                raise
            finally:
                self._tx_depth -= 1
            self._db.execute(f"RELEASE {savepoint}")
            return
        self._db.execute("BEGIN IMMEDIATE")
        self._tx_depth = 1
        try:
            yield self._db
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise
        finally:
            self._tx_depth = 0

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Every ledger write inside the block commits together, or none does."""
        with self._tx():
            yield

    # Runs and spend

    def begin_run(
        self,
        *,
        run_id: str,
        business: str,
        job: str,
        kind: RunKind,
        engine: str,
        now: datetime,
        reserve_micros: int,
        day_cap_micros: int | None,
    ) -> Run:
        """Record a run as started and reserve its spend cap against the day's cap.

        With ``day_cap_micros`` set, the run starts only when what the day has already
        committed (spend so far plus the reservations of runs still open) leaves room for
        ``reserve_micros``; otherwise it raises :class:`DayCapReached`.
        """
        day = day_key(now)
        with self._tx() as db:
            committed = self._committed(db, business, day)
            if day_cap_micros is not None and committed + reserve_micros > day_cap_micros:
                raise DayCapReached(committed, day_cap_micros)
            db.execute(
                "INSERT INTO runs (run_id, business, job, kind, engine, day, started_at,"
                " reserved_micros) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, business, job, kind, engine, day, iso(now), reserve_micros),
            )
        return self.get_run(run_id)

    def end_run(
        self, run_id: str, *, outcome: str, cost_micros: int, duration_ms: int, now: datetime
    ) -> Run:
        """Close a run, release its reservation and add its cost to its day's spend."""
        with self._tx() as db:
            row = db.execute(
                "SELECT business, day FROM runs WHERE run_id = ? AND ended_at IS NULL", (run_id,)
            ).fetchone()
            if row is None:
                raise WrongStatus(f"run {run_id} is not open")
            db.execute(
                "UPDATE runs SET ended_at = ?, outcome = ?, cost_micros = ?, duration_ms = ?"
                " WHERE run_id = ?",
                (iso(now), outcome, cost_micros, duration_ms, run_id),
            )
            db.execute(
                "INSERT INTO spend (business, day, micros) VALUES (?, ?, ?)"
                " ON CONFLICT (business, day) DO UPDATE SET micros = micros + excluded.micros",
                (row["business"], row["day"], cost_micros),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> Run:
        row = self._db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound(f"no run {run_id}")
        return Run(
            run_id=row["run_id"],
            business=row["business"],
            job=row["job"],
            kind=row["kind"],
            engine=row["engine"],
            day=row["day"],
            started_at=parse_iso(row["started_at"]),
            ended_at=_maybe_time(row["ended_at"]),
            outcome=row["outcome"],
            reserved_micros=row["reserved_micros"],
            cost_micros=row["cost_micros"],
            duration_ms=row["duration_ms"],
        )

    def spent_micros(self, business: str, day: str) -> int:
        row = self._db.execute(
            "SELECT micros FROM spend WHERE business = ? AND day = ?", (business, day)
        ).fetchone()
        return 0 if row is None else int(row["micros"])

    def committed_micros(self, business: str, day: str) -> int:
        """Spend so far that day plus the reservations of its runs still open."""
        return self._committed(self._db, business, day)

    def open_runs(self, business: str) -> list[Run]:
        """The business's runs not yet ended, oldest first."""
        rows = self._db.execute(
            "SELECT run_id FROM runs WHERE business = ? AND ended_at IS NULL"
            " ORDER BY started_at, rowid",
            (business,),
        ).fetchall()
        return [self.get_run(row["run_id"]) for row in rows]

    def _committed(self, db: sqlite3.Connection, business: str, day: str) -> int:
        reserved = db.execute(
            "SELECT COALESCE(SUM(reserved_micros), 0) AS total FROM runs"
            " WHERE business = ? AND day = ? AND ended_at IS NULL",
            (business, day),
        ).fetchone()
        return self.spent_micros(business, day) + int(reserved["total"])

    # Changes

    def stage_change(
        self,
        *,
        run_id: str,
        business: str,
        job: str,
        kind: str,
        idempotency_key: str,
        payload: dict[str, Any],
        summary: str,
        now: datetime,
        ttl: timedelta,
    ) -> tuple[Change, bool]:
        """Stage a change, or return the one already staged under the same key.

        Returns the change and whether this call created it. The payload is stored as its
        canonical JSON, which refuses floats. A payload nested deeper than the action receipt
        can sign (``ACTION_FIELD_MAX_NESTING``) is refused here, before anyone can approve it.
        """
        check_payload_depth(payload)
        with self._tx() as db:
            row = db.execute(
                "SELECT change_id FROM changes WHERE business = ? AND idempotency_key = ?",
                (business, idempotency_key),
            ).fetchone()
            created = row is None
            change_id = new_id("chg") if created else str(row["change_id"])
            if created:
                db.execute(
                    "INSERT INTO changes (change_id, business, job, run_id, kind, idempotency_key,"
                    " payload, summary, status, created_at, expires_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?, ?, ?)",
                    (
                        change_id,
                        business,
                        job,
                        run_id,
                        kind,
                        idempotency_key,
                        canonicalize(payload),
                        summary,
                        iso(now),
                        iso(now + ttl),
                        iso(now),
                    ),
                )
        return self.get_change(change_id), created

    def get_change(self, change_id: str) -> Change:
        row = self._db.execute("SELECT * FROM changes WHERE change_id = ?", (change_id,)).fetchone()
        if row is None:
            raise NotFound(f"no change {change_id}")
        return self._change(row)

    def changes(
        self,
        business: str,
        *,
        status: ChangeStatus | None = None,
        run_id: str | None = None,
    ) -> list[Change]:
        query = "SELECT * FROM changes WHERE business = ?"
        params: list[str] = [business]
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        query += " ORDER BY created_at, rowid"
        return [self._change(row) for row in self._db.execute(query, params)]

    def expire_due(self, business: str, now: datetime) -> list[str]:
        """Mark every staged change whose expiry has passed as expired.

        An approved change past its expiry is left to the apply pass, whose expiry gate
        refuses it and records the refusal.
        """
        with self._tx() as db:
            rows = db.execute(
                "SELECT change_id FROM changes WHERE business = ? AND status = 'staged'"
                " AND expires_at <= ? ORDER BY change_id",
                (business, iso(now)),
            ).fetchall()
            ids = [row["change_id"] for row in rows]
            for change_id in ids:
                self._move(db, change_id, ("staged",), "expired", now)
        return ids

    def mark_expired(self, change_id: str, now: datetime) -> Change:
        with self._tx() as db:
            self._move(db, change_id, ("staged", "approved"), "expired", now)
        return self.get_change(change_id)

    def _move(
        self,
        db: sqlite3.Connection,
        change_id: str,
        from_statuses: tuple[ChangeStatus, ...],
        to_status: ChangeStatus,
        now: datetime,
    ) -> None:
        marks = ", ".join("?" for _ in from_statuses)
        moved = db.execute(
            f"UPDATE changes SET status = ?, updated_at = ? WHERE change_id = ?"
            f" AND status IN ({marks})",
            (to_status, iso(now), change_id, *from_statuses),
        ).rowcount
        if moved != 1:
            wanted = " or ".join(from_statuses)
            raise WrongStatus(f"change {change_id} is not {wanted}; cannot mark it {to_status}")

    @staticmethod
    def _change(row: sqlite3.Row) -> Change:
        return Change(
            change_id=row["change_id"],
            business=row["business"],
            job=row["job"],
            run_id=row["run_id"],
            kind=row["kind"],
            idempotency_key=row["idempotency_key"],
            payload=json.loads(row["payload"]),
            summary=row["summary"],
            status=row["status"],
            created_at=parse_iso(row["created_at"]),
            expires_at=parse_iso(row["expires_at"]),
            updated_at=parse_iso(row["updated_at"]),
        )

    # Approvals

    def record_decision(
        self,
        change_id: str,
        *,
        decision: Decision,
        decided_by: str,
        channel: str,
        now: datetime,
    ) -> Approval:
        """Record the owner's yes or no on a staged change that has not expired."""
        change = self.get_change(change_id)
        if change.status != "staged":
            raise WrongStatus(f"change {change_id} is {change.status}, not staged")
        if now >= change.expires_at:
            self.mark_expired(change_id, now)
            raise ChangeExpired(f"change {change_id} expired at {iso(change.expires_at)}")
        approval_id = new_id("apr")
        with self._tx() as db:
            self._move(
                db, change_id, ("staged",), "approved" if decision == "yes" else "refused", now
            )
            db.execute(
                "INSERT INTO approvals (approval_id, change_id, decision, decided_by, channel,"
                " decided_at) VALUES (?, ?, ?, ?, ?, ?)",
                (approval_id, change_id, decision, decided_by, channel, iso(now)),
            )
        return Approval(approval_id, change_id, decision, decided_by, channel, now, None)

    def approval_for(self, change_id: str) -> Approval | None:
        row = self._db.execute(
            "SELECT * FROM approvals WHERE change_id = ?", (change_id,)
        ).fetchone()
        if row is None:
            return None
        return Approval(
            approval_id=row["approval_id"],
            change_id=row["change_id"],
            decision=row["decision"],
            decided_by=row["decided_by"],
            channel=row["channel"],
            decided_at=parse_iso(row["decided_at"]),
            used_at=_maybe_time(row["used_at"]),
        )

    def claim(self, change_id: str, *, approval_id: str, now: datetime) -> Change:
        """Mark an approved change applying and its yes used, together or not at all.

        The host claims a change before it calls the applier, so another apply pass finds it
        claimed and leaves it alone, and the same yes can never be used twice.
        """
        with self._tx() as db:
            self._move(db, change_id, ("approved",), "applying", now)
            used = db.execute(
                "UPDATE approvals SET used_at = ? WHERE approval_id = ? AND change_id = ?"
                " AND decision = 'yes' AND used_at IS NULL",
                (iso(now), approval_id, change_id),
            ).rowcount
            if used != 1:
                raise WrongStatus(f"approval {approval_id} is not an unused yes for {change_id}")
        return self.get_change(change_id)

    def mark_applied_with_receipt(
        self,
        change_id: str,
        *,
        receipt_id: str,
        business: str,
        receipt: dict[str, Any],
        now: datetime,
    ) -> Change:
        """Mark a claimed change applied and store its signed action receipt, in one write.

        Either both happen or neither: a change is never applied without its receipt.
        """
        with self._tx() as db:
            self._move(db, change_id, ("applying",), "applied", now)
            self.add_receipt(
                receipt_id=receipt_id,
                business=business,
                kind="action",
                subject_id=change_id,
                receipt=receipt,
                now=now,
            )
        return self.get_change(change_id)

    def refuse_change(
        self,
        change_id: str,
        *,
        run_id: str,
        business: str,
        gate: str,
        reason: str,
        now: datetime,
        expire: bool = False,
    ) -> RefusalRecord:
        """Refuse (or, with ``expire``, expire) an approved change and record why, in one write.

        Raises :class:`WrongStatus`, recording nothing, when the change is no longer approved
        (another apply pass got to it first).
        """
        with self._tx() as db:
            self._move(db, change_id, ("approved",), "expired" if expire else "refused", now)
            return self.record_refusal(
                run_id=run_id,
                business=business,
                tool="apply",
                gate=gate,
                reason=reason,
                now=now,
                change_id=change_id,
            )

    # Refusals and flags

    def record_refusal(
        self,
        *,
        run_id: str,
        business: str,
        tool: str,
        gate: str,
        reason: str,
        now: datetime,
        change_id: str | None = None,
    ) -> RefusalRecord:
        refusal_id = new_id("ref")
        self._db.execute(
            "INSERT INTO refusals (refusal_id, run_id, business, change_id, tool, gate, reason,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (refusal_id, run_id, business, change_id, tool, gate, reason, iso(now)),
        )
        return RefusalRecord(refusal_id, run_id, business, change_id, tool, gate, reason, now)

    def refusals(self, *, run_id: str) -> list[RefusalRecord]:
        rows = self._db.execute(
            "SELECT * FROM refusals WHERE run_id = ? ORDER BY created_at, rowid", (run_id,)
        )
        return [
            RefusalRecord(
                refusal_id=row["refusal_id"],
                run_id=row["run_id"],
                business=row["business"],
                change_id=row["change_id"],
                tool=row["tool"],
                gate=row["gate"],
                reason=row["reason"],
                created_at=parse_iso(row["created_at"]),
            )
            for row in rows
        ]

    def add_flag(
        self, *, run_id: str, business: str, source_id: str, note: str, now: datetime
    ) -> Flag:
        """Something the agent reports to the owner; it changes nothing."""
        flag_id = new_id("flg")
        self._db.execute(
            "INSERT INTO flags (flag_id, run_id, business, source_id, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (flag_id, run_id, business, source_id, note, iso(now)),
        )
        return Flag(flag_id, run_id, business, source_id, note, now, None)

    def flags(self, *, run_id: str) -> list[Flag]:
        return self._flags("SELECT * FROM flags WHERE run_id = ?", (run_id,))

    def open_flags(self, business: str) -> list[Flag]:
        """Flags the owner has not been shown yet."""
        return self._flags(
            "SELECT * FROM flags WHERE business = ? AND seen_at IS NULL", (business,)
        )

    def mark_flags_seen(self, flag_ids: Iterable[str], now: datetime) -> None:
        with self._tx() as db:
            for flag_id in flag_ids:
                db.execute(
                    "UPDATE flags SET seen_at = ? WHERE flag_id = ? AND seen_at IS NULL",
                    (iso(now), flag_id),
                )

    def _flags(self, query: str, params: tuple[str, ...]) -> list[Flag]:
        rows = self._db.execute(query + " ORDER BY created_at, rowid", params)
        return [
            Flag(
                flag_id=row["flag_id"],
                run_id=row["run_id"],
                business=row["business"],
                source_id=row["source_id"],
                note=row["note"],
                created_at=parse_iso(row["created_at"]),
                seen_at=_maybe_time(row["seen_at"]),
            )
            for row in rows
        ]

    # Receipts

    def add_receipt(
        self,
        *,
        receipt_id: str,
        business: str,
        kind: ReceiptKind,
        subject_id: str,
        receipt: dict[str, Any],
        now: datetime,
    ) -> None:
        self._db.execute(
            "INSERT INTO receipts (receipt_id, business, kind, subject_id, receipt, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                receipt_id,
                business,
                kind,
                subject_id,
                json.dumps(receipt, ensure_ascii=False),
                iso(now),
            ),
        )

    def receipts(
        self,
        business: str,
        *,
        kind: ReceiptKind | None = None,
        subject_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT receipt FROM receipts WHERE business = ?"
        params: list[str] = [business]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if subject_id is not None:
            query += " AND subject_id = ?"
            params.append(subject_id)
        query += " ORDER BY created_at, rowid"
        return [json.loads(row["receipt"]) for row in self._db.execute(query, params)]
