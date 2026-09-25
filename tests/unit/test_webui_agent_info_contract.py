# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract test: every field the Agent UI declares on ``AgentInfo`` is sent.

TypeScript cannot see a Python response, so an optional field on the frontend
``AgentInfo`` that no backend endpoint emits compiles clean and reads as
``undefined`` forever — the feature behind it is dead and nothing says so.
#2970 shipped that way (the Hub Details modal read ``version``, the catalog
sends ``installed_version``), and #3842 found two more of the same shape.

This test pins the contract from the *real* emitters rather than a hand-built
mock: the pydantic model behind ``GET /api/agents`` and an actual
``merge_with_registry`` payload. Declaring a field neither one produces fails
here instead of silently at runtime.
"""

import inspect
import re
from pathlib import Path

import pytest

from gaia.hub.catalog import merge_with_registry
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
# mismatch.
CLIENT_DERIVED_FIELDS = {
    # mergeCatalogStatus maps the catalog's installed_version onto this so
    # components have one place to read "the version to display" (#3819).
    "version",
}

_COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*)")
_TRAILING_COMMENT = re.compile(r"//.*$")
# Any indentation: the depth == 1 guard, not the column, is what restricts this
# to top-level members.
_FIELD_DECL = re.compile(r"^\s+(\w+)\??\s*:")
# ``merge_with_registry`` emits optional keys with this idiom; see
# _catalog_payload_keys.
_CONDITIONAL_EMIT = re.compile(r'if "(\w+)" in entry:')


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


class _FakeReg:
    """Minimal registry stand-in — ``merge_with_registry`` only calls list()."""

    def list(self):
        return []


def _catalog_payload_keys() -> set:
    """Keys a real ``GET /api/agents/catalog`` agent entry carries.

    Built by running the actual merge over a catalog index entry. The
    unconditional keys are hand-declared below; the conditionally-emitted ones
    are read out of ``merge_with_registry``'s own source, so adding an emit
    there cannot leave this fixture stale and fail the contract on a field that
    is genuinely on the wire.
    """
    entry = {
        "id": "demo",
        "name": "Demo",
        "description": "demo agent",
        "category": "general",
        "type": "agent",
        "latest_version": "1.2.0",
        "icon": "",
        "language": "python",
        "author": "AMD",
        "security_tier": "verified",
        "permissions": ["fs:read"],
        "download_size_bytes": 1000,
        "requirements": {"platforms": ["linux-x64"]},
        "deprecated": False,
        "eval_score": 91,
        "eval_scorecard_url": "https://hub.test/demo/scorecard.md",
    }
    conditional = set(_CONDITIONAL_EMIT.findall(inspect.getsource(merge_with_registry)))
    assert conditional, (
        "found no 'if \"<key>\" in entry:' emits in gaia.hub.catalog."
        "merge_with_registry — the idiom changed, so this test's introspection no "
        "longer sees conditionally-emitted fields and will fail on fields that are "
        "really on the wire. Update _CONDITIONAL_EMIT to match the new idiom."
    )
    for key in conditional:
        entry.setdefault(key, "present")

    merged = merge_with_registry([entry], _FakeReg(), {"demo": "1.1.0"})
    assert len(merged) == 1, merged
    return set(merged[0])


def test_every_declared_agent_info_field_has_a_backend_emitter():
    declared = _declared_agent_info_fields()
    emitted = set(BackendAgentInfo.model_fields) | _catalog_payload_keys()

    unsent = declared - emitted - CLIENT_DERIVED_FIELDS
    assert not unsent, (
        "src/gaia/apps/webui/src/types/index.ts declares AgentInfo field(s) that no "
        f"backend endpoint sends: {sorted(unsent)}. They will read as undefined at "
        "runtime and any UI behind them is dead (#2970, #3842). Either emit them from "
        "gaia.ui.models.AgentInfo / gaia.hub.catalog.merge_with_registry, drop them "
        "from the type, or — if the UI genuinely synthesizes the value — document it "
        "in the type and add it to CLIENT_DERIVED_FIELDS."
    )


@pytest.mark.parametrize("field", sorted(CLIENT_DERIVED_FIELDS))
def test_client_derived_fields_are_still_declared(field):
    """Guard the allowlist against rot: a stale entry hides a real mismatch."""
    assert field in _declared_agent_info_fields(), (
        f"CLIENT_DERIVED_FIELDS lists '{field}' but AgentInfo no longer declares it — "
        "remove the allowlist entry."
    )


def test_the_two_fields_from_issue_3842_stay_gone():
    """``compatibility``/``avatar_url`` were declared but never emitted (#3842).

    Explicit because re-adding either is the exact regression: both were read
    defensively, so the UI degraded quietly instead of failing.
    """
    declared = _declared_agent_info_fields()
    for field in ("compatibility", "avatar_url"):
        assert field not in declared or field in _catalog_payload_keys(), (
            f"AgentInfo declares '{field}' again without a backend emitter. Implement "
            "it in gaia.hub.catalog.merge_with_registry first (#3842)."
        )
