# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract test: every field the Agent UI declares on ``AgentInfo`` is sent.

TypeScript cannot see a Python response, so an optional field on the frontend
``AgentInfo`` that no backend endpoint emits compiles clean and reads as
``undefined`` forever — the feature behind it is dead and nothing says so.
#2970 shipped that way (the Hub Details modal read ``version``, the catalog
sends ``installed_version``), and #3842 found two more of the same shape.

The Agent UI now reads agents only from ``GET /api/agents`` (it no longer
merges the Hub catalog), so the contract is pinned to the pydantic model behind
that endpoint. A catalog-only field declared on ``AgentInfo`` would read as
``undefined`` and fails here.
"""

import re
from pathlib import Path

from gaia.ui.models import AgentInfo as BackendAgentInfo

TYPES_TS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "gaia"
    / "apps"
    / "webui"
    / "src"
    / "types"
    / "index.ts"
)

# Fields the frontend synthesizes client-side and no endpoint sends. Each one
# must be documented as such in ``types/index.ts``; adding an entry here is a
# deliberate choice to normalize a field in the UI, not a place to park a
# mismatch. Empty since the catalog-only ``version`` left the type.
CLIENT_DERIVED_FIELDS: set = set()

# Catalog-only fields the UI dropped with the Hub page. Re-declaring one without
# a ``GET /api/agents`` emitter is the #2970 / #3842 regression.
_RETIRED_CATALOG_FIELDS = (
    "version",
    "installed_version",
    "latest_version",
    "status",
    "compatibility",
    "avatar_url",
)

_COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*)")
_TRAILING_COMMENT = re.compile(r"//.*$")
# Any indentation: the depth == 1 guard, not the column, is what restricts this
# to top-level members.
_FIELD_DECL = re.compile(r"^\s+(\w+)\??\s*:")


def _declared_agent_info_fields() -> set:
    """Field names declared on the frontend ``AgentInfo`` interface.

    Parses the interface body by brace depth so only top-level fields count —
    keys of an inline object type (``requirements?: { platforms?: string[] }``)
    belong to that nested shape, not to ``AgentInfo``.
    """
    lines = TYPES_TS.read_text(encoding="utf-8").splitlines()
    try:
        start = next(
            i
            for i, ln in enumerate(lines)
            if ln.startswith("export interface AgentInfo")
        )
    except StopIteration:
        raise AssertionError(
            f"could not find 'export interface AgentInfo' in {TYPES_TS}. If the type "
            "was renamed or moved, update this test — do not delete it (#3842)."
        ) from None

    fields = set()
    depth = 1
    for line in lines[start + 1 :]:
        if _COMMENT_LINE.match(line):
            continue
        # A brace inside a trailing comment would offset the depth counter.
        code = _TRAILING_COMMENT.sub("", line)
        if depth == 1:
            match = _FIELD_DECL.match(code)
            if match:
                fields.add(match.group(1))
        depth += code.count("{") - code.count("}")
        if depth <= 0:
            break
    else:
        raise AssertionError(f"unterminated 'interface AgentInfo' body in {TYPES_TS}")

    assert fields, f"parsed no fields from AgentInfo in {TYPES_TS}"
    return fields


def test_every_declared_agent_info_field_has_a_backend_emitter():
    declared = _declared_agent_info_fields()
    emitted = set(BackendAgentInfo.model_fields)

    unsent = declared - emitted - CLIENT_DERIVED_FIELDS
    assert not unsent, (
        "src/gaia/apps/webui/src/types/index.ts declares AgentInfo field(s) that "
        f"GET /api/agents does not send: {sorted(unsent)}. They will read as "
        "undefined at runtime and any UI behind them is dead (#2970, #3842). Either "
        "emit them from gaia.ui.models.AgentInfo, drop them from the type, or — if "
        "the UI genuinely synthesizes the value — document it in the type and add "
        "it to CLIENT_DERIVED_FIELDS."
    )


def test_client_derived_fields_are_still_declared():
    """Guard the allowlist against rot: a stale entry hides a real mismatch."""
    stale = CLIENT_DERIVED_FIELDS - _declared_agent_info_fields()
    assert not stale, (
        f"CLIENT_DERIVED_FIELDS lists {sorted(stale)} but AgentInfo no longer "
        "declares them — remove the allowlist entries."
    )


def test_retired_catalog_fields_stay_gone():
    """Catalog-only fields must not come back without a real emitter (#3842)."""
    declared = _declared_agent_info_fields()
    for field in _RETIRED_CATALOG_FIELDS:
        assert field not in declared or field in BackendAgentInfo.model_fields, (
            f"AgentInfo declares '{field}' again, but GET /api/agents does not send "
            "it and the Agent UI no longer reads the Hub catalog. Emit it from "
            "gaia.ui.models.AgentInfo first (#2970, #3842)."
        )
