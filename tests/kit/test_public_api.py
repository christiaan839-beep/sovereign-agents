# Copyright 2026 Christiaan de Wet / Sovereign Matrix
# SPDX-License-Identifier: Apache-2.0

import sovereign_agents.kit
import sovereign_agents.kit.testing


def test_every_public_name_is_importable_from_the_package() -> None:
    for module in (sovereign_agents.kit, sovereign_agents.kit.testing):
        names = list(module.__all__)
        assert len(names) == len(set(names))
        assert [name for name in names if not hasattr(module, name)] == []


def test_the_public_api_has_no_apply_tool_kind() -> None:
    assert sovereign_agents.kit.ToolKind.__args__ == ("read", "stage")  # type: ignore[attr-defined]
