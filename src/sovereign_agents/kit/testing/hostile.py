# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""The attack suite's hostile texts, shared by every job's attack tests.

Each text names its technique and the fragments that must not reach the model after the fence.
A body may hold ``{fence}``, which :meth:`HostileText.body_for` replaces with a job's fence
label, so a forged closing tag targets that job's own fence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files


@dataclass(frozen=True)
class HostileText:
    id: str
    technique: str
    sender: str
    subject: str
    body: str
    must_not_survive: tuple[str, ...]

    def body_for(self, fence_label: str) -> str:
        return self.body.replace("{fence}", fence_label)


def hostile_mail() -> list[HostileText]:
    """The hostile emails, in a fixed order."""
    document = json.loads(files(__package__).joinpath("hostile_mail.json").read_text("utf-8"))
    return [
        HostileText(
            id=entry["id"],
            technique=entry["technique"],
            sender=entry["from"],
            subject=entry["subject"],
            body=entry["body"],
            must_not_survive=tuple(entry["must_not_survive"]),
        )
        for entry in document["mail"]
    ]
