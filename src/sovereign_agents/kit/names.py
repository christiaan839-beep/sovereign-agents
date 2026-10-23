# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""Business slugs: the names the core puts into paths and receipts."""

from __future__ import annotations

import re

SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


def check_slug(slug: str) -> str:
    """Return ``slug`` when it is lowercase letters, digits and hyphens, 1 to 63 long.

    Raises ValueError otherwise, so a slug such as ``../x`` never reaches a path.
    """
    if not SLUG.fullmatch(slug):
        raise ValueError(f"{slug!r} is not a business slug (lowercase letters, digits, hyphens)")
    return slug
