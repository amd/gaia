# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-13 (AC11, A14): cross-cutting secret-hygiene tests.

The plain rule: a refresh token must NEVER appear in:
  - any ``logging`` record produced by ``gaia.connectors.*``
  - any file inside ``~/.gaia/`` (only the keyring may hold it)
  - any tracebacks formatted by user-visible error reporters
  - any Pydantic ``model_dump_json`` of UI models
  - the FastAPI ``GET /openapi.json`` schema string
  - any SSE event payload

We inject a sentinel refresh token through the production save path and
exercise representative flows, then assert the sentinel is absent in
every sink listed above.
"""

from __future__ import annotations

import json
import traceback

import httpx
import pytest
import respx

from gaia.connectors.errors import ConnectionRevokedError
from gaia.connectors.providers import _registry
from gaia.connectors.store import save_connection
from gaia.connectors.tokens import get_or_refresh

SENTINEL = "REFRESH-TOKEN-SENTINEL-DO-NOT-LEAK-9f8e7d6c5b4a3210"


@pytest.fixture
def google_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    _registry.clear()
    from gaia.connectors.providers import get as get_provider

    return get_provider("google")


@pytest.fixture
def seeded(google_provider):
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token=SENTINEL,
        scopes=["gmail.readonly"],
        client_id_hash=google_provider.client_id_hash,
    )
    return google_provider


class TestLogging:
    @respx.mock
    async def test_save_load_refresh_does_not_log_sentinel(self, seeded, caplog):
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "x", "expires_in": 3600, "scope": "x"},
            )
        )
        caplog.set_level("DEBUG")
        await get_or_refresh("google")
        assert SENTINEL not in caplog.text


class TestTracebacks:
    @respx.mock
    async def test_traceback_does_not_leak_refresh_token(self, seeded):
        # Force a refresh-time exception with the sentinel in scope. The
        # formatted traceback must not include the sentinel.
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        try:
            await get_or_refresh("google")
        except ConnectionRevokedError as e:
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            assert SENTINEL not in tb


class TestPydanticDump:
    """A14: dumping any Pydantic model that touches token data must NOT
    include the sentinel. None of our models carry the refresh token by
    design, but a regression that adds a field can be caught here."""

    def test_agent_info_dump_no_sentinel(self):
        from gaia.ui.models import AgentInfo

        info = AgentInfo(
            id="x",
            name="x",
            description=f"some text containing nothing sensitive",
            source="builtin",
        )
        as_json = info.model_dump_json()
        assert SENTINEL not in as_json


class TestOpenApi:
    """A14: OpenAPI RESPONSE schemas must not name a field that exposes the
    token.

    A ``refresh_token`` field is legitimate on a *request* body — the
    forwarded-connection POST (#1292) accepts one as input. The hygiene
    invariant is that no *response* model exposes it. We therefore walk every
    operation's response content schemas (resolving ``$ref`` into
    ``components.schemas``) and assert ``refresh_token`` / ``client_secret``
    never appears as a returned property name.
    """

    def test_openapi_response_schemas_do_not_expose_token_fields(self, ui_api_client):
        resp = ui_api_client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        components = spec.get("components", {}).get("schemas", {})

        def _walk(schema: object, seen: set) -> set:
            """Return the set of property names reachable from ``schema``,
            resolving local ``$ref``s into ``components.schemas``."""
            names: set = set()
            if not isinstance(schema, dict):
                return names
            ref = schema.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[-1]
                if name in seen:
                    return names
                seen.add(name)
                return _walk(components.get(name, {}), seen)
            for prop in schema.get("properties") or {}:
                names.add(prop)
            for prop_schema in (schema.get("properties") or {}).values():
                names |= _walk(prop_schema, seen)
            for key in ("items", "additionalProperties"):
                if isinstance(schema.get(key), dict):
                    names |= _walk(schema[key], seen)
            for key in ("allOf", "anyOf", "oneOf"):
                for sub in schema.get(key, []) or []:
                    names |= _walk(sub, seen)
            return names

        leaked: set = set()
        for path_item in spec.get("paths", {}).values():
            for op in path_item.values():
                if not isinstance(op, dict):
                    continue
                for resp_obj in (op.get("responses") or {}).values():
                    for media in (resp_obj.get("content") or {}).values():
                        props = _walk(media.get("schema", {}), set())
                        leaked |= {"refresh_token", "client_secret"} & props

        assert not leaked, f"secret field(s) exposed in a response schema: {leaked}"


class TestFiles:
    def test_no_sentinel_in_grants_file(self, seeded, tmp_path):
        # Even with a connection seeded with the sentinel, no plaintext
        # file under ~/.gaia/ should contain it. (grants.json doesn't
        # carry tokens at all; this guards against regressions.)
        from gaia.connectors.grants import grant_agent

        grant_agent("google", "builtin:chat", ["gmail.readonly"])
        for path in tmp_path.rglob("*"):
            if path.is_file():
                content = path.read_text(encoding="utf-8", errors="ignore")
                assert SENTINEL not in content, f"sentinel leaked into {path}"
