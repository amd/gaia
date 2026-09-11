# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Contract tests for the gateway against a REAL Lemonade Server.

The unit tests mock the HTTP layer, so they prove GAIA *called* Lemonade — not
that Lemonade would *accept* the call. The cloud-provider contract lives in
Lemonade's C++ server, so it can only be verified against a running one. This
is the #1655 lesson applied to a new boundary.

Skips automatically when Lemonade is not running or is older than 11.8.0.
Nothing here needs a real gateway or a real token: registration and discovery
are separable, and an unreachable base URL is itself a valid contract case.
"""

import pytest
import requests

from gaia.llm.gateway import (
    GATEWAY_PROVIDER,
    GatewayError,
    GatewayManager,
)
from gaia.version import LEMONADE_GATEWAY_MIN_VERSION

# A syntactically valid endpoint that will never resolve — enough to exercise
# registration and the error path without depending on the AMD network.
UNREACHABLE_BASE_URL = "https://gateway.invalid.example/api/v1"


def _version_tuple(value):
    parts = []
    for chunk in str(value).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0, 0, 0])[:3]


@pytest.fixture(scope="module")
def gateway_manager(require_lemonade):
    """A manager pointed at the live Lemonade, or a skip."""
    manager = GatewayManager()

    try:
        info = requests.get(f"{manager.base_url}/system-info", timeout=10)
    except requests.exceptions.RequestException as e:
        pytest.skip(f"Lemonade not reachable at {manager.base_url}: {e}")

    if info.status_code != 200:
        pytest.skip(f"Lemonade system-info returned HTTP {info.status_code}")

    payload = info.json()
    if "cloud" not in payload:
        pytest.skip(
            f"This Lemonade has no cloud-provider support "
            f"(needs >= {LEMONADE_GATEWAY_MIN_VERSION})"
        )

    version = payload.get("version") or payload.get("server_version")
    if version and _version_tuple(version) < _version_tuple(
        LEMONADE_GATEWAY_MIN_VERSION
    ):
        pytest.skip(f"Lemonade {version} is older than {LEMONADE_GATEWAY_MIN_VERSION}")

    yield manager

    # Leave the developer's Lemonade as we found it.
    try:
        manager.uninstall()
    except GatewayError:
        pass


@pytest.mark.integration
class TestCloudProviderContract:
    def test_lemonade_accepts_the_install_payload_gaia_sends(self, gateway_manager):
        """The assertion the mocks cannot make.

        If Lemonade's cloud-install schema ever changes, this fails here rather
        than as a confusing 400 the first time a user runs `gaia gateway
        install`.
        """
        result = gateway_manager.install(UNREACHABLE_BASE_URL)
        # Registration succeeds even though the URL is unreachable: discovery
        # is best-effort and separate from accepting the provider record.
        assert isinstance(result, dict)

    def test_registered_provider_appears_in_system_info(self, gateway_manager):
        gateway_manager.install(UNREACHABLE_BASE_URL)
        status = gateway_manager.status()

        assert status.installed
        assert status.base_url.rstrip("/") == UNREACHABLE_BASE_URL.rstrip("/")

    def test_auth_endpoint_exists_and_takes_our_payload_shape(self, gateway_manager):
        """Exercises POST /api/v1/cloud/auth for real.

        Lemonade stores the runtime key without validating it upstream, so a
        dummy value is enough to prove the route and body shape. A 409 is an
        equally valid pass — it means LEMONADE_AMD_API_KEY is set in this
        Lemonade's environment, which is the documented precedence.
        """
        gateway_manager.install(UNREACHABLE_BASE_URL)
        try:
            gateway_manager.set_token("integration-test-not-a-real-token")
        except GatewayError as e:
            assert "already set in Lemonade's environment" in str(e), e
            return

        status = gateway_manager.status()
        assert status.runtime_key_set

        gateway_manager.clear_token()
        assert not gateway_manager.status().runtime_key_set

    def test_uninstall_removes_the_provider(self, gateway_manager):
        gateway_manager.install(UNREACHABLE_BASE_URL)
        assert gateway_manager.status().installed

        gateway_manager.uninstall()
        assert not gateway_manager.status().installed

    def test_unreachable_gateway_probe_fails_with_an_actionable_error(
        self, gateway_manager
    ):
        with pytest.raises(GatewayError) as excinfo:
            gateway_manager.check_reachable(UNREACHABLE_BASE_URL)

        message = str(excinfo.value)
        assert UNREACHABLE_BASE_URL in message  # what failed
        assert "network" in message.lower() or "URL" in message  # what to do


@pytest.mark.integration
class TestLocalModelsStillWork:
    def test_registering_a_gateway_does_not_hide_local_models(self, gateway_manager):
        """A cloud provider must not disturb the local catalog."""
        before = {
            m["id"]
            for m in gateway_manager.client.list_models().get("data", [])
            if m.get("recipe") != "cloud"
        }
        gateway_manager.install(UNREACHABLE_BASE_URL)
        after = {
            m["id"]
            for m in gateway_manager.client.list_models().get("data", [])
            if m.get("recipe") != "cloud"
        }
        assert before == after

    def test_no_cloud_models_are_discovered_from_an_unreachable_gateway(
        self, gateway_manager
    ):
        gateway_manager.install(UNREACHABLE_BASE_URL)
        assert gateway_manager.list_models() == []

    def test_provider_namespace_matches_what_gaia_expects(self, gateway_manager):
        """GAIA filters gateway models by the `<provider>.` prefix, so the
        name it registers under and the one it filters on must agree."""
        gateway_manager.install(UNREACHABLE_BASE_URL)
        status = gateway_manager.status()
        assert status.installed
        assert GATEWAY_PROVIDER == "amd"
