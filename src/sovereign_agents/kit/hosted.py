# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""The interface for serving a run's tools to a remote engine. No network code lives here.

An engine that runs its agent loop elsewhere (on a model provider's managed agent service, for
instance) reaches the run's tools through a hosted tool server on the business's own host,
over authenticated HTTPS. Any implementation of :class:`HostedToolServer` must:

- expose exactly ``tools.names()`` and dispatch every call through ``ToolSet.call``, so input
  validation, the gates, the fence and the ledger are the same as for an in-process engine;
- refuse a caller that does not hold the run's credential;
- serve nothing for a run once ``close`` has been called for it.

There is no apply tool to serve: a ToolSet holds only read and stage tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .tools import ToolSet


@dataclass(frozen=True)
class HostedEndpoint:
    run_id: str
    url: str


class HostedToolServer(Protocol):
    async def open(self, tools: ToolSet, *, run_id: str) -> HostedEndpoint: ...

    async def close(self, run_id: str) -> None: ...
