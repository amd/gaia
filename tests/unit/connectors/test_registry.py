# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-1 unit tests — ConnectorSpec, ConfigField, ConnectorRegistry.

Tests focus on:
- spec construction and immutability (frozen dataclass)
- ConfigField validation
- ConnectorRegistry id-uniqueness (plan amendment A7)
- Registry freeze prevents mutation
- Registry read path: get / all / contains / len
"""

from __future__ import annotations

import pytest

from gaia.connectors.registry import ConnectorRegistry
from gaia.connectors.spec import ConfigField, ConnectorSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(
    id: str = "test",
    type: str = "oauth_pkce",
    tier: int = 10,
    **kwargs,
) -> ConnectorSpec:
    return ConnectorSpec(
        id=id,
        display_name="Test Connector",
        icon="🔗",
        category="test",
        tier=tier,
        type=type,  # type: ignore[arg-type]
        description="A test connector",
        **kwargs,
    )


def _mcp_spec(id: str = "test-mcp") -> ConnectorSpec:
    return ConnectorSpec(
        id=id,
        display_name="Test MCP",
        icon="🔌",
        category="tools",
        tier=5,
        type="mcp_server",
        description="A test MCP server",
        mcp_command="npx",
        mcp_args=("-y", "test-mcp"),
        mcp_env_keys=("TEST_TOKEN",),
    )


# ---------------------------------------------------------------------------
# ConfigField
# ---------------------------------------------------------------------------


class TestConfigField:
    def test_basic_construction(self):
        f = ConfigField(key="client_id", label="Client ID", kind="text")
        assert f.key == "client_id"
        assert f.label == "Client ID"
        assert f.kind == "text"
        assert f.required is True
        assert f.secret is False
        assert f.options is None

    def test_secret_kind(self):
        f = ConfigField(key="token", label="API Token", kind="secret", secret=True)
        assert f.secret is True

    def test_options_normalized_to_tuple(self):
        f = ConfigField(
            key="region", label="Region", kind="select", options=["us", "eu"]
        )
        assert isinstance(f.options, tuple)
        assert f.options == ("us", "eu")

    def test_empty_key_raises(self):
        with pytest.raises(ValueError, match="key must not be empty"):
            ConfigField(key="", label="X", kind="text")

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError, match="kind"):
            ConfigField(key="x", label="X", kind="radio")  # type: ignore[arg-type]

    def test_frozen(self):
        f = ConfigField(key="x", label="X", kind="text")
        with pytest.raises(Exception):
            f.key = "y"  # type: ignore[misc]

    def test_equality(self):
        a = ConfigField(key="x", label="X", kind="text")
        b = ConfigField(key="x", label="X", kind="text")
        assert a == b


# ---------------------------------------------------------------------------
# ConnectorSpec
# ---------------------------------------------------------------------------


class TestConnectorSpec:
    def test_oauth_pkce_construction(self):
        spec = _spec(
            id="google",
            type="oauth_pkce",
            oauth_provider_ref="google",
            default_scopes=["openid", "email"],
            available_scopes=["openid", "email", "profile"],
        )
        assert spec.id == "google"
        assert spec.type == "oauth_pkce"
        assert spec.default_scopes == ("openid", "email")
        assert spec.available_scopes == ("openid", "email", "profile")

    def test_mcp_server_construction(self):
        spec = _mcp_spec()
        assert spec.type == "mcp_server"
        assert spec.mcp_command == "npx"
        assert spec.mcp_args == ("-y", "test-mcp")
        assert spec.mcp_env_keys == ("TEST_TOKEN",)

    def test_sequences_normalised_to_tuple(self):
        spec = ConnectorSpec(
            id="x",
            display_name="X",
            icon="",
            category="c",
            tier=1,
            type="oauth_pkce",
            description="d",
            default_scopes=["a", "b"],
            available_scopes=["a", "b", "c"],
        )
        assert isinstance(spec.default_scopes, tuple)
        assert isinstance(spec.available_scopes, tuple)
        assert isinstance(spec.config_schema, tuple)
        assert isinstance(spec.mcp_args, tuple)

    def test_config_schema_stores_fields(self):
        fields = (
            ConfigField(key="client_id", label="Client ID", kind="text"),
            ConfigField(
                key="client_secret", label="Secret", kind="secret", secret=True
            ),
        )
        spec = _spec(config_schema=fields)
        assert len(spec.config_schema) == 2
        assert spec.config_schema[0].key == "client_id"

    def test_empty_id_raises(self):
        with pytest.raises(ValueError, match="id must not be empty"):
            _spec(id="")

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="type"):
            _spec(type="api_token")  # type: ignore[arg-type]

    def test_negative_tier_raises(self):
        with pytest.raises(ValueError, match="tier"):
            _spec(tier=-1)

    def test_frozen(self):
        spec = _spec()
        with pytest.raises(Exception):
            spec.id = "other"  # type: ignore[misc]

    def test_equality(self):
        a = _spec(id="google")
        b = _spec(id="google")
        assert a == b

    def test_hashable(self):
        a = _spec(id="google")
        b = _spec(id="github")
        assert len({a, b}) == 2


# ---------------------------------------------------------------------------
# ConnectorRegistry
# ---------------------------------------------------------------------------


class TestConnectorRegistry:
    def setup_method(self):
        self.reg = ConnectorRegistry()

    def test_register_and_get(self):
        spec = _spec(id="google")
        self.reg.register(spec)
        assert self.reg.get("google") is spec

    def test_get_unknown_raises_keyerror(self):
        with pytest.raises(KeyError, match="google"):
            self.reg.get("google")

    def test_duplicate_id_raises_valueerror(self):
        self.reg.register(_spec(id="google"))
        with pytest.raises(ValueError, match="Duplicate connector id"):
            self.reg.register(_spec(id="google"))

    def test_all_returns_sorted_by_tier_then_id(self):
        self.reg.register(_spec(id="zzz", tier=5))
        self.reg.register(_spec(id="aaa", tier=10))
        self.reg.register(_spec(id="mmm", tier=5))
        ids = [s.id for s in self.reg.all()]
        assert ids == ["mmm", "zzz", "aaa"]

    def test_contains(self):
        self.reg.register(_spec(id="google"))
        assert "google" in self.reg
        assert "github" not in self.reg

    def test_len(self):
        assert len(self.reg) == 0
        self.reg.register(_spec(id="a"))
        self.reg.register(_spec(id="b"))
        assert len(self.reg) == 2

    def test_iter(self):
        self.reg.register(_spec(id="a", tier=1))
        self.reg.register(_spec(id="b", tier=2))
        ids = [s.id for s in self.reg]
        assert ids == ["a", "b"]

    def test_freeze_blocks_registration(self):
        self.reg.freeze()
        with pytest.raises(RuntimeError, match="frozen"):
            self.reg.register(_spec(id="google"))

    def test_clear_resets_frozen_state(self):
        self.reg.register(_spec(id="google"))
        self.reg.freeze()
        self.reg.clear()
        assert len(self.reg) == 0
        # Should be able to register again after clear.
        self.reg.register(_spec(id="google"))

    def test_all_empty_registry(self):
        assert self.reg.all() == []

    def test_mixed_types_coexist(self):
        self.reg.register(_spec(id="google", type="oauth_pkce"))
        self.reg.register(_mcp_spec(id="github-mcp"))
        assert len(self.reg) == 2
        assert self.reg.get("google").type == "oauth_pkce"
        assert self.reg.get("github-mcp").type == "mcp_server"
