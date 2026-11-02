# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Drift guard for the committed ``specification.html`` (#2080).

``specification.html`` is a pure render of
``gaia_agent_email.spec_html.render_endpoint_spec_html()``. Without a guard the
generator and the committed artifact could structurally drift — a later
regeneration would then silently drop any section that lives only in the file.
This mirrors the ``openapi.email.json`` drift test in ``test_rest_contract.py``:
regenerate the spec in-memory and fail loudly if the committed file diverges.
"""

from __future__ import annotations

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip cleanly when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

from gaia_agent_email import spec_html  # noqa: E402


def test_committed_spec_html_artifact_is_up_to_date():
    assert spec_html.check_artifact(), (
        "specification.html is stale — it has drifted from spec_html.py. "
        "Regenerate it with:\n"
        "  python -m gaia_agent_email.spec_html"
    )


def test_check_artifact_fails_when_committed_file_diverges(tmp_path):
    # A file edited on its own (the failure mode #2080 guards against) must be
    # reported stale, not silently accepted.
    stale = tmp_path / "specification.html"
    stale.write_text(
        spec_html.render_endpoint_spec_html() + "<!-- hand edit -->",
        encoding="utf-8",
    )
    assert spec_html.check_artifact(stale) is False


def test_check_artifact_missing_file_is_stale(tmp_path):
    assert spec_html.check_artifact(tmp_path / "does-not-exist.html") is False


def test_write_artifact_round_trips_through_check(tmp_path):
    dest = tmp_path / "specification.html"
    spec_html.write_artifact(dest)
    assert spec_html.check_artifact(dest) is True


def _rendered_paths(html: str) -> set:
    import re

    return set(re.findall(r'<span class="path">([^<]+)</span>', html))


@pytest.mark.parametrize(
    "module",
    ["gaia_agent_email.agent_routes", "gaia_agent_email.connection_intake_routes"],
)
def test_every_router_route_is_in_the_spec(module):
    """The generator is compared only to its own output above, so a route
    missing from both sides (#3321: autonomy/undo, /v1/connections) went unseen.
    Enumerate the real router instead."""
    import importlib

    router = importlib.import_module(module).router
    rendered = _rendered_paths(spec_html.render_endpoint_spec_html())
    actual = {r.path for r in router.routes}
    assert actual, f"{module} router has no routes - enumeration is broken"
    missing = sorted(actual - rendered)
    assert not missing, f"routes missing from specification.html: {missing}"
