# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The /install and /uninstall request contract, checked against a live server.

``tests/unit/test_npu_device_support.py`` mocks ``_send_request``, so it can only
prove the client *called* the endpoint -- it happily passed while the client sent
``{"spec": "flm:npu"}``, which every Lemonade build rejects with a 400. That
silently broke ``gaia init --profile npu`` and the NPU CI job.

These tests talk to a real server, so they fail if the contract moves.
"""

import pytest
import requests

from gaia.llm.lemonade_client import LemonadeClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    return LemonadeClient()


def _installed_backend(client: LemonadeClient):
    """An (recipe, backend) pair the live server reports as already installed."""
    recipes = client.get_system_info().get("recipes", {}) or {}
    for recipe, info in sorted(recipes.items()):
        for backend, state in (info.get("backends") or {}).items():
            if state.get("state") == "installed":
                return recipe, backend
    return None


def test_combined_spec_field_is_rejected(client, require_lemonade):
    """Pins *why* the client splits the spec: a combined field is a 400."""
    response = requests.post(
        f"{client.base_url}/install", json={"spec": "flm:npu"}, timeout=30
    )

    assert response.status_code == 400, (
        "A combined 'spec' field was accepted -- if the server now supports it, "
        "LemonadeClient._split_backend_spec can be simplified"
    )
    assert "recipe" in response.text and "backend" in response.text


def test_install_backend_request_is_accepted(client, require_lemonade):
    """The client's real request shape round-trips against a live server.

    Re-installing an already-installed backend is the idempotent no-op the
    server offers, so this asserts validity without changing machine state.
    """
    pair = _installed_backend(client)
    if pair is None:
        pytest.skip("no already-installed backend to exercise idempotently")
    recipe, backend = pair

    result = client.install_backend(f"{recipe}:{backend}")

    assert result.get("status") == "success", result
    assert result.get("recipe") == recipe
    assert result.get("backend") == backend
