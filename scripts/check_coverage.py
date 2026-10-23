# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

"""Hold the critical modules to full branch coverage.

Run it after ``uv run pytest --cov``. It reads the ``.coverage`` data that run wrote and the
``critical`` list in ``pyproject.toml`` (``[tool.sovereign_agents.coverage]``): files, or
folders whose ``.py`` files all count. It prints their coverage and exits 1 when the total is
below ``critical_fail_under``, when a listed path is missing, or when there is no data.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import coverage
from coverage.exceptions import NoDataError

ROOT = Path(__file__).resolve().parent.parent


def critical_files(entries: list[str]) -> tuple[list[str], list[str]]:
    """The files the entries name, and the entries that name nothing."""
    files: list[str] = []
    missing: list[str] = []
    for entry in entries:
        path = ROOT / entry
        if path.is_dir():
            files.extend(str(found) for found in sorted(path.rglob("*.py")))
        elif path.is_file():
            files.append(str(path))
        else:
            missing.append(entry)
    return files, missing


def main() -> int:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    settings = pyproject["tool"]["sovereign_agents"]["coverage"]
    threshold = float(settings["critical_fail_under"])
    files, missing = critical_files(list(settings["critical"]))
    if missing:
        print(f"check_coverage: listed but missing: {', '.join(missing)}", file=sys.stderr)
        return 1
    if not files:
        print("check_coverage: no critical modules listed yet")
        return 0
    cov = coverage.Coverage(
        data_file=str(ROOT / ".coverage"), config_file=str(ROOT / "pyproject.toml")
    )
    cov.load()
    try:
        percent = cov.report(include=files, show_missing=True, file=sys.stdout)
    except NoDataError:
        print("check_coverage: no coverage data; run pytest --cov first", file=sys.stderr)
        return 1
    if percent < threshold:
        print(f"check_coverage: {percent:.2f}% < {threshold:.0f}%", file=sys.stderr)
        return 1
    print(f"check_coverage: {len(files)} critical file(s) at {threshold:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
