"""The repo's standing rules, checked on every tracked file and every import."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import sovereign_agents.kit
import sovereign_agents.kit.testing

ROOT = Path(__file__).resolve().parent.parent

# Package and repo names that start with the prefix and are not model IDs.
ALLOWED_PREFIXED = {"claude-agent-sdk", "claude-plugins-official"}
# Model IDs are lowercase, so "Claude-Session" in a commit trailer is not one.
PREFIXED = re.compile(r"\bclaude-[a-z0-9][a-z0-9.-]*")
# The model family names, each built in two parts so this file does not contain them.
MODEL_NAMES = re.compile(
    r"\b(" + "|".join(("op" + "us", "son" + "net", "hai" + "ku")) + r")\b", re.IGNORECASE
)
# Secret shapes, each built in parts so this file does not contain what it looks for: a PEM
# private key, an Anthropic API key, a GitHub token, an AWS access key id and a Slack token.
SECRET_SHAPES = re.compile(
    "|".join(
        (
            re.escape("PRIVATE " + "KEY-----"),
            "sk" + r"-ant-[A-Za-z0-9_-]{20,}",
            "gh" + r"[pousr]_[A-Za-z0-9]{36}",
            "AK" + r"IA[0-9A-Z]{16}",
            "xo" + r"x[abprs]-[A-Za-z0-9-]{10,}",
        )
    )
)
# The kit's public API: the package and its testing helpers, by the names in __all__.
KIT = "sovereign_agents.kit"
KIT_API = {
    "sovereign_agents.kit": set(sovereign_agents.kit.__all__),
    "sovereign_agents.kit.testing": set(sovereign_agents.kit.testing.__all__),
}
# What the kit never imports: the jobs, connectors and engines around it, and any engine's SDK.
KIT_FORBIDDEN = ("sovereign_agents", "claude_agent_sdk")
SPDX = "# SPDX-License-Identifier: Apache-2.0"
COPYRIGHT = re.compile(r"^# Copyright 2026 (Christiaan de Wet / Sovereign Matrix|Anthropic PBC)$")
KIT_DIRS = ("src/sovereign_agents/kit", "tests/kit", "evals")


def tracked_files() -> list[Path]:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run([git, "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [ROOT / name for name in out.decode("utf-8").split("\0") if name]


def tracked_texts() -> list[tuple[str, str]]:
    return [
        (str(path.relative_to(ROOT)), path.read_text("utf-8", errors="replace"))
        for path in tracked_files()
        if path.is_file()
    ]


def test_no_model_id_in_any_tracked_file() -> None:
    found = [
        f"{name}: {token}"
        for name, text in tracked_texts()
        for token in PREFIXED.findall(text)
        if token.rstrip(".-") not in ALLOWED_PREFIXED
    ]
    assert found == []


def test_no_model_name_in_any_tracked_file() -> None:
    found = [
        f"{name}: {match.group(0)}"
        for name, text in tracked_texts()
        for match in MODEL_NAMES.finditer(text)
    ]
    assert found == []


def test_no_secret_shaped_string_in_any_tracked_file() -> None:
    found = [name for name, text in tracked_texts() if SECRET_SHAPES.search(text)]
    assert found == []


def imports(path: Path) -> list[tuple[str, str]]:
    """Each absolute import in a file, as (module, name); name is empty for ``import x``."""
    pairs: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            pairs += [(alias.name, "") for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            pairs += [(node.module, alias.name) for alias in node.names]
    return pairs


def in_kit(path: Path) -> bool:
    return any(path.is_relative_to(ROOT / folder) for folder in KIT_DIRS)


def test_the_kit_imports_neither_the_jobs_nor_an_engine_sdk() -> None:
    found = [
        f"{path.relative_to(ROOT)}: {module}"
        for path in sorted((ROOT / "src" / "sovereign_agents" / "kit").rglob("*.py"))
        for module, _ in imports(path)
        if module.split(".")[0] in KIT_FORBIDDEN and not module.startswith(KIT)
    ]
    assert found == []


def test_everything_outside_the_kit_uses_only_its_public_api() -> None:
    found = []
    python_files = sorted(ROOT.glob("src/**/*.py")) + sorted(ROOT.glob("tests/**/*.py"))
    for path in [ROOT / "conftest.py", *python_files]:
        if in_kit(path):
            continue
        for module, name in imports(path):
            if module != KIT and not module.startswith(KIT + "."):
                continue
            if module not in KIT_API or (name and name not in KIT_API[module]):
                found.append(f"{path.relative_to(ROOT)}: {module} {name}".rstrip())
    assert found == []


def test_every_kit_python_file_carries_its_licence_header() -> None:
    missing = []
    for path in tracked_files():
        if path.suffix != ".py" or not path.is_file() or not in_kit(path):
            continue
        lines = path.read_text("utf-8").splitlines()[:2]
        if len(lines) < 2 or not COPYRIGHT.match(lines[0]) or lines[1] != SPDX:
            missing.append(str(path.relative_to(ROOT)))
    assert missing == []
