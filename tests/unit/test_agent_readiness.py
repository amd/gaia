# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for inherited agent init (``gaia.agents.base.readiness``).

Covers the readiness probe, the provisioning stream, and the routes
:class:`~gaia.agents.base.server.AgentServer` mounts from a declaration —
without a live Lemonade server.

Two properties these tests exist to defend:

* **Indeterminate is never a pass.** A backend that does not advertise a
  version, or a model list that cannot be read, must be reported as unknown —
  not as ready, and not as missing.
* **The agent never installs the backend.** An unreachable backend fails loudly
  and names whose job the fix is; nothing is pulled.
"""

import pytest
import requests

from gaia.agents.base.agent import Agent
from gaia.agents.base.readiness import (
    AgentRequirements,
    compute_init_status,
    extract_loaded_ctx,
    parse_version,
    provision_progress,
    resolve_probe_base,
    version_meets_min,
)
from gaia.agents.base.server import AgentServer

MODULE = "gaia.agents.base.readiness"


class FakeAgent(Agent):
    """Minimal Agent that skips the heavy LLM/Lemonade ``__init__``."""

    def __init__(self):  # noqa: D401 - deliberately skip super().__init__
        pass

    def _register_tools(self):
        pass

    def process_query(self, user_input, **kwargs):
        return {"status": "success", "result": user_input}

    def get_tools_info(self):
        return {}


@pytest.fixture
def reqs():
    return AgentRequirements(model_id="Test-Model", min_backend_version="10.2.0")


def _patch_health(monkeypatch, reachable=True, version="10.10.0", loaded=None):
    monkeypatch.setattr(
        f"{MODULE}.probe_backend_health",
        lambda base_url=None: (
            reachable,
            "http://localhost:13305/api/v1",
            version,
            loaded or [],
        ),
    )


def _patch_present(monkeypatch, present=True, raises=None):
    def _probe(probe_base, model_id):
        if raises is not None:
            raise raises
        return present

    monkeypatch.setattr(f"{MODULE}.probe_model_present", _probe)


# ---------------------------------------------------------------------------
# Version comparison — indeterminate is not a pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "found,minimum,expected",
    [
        ("10.10.0", "10.2.0", True),
        ("v10.10.0", "10.2.0", True),
        ("10.2.0", "10.2.0", True),
        ("10.1.9", "10.2.0", False),
        ("9.1.4", "10.2.0", False),
        (None, "10.2.0", None),  # server advertised nothing
        ("not-a-version", "10.2.0", None),  # unparseable
        ("10.10.0", None, None),  # agent declared no minimum
    ],
)
def test_version_meets_min(found, minimum, expected):
    assert version_meets_min(found, minimum) is expected


def test_parse_version_ignores_trailing_parts():
    assert parse_version("10.10.0.1234") == (10, 10, 0)


# ---------------------------------------------------------------------------
# Loaded-context extraction — report, never guess
# ---------------------------------------------------------------------------


def test_extract_loaded_ctx_matches_model_name():
    loaded = [{"model_name": "Test-Model", "recipe_options": {"ctx_size": 65536}}]
    assert extract_loaded_ctx(loaded, "Test-Model") == 65536


def test_extract_loaded_ctx_matches_checkpoint():
    loaded = [{"checkpoint": "Test-Model", "recipe_options": {"ctx_size": 4096}}]
    assert extract_loaded_ctx(loaded, "Test-Model") == 4096


@pytest.mark.parametrize(
    "entry",
    [
        {"model_name": "Other-Model", "recipe_options": {"ctx_size": 4096}},
        {"model_name": "Test-Model"},  # no recipe_options
        {"model_name": "Test-Model", "recipe_options": {}},  # no ctx_size
        {"model_name": "Test-Model", "recipe_options": {"ctx_size": 0}},
        {"model_name": "Test-Model", "recipe_options": {"ctx_size": True}},  # bool
    ],
)
def test_extract_loaded_ctx_returns_none_rather_than_guessing(entry):
    assert extract_loaded_ctx([entry], "Test-Model") is None


# ---------------------------------------------------------------------------
# Probe base resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("http://host:1234", "http://host:1234/api/v1"),
        ("http://host:1234/", "http://host:1234/api/v1"),
        ("http://host:1234/api/v1", "http://host:1234/api/v1"),
    ],
)
def test_resolve_probe_base_normalises(given, expected):
    assert resolve_probe_base(given) == expected


# ---------------------------------------------------------------------------
# Readiness — the failure ladder, in dependency order
# ---------------------------------------------------------------------------


def test_unreachable_backend_names_whose_job_the_fix_is(monkeypatch, reqs):
    _patch_health(monkeypatch, reachable=False, version=None)
    status = compute_init_status(reqs, "Test Agent")

    assert status.ready is False
    assert status.lemonade.reachable is False
    # Indeterminate, not a failure: an unreachable server has no version to compare.
    assert status.lemonade.compatible is None
    # A real next step, asserted as "names the one command that always exists"
    # rather than as a launch command. Pinning a literal launcher here is
    # precisely how `lemonade-server serve` stayed asserted-as-real for
    # releases after Lemonade 10.7 removed it — the manual fallback is resolved
    # per machine (CLAUDE.md, "Never hardcode how Lemonade is started"), so
    # there is no single launch string to assert.
    assert "gaia daemon start" in status.hint
    # The boundary must be explicit — the agent cannot bootstrap the backend.
    assert "host prerequisite" in status.hint


def test_unreachable_backend_never_probes_for_the_model(monkeypatch, reqs):
    _patch_health(monkeypatch, reachable=False, version=None)

    def _boom(probe_base, model_id):
        raise AssertionError("must not probe the model list on a dead backend")

    monkeypatch.setattr(f"{MODULE}.probe_model_present", _boom)
    assert compute_init_status(reqs).ready is False


def test_version_too_old_is_reported_before_a_missing_model(monkeypatch, reqs):
    """Upgrading the backend comes first even when the model is also missing.

    The other order would send the user to download gigabytes that the old
    server may then refuse to serve.
    """
    _patch_health(monkeypatch, version="10.1.0")
    _patch_present(monkeypatch, present=False)

    status = compute_init_status(reqs, "Test Agent")

    assert status.ready is False
    assert status.lemonade.compatible is False
    assert "older than the required 10.2.0" in status.hint
    assert "not downloaded" not in status.hint


def test_missing_model_hint_offers_both_remedies(monkeypatch, reqs):
    _patch_health(monkeypatch)
    _patch_present(monkeypatch, present=False)

    status = compute_init_status(reqs, "Test Agent")

    assert status.ready is False
    assert status.model.present is False
    assert "gaia init" in status.hint
    assert "POST" in status.hint  # the pull-it-here path


def test_unreadable_model_list_is_not_reported_as_missing(monkeypatch, reqs):
    """ "Could not tell" and "absent" have different remedies.

    Reporting an unreadable list as a missing model sends the user to
    re-download something they may already have.
    """
    _patch_health(monkeypatch)
    _patch_present(monkeypatch, raises=requests.exceptions.ConnectionError("boom"))

    status = compute_init_status(reqs, "Test Agent")

    assert status.ready is False
    assert "model list" in status.hint
    assert "could not be read" in status.hint
    assert "not downloaded" not in status.hint


def test_indeterminate_version_does_not_block_readiness(monkeypatch, reqs):
    """A server that advertises no version is unknown, not incompatible."""
    _patch_health(monkeypatch, version=None)
    _patch_present(monkeypatch, present=True)

    status = compute_init_status(reqs, "Test Agent")

    assert status.ready is True
    assert status.lemonade.compatible is None


def test_ready_reports_loaded_context_and_no_hint(monkeypatch, reqs):
    _patch_health(
        monkeypatch,
        loaded=[{"model_name": "Test-Model", "recipe_options": {"ctx_size": 65536}}],
    )
    _patch_present(monkeypatch, present=True)

    status = compute_init_status(reqs, "Test Agent")

    assert status.ready is True
    assert status.model.ctx_size == 65536
    assert status.hint is None


def test_agent_with_no_declared_model_is_ready_on_a_live_backend(monkeypatch):
    """A model-less agent's readiness is just "the backend is up and current"."""
    _patch_health(monkeypatch)

    def _boom(probe_base, model_id):
        raise AssertionError("must not probe a model the agent never declared")

    monkeypatch.setattr(f"{MODULE}.probe_model_present", _boom)

    status = compute_init_status(
        AgentRequirements(min_backend_version="10.2.0"), "Test Agent"
    )
    assert status.ready is True


