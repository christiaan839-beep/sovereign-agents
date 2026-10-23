"""The business's mailbox: messages in, replies out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .files import items, read_json, text
from .outbox import Outbox


@dataclass(frozen=True)
class MailMessage:
    message_id: str
    sender: str
    subject: str
    received_at: str
    body: str


@dataclass(frozen=True)
class OutgoingMail:
    idempotency_key: str
    to: str
    subject: str
    body: str
    in_reply_to: str


class Mailbox(Protocol):
    def list_messages(self) -> list[MailMessage]: ...

    def get_message(self, message_id: str) -> MailMessage | None: ...

    def send(self, mail: OutgoingMail) -> str: ...


class SampleMailbox:
    """Reads ``inbox.json``; sending writes to the outbox's ``mail.jsonl``."""

    def __init__(self, inbox_path: Path, outbox_dir: Path) -> None:
        self._inbox_path = inbox_path
        self.outbox = Outbox(outbox_dir / "mail.jsonl")

    def list_messages(self) -> list[MailMessage]:
        where = str(self._inbox_path)
        return [
            MailMessage(
                message_id=text(row, "message_id", where),
                sender=text(row, "from", where),
                subject=text(row, "subject", where),
                received_at=text(row, "received_at", where),
                body=text(row, "body", where),
            )
            for row in items(read_json(self._inbox_path), "messages", where)
        ]

    def get_message(self, message_id: str) -> MailMessage | None:
        return next((m for m in self.list_messages() if m.message_id == message_id), None)

    def send(self, mail: OutgoingMail) -> str:
        reference = f"mail-{mail.idempotency_key[:12]}"
        self.outbox.append(
            {
                "reference": reference,
                "to": mail.to,
                "subject": mail.subject,
                "body": mail.body,
                "inReplyTo": mail.in_reply_to,
            },
            key=mail.idempotency_key,
        )
        return reference
