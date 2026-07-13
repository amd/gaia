# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Authoritative version metadata for the GAIA Email Triage agent (#1645).

This is the **single source of truth** for the two version numbers a host needs
to reason about the email surface, sourced once so the product REST server
(``gaia.api.openai_server`` mounting ``api_routes.router``) and the frozen-binary
freeze server (``packaging/server.py``) report identical values:

- ``API_VERSION`` — the REST/contract version a consuming app negotiates against.
  It is the contract's :data:`gaia_agent_email.contract.SCHEMA_VERSION`, so
  **bumping the frozen contract automatically bumps the API version** — they
  cannot drift.
- ``AGENT_VERSION`` — the package build version (the wheel's ``__version__``).
  Pinned here and re-exported as ``gaia_agent_email.__version__`` so the package
  version has one literal home in code.

Dependency-light by construction: this imports ONLY the contract module (pydantic
only), never the Gmail/connector backends, so ``/version`` stays cheap and any
host can read it without pulling live-mail machinery into process.
"""

from __future__ import annotations

from gaia_agent_email.contract import SCHEMA_VERSION

# Package build version. Keep in sync with ``pyproject.toml``'s ``version`` —
# ``test_rest_contract.test_agent_version_matches_package_metadata`` asserts the
# installed distribution metadata agrees with this literal so the two never drift.
AGENT_VERSION = "0.4.0"

# REST/contract version exposed to hosts. Aliased to the frozen contract's
# SCHEMA_VERSION so a contract bump is an API bump — no second number to forget.
API_VERSION = SCHEMA_VERSION

# Minimum Lemonade Server version the triage stack requires (#1795 readiness).
# This is the RUNTIME source of truth for the GET /v1/email/init version check:
# the frozen sidecar bundles this module but NOT ``gaia.installer`` or the
# ``gaia-agent.yaml`` manifest, so the value cannot be read from either at run
# time. ``gaia-agent.yaml``'s ``requirements.min_lemonade_version`` mirrors this
# literal for ``gaia init`` / manifest tooling, and
# ``test_init_endpoint.py::test_min_lemonade_version_locksteps_with_manifest``
# fails if the two drift — keep them in lock-step when bumping (mirrors the
# INIT_PROFILES lock-step note in ``gaia.llm.lemonade_client``).
MIN_LEMONADE_VERSION = "10.2.0"

__all__ = ["AGENT_VERSION", "API_VERSION", "MIN_LEMONADE_VERSION"]