def test_model_less_agent_still_fails_an_old_backend(monkeypatch):
    _patch_health(monkeypatch, version="9.1.4")
    status = compute_init_status(
        AgentRequirements(min_backend_version="10.2.0"), "Test Agent"
    )
    assert status.ready is False
    assert "older than the required" in status.hint


# ---------------------------------------------------------------------------
# Provisioning — the final line is the verdict
# ---------------------------------------------------------------------------


def _final(lines):
    return [ln for ln in lines if ln.strip()][-1]


def test_provision_skips_the_pull_when_the_model_is_present(monkeypatch):
    _patch_present(monkeypatch, present=True)
    monkeypatch.setattr(
        f"{MODULE}.pull_model",
        lambda *a: pytest.fail("must not pull a model that is already present"),
    )

    lines = list(provision_progress("http://b/api/v1", "Test-Model", "Test Agent"))

    assert "already downloaded" in "".join(lines)
    assert _final(lines).startswith("✓")


def test_provision_pulls_then_verifies(monkeypatch):
    calls = {"pull": 0, "present": 0}

    def _present(probe_base, model_id):
        calls["present"] += 1
        # Absent on the pre-check, present on the post-pull verify.
        return calls["present"] > 1

    monkeypatch.setattr(f"{MODULE}.probe_model_present", _present)
    monkeypatch.setattr(
        f"{MODULE}.pull_model", lambda *a: calls.__setitem__("pull", calls["pull"] + 1)
    )

    lines = list(provision_progress("http://b/api/v1", "Test-Model", "Test Agent"))

    assert calls["pull"] == 1
    assert "Verified" in "".join(lines)
    assert _final(lines).startswith("✓")


