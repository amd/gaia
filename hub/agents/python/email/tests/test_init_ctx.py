# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TDD pin for GET /v1/email/init reporting the loaded model's ctx_size (#1892).

``InitModelStatus`` (``gaia_agent_email.api_routes``, ``_Strict``/extra="forbid")
does not yet carry a ``ctx_size`` field. This file is the RED-first pin for the
planned extension: ``_probe_lemonade_health`` parses the loaded-models list in
the raw ``/health`` body (``all_models_loaded[]``) and, when the resolved
triage model is currently loaded, ``_compute_init_status`` reports its
``recipe_options.ctx_size``. No config echo, no guessing — ``None`` whenever
the server doesn't currently show the model loaded with a ctx.

Seam facts locked in by these tests:
- raw ``/health`` entries carry ``model_name`` / ``checkpoint`` — there is NO
  ``id`` key at that layer — plus ``recipe_options: {"ctx_size": <int>}``.
- matching the target model against ``all_models_loaded[].model_name`` must
  tolerate the ``user.`` prefix (via ``_model_ids_match``).

Today every case here fails because ``InitModelStatus`` has no ``ctx_size``
attribute at all (``AttributeError`` on ``resp.model.ctx_size``) — expected
and correct until the feature lands.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# parents[0] = tests/, [1] = email/, [2] = python/, [3] = agents/, [4] = hub/, [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")


def _fake_get_factory(*, health_body, models_present):
    """Build a ``requests.get`` stub serving ``/health`` and ``/models``.

    ``models_present`` controls whether ``/models`` lists the resolved model
    id (mirrors the existing pattern in test_email_agent.py:197-263).
    """

    def _fake_get(url, *args, **kwargs):
        from gaia_agent_email.api_routes import _resolve_email_model_id

        if url.endswith("/health"):
            resp = MagicMock(status_code=200)
            resp.json.return_value = health_body
            return resp
        if url.endswith("/models"):
            model_id = _resolve_email_model_id()
            resp = MagicMock(status_code=200)
            resp.json.return_value = {
                "data": [{"id": model_id}] if models_present else []
            }
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    return _fake_get


def test_ctx_size_reported_when_model_loaded(monkeypatch):
    """The exact model id is loaded with a ctx_size in /health → reported."""
    import requests
    from gaia_agent_email.api_routes import (
        _compute_init_status,
        _resolve_email_model_id,
    )

    model_id = _resolve_email_model_id()
    health_body = {
        "version": "10.10.0",
        "all_models_loaded": [
            {
                "model_name": model_id,
                "checkpoint": "x",
                "recipe_options": {"ctx_size": 16384},
            }
        ],
    }
    monkeypatch.setattr(
        requests, "get", _fake_get_factory(health_body=health_body, models_present=True)
    )

    resp = _compute_init_status()

    assert resp.model.ctx_size == 16384


def test_ctx_size_reported_when_loaded_under_user_prefix(monkeypatch):
    """A ``user.``-prefixed /health entry must still tolerantly match and
    report ctx_size (via ``_model_ids_match``)."""
    import requests
    from gaia_agent_email.api_routes import (
        _compute_init_status,
        _resolve_email_model_id,
    )

    model_id = _resolve_email_model_id()
    health_body = {
        "version": "10.10.0",
        "all_models_loaded": [
            {
                "model_name": f"user.{model_id}",
                "checkpoint": "x",
                "recipe_options": {"ctx_size": 16384},
            }
        ],
    }
    monkeypatch.setattr(
        requests, "get", _fake_get_factory(health_body=health_body, models_present=True)
    )

    resp = _compute_init_status()

    assert resp.model.ctx_size == 16384


def test_ctx_size_none_when_model_absent(monkeypatch):
    """Model not currently loaded (and not downloaded) → ctx_size is None."""
    import requests
    from gaia_agent_email.api_routes import _compute_init_status

    health_body = {"version": "10.10.0", "all_models_loaded": []}
    monkeypatch.setattr(
        requests,
        "get",
        _fake_get_factory(health_body=health_body, models_present=False),
    )

    resp = _compute_init_status()

    assert resp.model.ctx_size is None


def test_ctx_size_none_when_lemonade_unreachable(monkeypatch):
    """A transport failure on /health → ctx_size is None and ready is False."""
    import requests
    from gaia_agent_email.api_routes import _compute_init_status

    def _fake_get(url, *args, **kwargs):
        raise requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(requests, "get", _fake_get)

    resp = _compute_init_status()

    assert resp.model.ctx_size is None
    assert resp.ready is False


def test_ctx_size_none_when_recipe_options_missing_ctx(monkeypatch):
    """Entry present but ``recipe_options`` lacks a ``ctx_size`` key (or is
    absent entirely) → report only what the server says: None."""
    import requests
    from gaia_agent_email.api_routes import (
        _compute_init_status,
        _resolve_email_model_id,
    )

    model_id = _resolve_email_model_id()
    health_body = {
        "version": "10.10.0",
        "all_models_loaded": [
            {
                "model_name": model_id,
                "checkpoint": "x",
                # No "recipe_options" key at all.
            }
        ],
    }
    monkeypatch.setattr(
        requests, "get", _fake_get_factory(health_body=health_body, models_present=True)
    )

    resp = _compute_init_status()

    assert resp.model.ctx_size is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
