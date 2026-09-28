# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The card a sidecar spec carries must match the package's own (#4161).

A binary install ships no importable wheel, so the entry point's registration
never loads and the Agent UI renders the stand-in that
``AgentRegistry.register_sidecar`` builds from
``gaia.daemon.sidecars.spec``. Core cannot import a hub wheel at runtime, so
that card is transcribed — and a transcription with no guard drifts the first
time someone edits the package and not the spec. The user then sees a
different name, description or starter prompts depending only on whether they
installed the wheel or the binary.

Parametrized over ``builtin_specs()`` rather than written per agent: a new
sidecar must be guarded the day it is added, not the day someone remembers.
Each agent contributes the callable that returns its own registration.

Per agent, the test skips when that wheel is not importable — the comparison
can only run where both halves exist (CI installs them; a binary-only box does
not).
"""

import importlib
import importlib.util

import pytest

from gaia.daemon.sidecars.spec import builtin_specs

#: agent id -> "module:builder" for the package's own registration. Must cover
#: every entry in ``builtin_specs()``; the completeness test below enforces it.
_REGISTRATION_BUILDERS = {
    "gaia": "gaia_agent:build_gaia",
    "email": "gaia_agent_email:build_registration",
}


def test_every_spec_has_a_builder_to_compare_against():
    """Without this, adding a sidecar silently adds an unguarded card."""
    missing = sorted(set(builtin_specs()) - set(_REGISTRATION_BUILDERS))
    assert not missing, (
        f"no registration builder recorded for {missing} — add it to "
        f"_REGISTRATION_BUILDERS so its card is drift-guarded"
    )


@pytest.fixture(params=sorted(builtin_specs()))
def spec_and_registration(request):
    agent_id = request.param
    module_name, builder_name = _REGISTRATION_BUILDERS[agent_id].split(":")
    if importlib.util.find_spec(module_name) is None:
        pytest.skip(f"{module_name} not importable; nothing to compare against")
    module = importlib.import_module(module_name)
    return agent_id, builtin_specs()[agent_id], getattr(module, builder_name)()


def test_name_matches(spec_and_registration):
    _, spec, registration = spec_and_registration
    assert spec.display_name == registration.name


def test_description_matches(spec_and_registration):
    _, spec, registration = spec_and_registration
    assert spec.description == registration.description


def test_conversation_starters_match(spec_and_registration):
    _, spec, registration = spec_and_registration
    assert list(spec.conversation_starters) == list(registration.conversation_starters)


def test_category_tags_and_icon_match(spec_and_registration):
    _, spec, registration = spec_and_registration
    assert spec.category == registration.category
    assert list(spec.tags) == list(registration.tags)
    assert spec.icon == registration.icon


def test_card_is_actually_populated(spec_and_registration):
    """Guards the guard: two empty values also "match"."""
    _, spec, _ = spec_and_registration
    assert spec.description
    assert spec.conversation_starters
    assert spec.icon


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