def test_failed_pull_ends_on_a_failure_line(monkeypatch):
    """The stream is a committed 200, so the final line has to carry the verdict."""
    _patch_present(monkeypatch, present=False)

    def _pull(probe_base, model_id):
        raise requests.exceptions.HTTPError("400 Client Error")

    monkeypatch.setattr(f"{MODULE}.pull_model", _pull)

    lines = list(provision_progress("http://b/api/v1", "Test-Model", "Test Agent"))

    assert _final(lines).startswith("✗")
    assert "was not downloaded" in "".join(lines)


def test_pull_that_does_not_register_is_a_failure_not_a_success(monkeypatch):
    """Lemonade reporting OK is not proof the model landed."""
    monkeypatch.setattr(f"{MODULE}.probe_model_present", lambda *a: False)
    monkeypatch.setattr(f"{MODULE}.pull_model", lambda *a: None)

    lines = list(provision_progress("http://b/api/v1", "Test-Model", "Test Agent"))

    assert _final(lines).startswith("✗")
    assert "still not listed" in "".join(lines)


def test_unverifiable_pull_warns_rather_than_claiming_success(monkeypatch):
    calls = {"n": 0}

    def _present(probe_base, model_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return False
        raise requests.exceptions.ConnectionError("verify read failed")

    monkeypatch.setattr(f"{MODULE}.probe_model_present", _present)
    monkeypatch.setattr(f"{MODULE}.pull_model", lambda *a: None)

    body = "".join(provision_progress("http://b/api/v1", "Test-Model", "Test Agent"))

    assert "⚠" in body


def test_unreadable_list_aborts_before_pulling(monkeypatch):
    _patch_present(monkeypatch, raises=requests.exceptions.ConnectionError("boom"))
    monkeypatch.setattr(
        f"{MODULE}.pull_model",
        lambda *a: pytest.fail("must not pull when the list could not be read"),
    )

    lines = list(provision_progress("http://b/api/v1", "Test-Model", "Test Agent"))
    assert _final(lines).startswith("✗")


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


def test_from_manifest_reads_the_declared_model():
    from gaia.hub.manifest import AgentManifest

    manifest = AgentManifest.from_dict(
        {
            "id": "declared",
            "name": "Declared",
            "version": "0.1.0",
            "description": "d",
            "author": "AMD",
            "license": "MIT",
            "language": "python",
            "models": ["Declared-Model"],
            "interfaces": {"api_server": True},
        }
    )
    assert AgentRequirements.from_manifest(manifest).model_id == "Declared-Model"


def test_from_manifest_without_models_declares_nothing():
    from gaia.hub.manifest import AgentManifest

    manifest = AgentManifest.from_dict(
        {
            "id": "bare",
            "name": "Bare",
            "version": "0.1.0",
            "description": "d",
            "author": "AMD",
            "license": "MIT",
            "language": "python",
            "interfaces": {"api_server": True},
        }
    )
    assert AgentRequirements.from_manifest(manifest).model_id is None


# ---------------------------------------------------------------------------
# The routes AgentServer mounts from a declaration
# ---------------------------------------------------------------------------


def _client(**kwargs):
    from fastapi.testclient import TestClient

    server = AgentServer(FakeAgent(), name="Test Agent", **kwargs)
    return TestClient(server.build_api_app(), raise_server_exceptions=False)


def test_no_declaration_means_no_init_route():
    """An /init that answers "ready" without checking anything is worse than a 404."""
    assert _client().get("/v1/fake/init").status_code == 404


def test_empty_declaration_is_treated_as_no_declaration():
    """A manifest with no models and no minimum version declares nothing.

    Mounting /init for it would answer "ready" the moment the backend is up,
    having verified nothing about the agent — and a consumer would believe it.
    """
    empty = AgentRequirements()
    assert empty.declares_anything() is False
    assert (
        _client(requirements=empty, agent_id="fake").get("/v1/fake/init").status_code
        == 404
    )


def test_base_url_alone_does_not_count_as_a_declaration():
    """It says where to look, not what is required."""
    assert AgentRequirements(base_url="http://host/api/v1").declares_anything() is False


@pytest.mark.parametrize(
    "requirements",
    [
        AgentRequirements(model_id="Test-Model"),
        AgentRequirements(min_backend_version="10.2.0"),
    ],
)
def test_either_half_of_a_declaration_is_enough(requirements):
    assert requirements.declares_anything() is True


def test_manifest_without_models_mounts_no_init_route():
    """The manifest path must obey the same rule as the explicit one."""
    from gaia.hub.manifest import AgentManifest

    manifest = AgentManifest.from_dict(
        {
            "id": "bare",
            "name": "Bare",
            "version": "0.1.0",
            "description": "d",
            "author": "AMD",
            "license": "MIT",
            "language": "python",
            "interfaces": {"api_server": True},
        }
    )
    from fastapi.testclient import TestClient

    server = AgentServer(FakeAgent(), manifest=manifest)
    client = TestClient(server.build_api_app(), raise_server_exceptions=False)
    assert client.get("/v1/bare/init").status_code == 404


def test_model_less_agent_names_that_no_model_is_required(monkeypatch):
    _patch_health(monkeypatch)
    status = compute_init_status(
        AgentRequirements(min_backend_version="10.2.0"), "Test Agent"
    )
    assert status.ready is True
    # present=True so a consumer's "is the model downloaded?" check does not
    # fail over a model that was never required; the id says there is none.
    assert status.model.present is True
    assert status.model.id == "(none required)"


def test_declared_requirements_mount_the_route(monkeypatch, reqs):
    _patch_health(monkeypatch)
    _patch_present(monkeypatch, present=True)

    resp = _client(requirements=reqs, agent_id="fake").get("/v1/fake/init")

    assert resp.status_code == 200
    assert resp.json()["ready"] is True


def test_not_ready_is_served_as_503_with_the_same_body(monkeypatch, reqs):
    _patch_health(monkeypatch, reachable=False, version=None)

    resp = _client(requirements=reqs, agent_id="fake").get("/v1/fake/init")

    assert resp.status_code == 503
    body = resp.json()
    # Same shape as the 200 — a consumer parses one contract, not two.
    assert body["ready"] is False
    assert set(body) == {"ready", "lemonade", "model", "hint"}
    assert body["hint"]


def test_agent_id_defaults_to_the_manifest_id(reqs):
    from gaia.hub.manifest import AgentManifest

    manifest = AgentManifest.from_dict(
        {
            "id": "from-manifest",
            "name": "M",
            "version": "0.1.0",
            "description": "d",
            "author": "AMD",
            "license": "MIT",
            "language": "python",
            "interfaces": {"api_server": True},
        }
    )
    server = AgentServer(FakeAgent(), manifest=manifest, requirements=reqs)
    assert server.agent_id == "from-manifest"


def test_provision_route_refuses_with_503_when_the_backend_is_down(monkeypatch, reqs):
    _patch_health(monkeypatch, reachable=False, version=None)
    monkeypatch.setattr(
        f"{MODULE}.pull_model",
        lambda *a: pytest.fail("must not pull against an unreachable backend"),
    )

    resp = _client(requirements=reqs, agent_id="fake").post("/v1/fake/init")

    assert resp.status_code == 503
    assert "host prerequisite" in resp.text


def test_provision_route_streams_progress(monkeypatch, reqs):
    _patch_health(monkeypatch)
    _patch_present(monkeypatch, present=True)

    resp = _client(requirements=reqs, agent_id="fake").post("/v1/fake/init")

    assert resp.status_code == 200
    assert resp.text.strip().splitlines()[-1].startswith("✓")
