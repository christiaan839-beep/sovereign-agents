"""The jobs, by name. Each reads, stages and never applies on its own."""

from __future__ import annotations

from .spec import JobSpec
from .supplier_reorders import SPEC as SUPPLIER_REORDERS

JOBS: dict[str, JobSpec] = {SUPPLIER_REORDERS.name: SUPPLIER_REORDERS}
