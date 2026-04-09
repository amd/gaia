# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for SecretsManager.

Tests the secure secrets handling system.
"""

import asyncio
import os
import pytest
import time

from gaia.config.secrets_manager import SecretsManager, SecretEntry, get_secrets_manager


class TestSecretsManagerInit:
    """Test SecretsManager initialization."""

    def test_init_default(self):
        """Test default initialization."""
        secrets = SecretsManager()
        assert secrets.enable_audit_log is True
        assert secrets.enable_encryption is False
        assert secrets.cache is not None

    def test_init_no_audit(self):
        """Test initialization without audit log."""
        secrets = SecretsManager(enable_audit_log=False)
        assert secrets.enable_audit_log is False

    def test_init_with_encryption(self):
        """Test initialization with encryption."""
        secrets = SecretsManager(enable_encryption=True)
        assert secrets.enable_encryption is True


class TestSecretsManagerRegister:
    """Test SecretsManager register method."""

    def test_register_basic(self):
        """Test basic registration."""
        secrets = SecretsManager()
        secrets.register("api_key", env_var="TEST_API_KEY")

        assert "api_key" in secrets._registered
        assert secrets._registered["api_key"]["env_var"] == "TEST_API_KEY"

    def test_register_required(self):
        """Test registering required secret."""
        os.environ["TEST_REQUIRED_KEY"] = "test_value"

        secrets = SecretsManager()
        secrets.register("required_key", env_var="TEST_REQUIRED_KEY", required=True)

        assert secrets._registered["required_key"]["required"] is True

    def test_register_required_missing(self):
        """Test registering required secret that's missing."""
        # Make sure env var doesn't exist
        os.environ.pop("MISSING_REQUIRED_KEY", None)

        secrets = SecretsManager()
        with pytest.raises(ValueError) as exc_info:
            secrets.register("missing", env_var="MISSING_REQUIRED_KEY", required=True)

        assert "not found" in str(exc_info.value).lower()

    def test_register_with_default(self):
        """Test registering secret with default."""
        os.environ.pop("OPTIONAL_KEY", None)

        secrets = SecretsManager()
        secrets.register("optional", env_var="OPTIONAL_KEY", default="default_value")

        value = secrets.get("optional")
        assert value == "default_value"

    def test_register_method_chaining(self):
        """Test method chaining."""
        secrets = SecretsManager()
        result = secrets.register("key1", "ENV_KEY1").register("key2", "ENV_KEY2")

        assert result is secrets
        assert len(secrets._registered) == 2


class TestSecretsManagerGet:
    """Test SecretsManager get method."""

    def test_get_existing(self):
        """Test getting existing secret."""
        os.environ["TEST_KEY"] = "test_value"

        secrets = SecretsManager()
        secrets.register("test_key", env_var="TEST_KEY")

        value = secrets.get("test_key")
        assert value == "test_value"

    def test_get_missing(self):
        """Test getting missing secret."""
        os.environ.pop("MISSING_KEY", None)

        secrets = SecretsManager()
        secrets.register("missing", env_var="MISSING_KEY")

        value = secrets.get("missing")
        assert value is None

    def test_get_unregistered(self):
        """Test getting unregistered secret."""
        secrets = SecretsManager()

        value = secrets.get("unregistered")
        assert value is None

    def test_get_cached(self):
        """Test getting cached secret."""
        os.environ["CACHED_KEY"] = "cached_value"

        secrets = SecretsManager()
        secrets.register("cached_key", env_var="CACHED_KEY")

        # First get - cache miss
        value1 = secrets.get("cached_key")
        assert value1 == "cached_value"

        # Second get - cache hit
        value2 = secrets.get("cached_key")
        assert value2 == "cached_value"

    def test_get_access_logged(self):
        """Test that access is logged."""
        os.environ["LOGGED_KEY"] = "logged_value"

        secrets = SecretsManager(enable_audit_log=True)
        secrets.register("logged_key", env_var="LOGGED_KEY")

        secrets.get("logged_key")

        log = secrets.get_access_log()
        assert len(log) > 0
        assert log[-1]["secret_name"] == "logged_key"

    @pytest.mark.asyncio
    async def test_get_async(self):
        """Test async get method."""
        os.environ["ASYNC_KEY"] = "async_value"

        secrets = SecretsManager()
        secrets.register("async_key", env_var="ASYNC_KEY")

        value = await secrets.get_async("async_key")
        assert value == "async_value"


class TestSecretsManagerGetAll:
    """Test SecretsManager get_all method."""

    def test_get_all_redacted(self):
        """Test getting all secrets redacted."""
        os.environ["SECRET1"] = "password123"
        os.environ["SECRET2"] = "mysecretkey"

        secrets = SecretsManager()
        secrets.register("secret1", env_var="SECRET1")
        secrets.register("secret2", env_var="SECRET2")

        all_secrets = secrets.get_all(redact=True)

        assert "secret1" in all_secrets
        assert "secret2" in all_secrets
        # Values should be redacted
        assert "***" in all_secrets["secret1"]
        assert "***" in all_secrets["secret2"]

    def test_get_all_unredacted(self):
        """Test getting all secrets unredacted."""
        os.environ["SECRET1"] = "password123"

        secrets = SecretsManager()
        secrets.register("secret1", env_var="SECRET1")

        all_secrets = secrets.get_all(redact=False)

        assert all_secrets["secret1"] == "password123"


class TestSecretsManagerRotate:
    """Test SecretsManager rotate method."""

    def test_rotate(self):
        """Test rotating secret."""
        os.environ["ROTATE_KEY"] = "original_value"

        secrets = SecretsManager()
        secrets.register("rotate_key", env_var="ROTATE_KEY")

        # Get original value
        value1 = secrets.get("rotate_key")
        assert value1 == "original_value"

        # Rotate
        secrets.rotate("rotate_key", "new_value")

        # Get new value (from cache)
        value2 = secrets.get("rotate_key")
        assert value2 == "new_value"

    def test_rotate_unregistered(self):
        """Test rotating unregistered secret."""
        secrets = SecretsManager()

        with pytest.raises(ValueError):
            secrets.rotate("unregistered", "new_value")


class TestSecretsManagerAccessLog:
    """Test SecretsManager access log."""

    def test_get_access_log(self):
        """Test getting access log."""
        os.environ["LOG_KEY"] = "log_value"

        secrets = SecretsManager(enable_audit_log=True)
        secrets.register("log_key", env_var="LOG_KEY")

        secrets.get("log_key")
        secrets.get("log_key")

        log = secrets.get_access_log()
        assert len(log) == 2

    def test_get_access_stats(self):
        """Test getting access stats."""
        os.environ["STATS_KEY"] = "stats_value"

        secrets = SecretsManager(enable_audit_log=True)
        secrets.register("stats_key", env_var="STATS_KEY")

        for _ in range(5):
            secrets.get("stats_key")

        stats = secrets.get_access_stats()

        assert stats["total_accesses"] == 5
        assert stats["unique_secrets"] == 1
        assert "avg_latency_ms" in stats

    def test_clear_access_log(self):
        """Test clearing access log."""
        secrets = SecretsManager(enable_audit_log=True)

        secrets._log_access("test", 1.0, "test")
        assert len(secrets._access_log) == 1

        secrets.clear_access_log()
        assert len(secrets._access_log) == 0

    def test_access_log_disabled(self):
        """Test with access log disabled."""
        secrets = SecretsManager(enable_audit_log=False)

        secrets._log_access("test", 1.0, "test")
        assert len(secrets._access_log) == 0


class TestSecretsManagerCache:
    """Test SecretsManager cache operations."""

    def test_clear_cache(self):
        """Test clearing cache."""
        os.environ["CACHE_KEY"] = "cache_value"

        secrets = SecretsManager()
        secrets.register("cache_key", env_var="CACHE_KEY")

        # First get - caches value
        secrets.get("cache_key")

        # Clear cache
        secrets.clear_cache()

        # Next get will fetch from env again

    def test_shutdown(self):
        """Test shutdown."""
        secrets = SecretsManager()
        secrets.register("test", "TEST_ENV", default="value")
        secrets._log_access("test", 1.0, "test")

        asyncio.run(secrets.shutdown())

        assert len(secrets._registered) == 0
        assert len(secrets._access_log) == 0


class TestSecretsManagerValidation:
    """Test SecretsManager validation methods."""

    def test_validate_all_valid(self):
        """Test validate_all with all secrets present."""
        os.environ["VALID_KEY1"] = "value1"
        os.environ["VALID_KEY2"] = "value2"

        secrets = SecretsManager()
        secrets.register("key1", env_var="VALID_KEY1", required=True)
        secrets.register("key2", env_var="VALID_KEY2", required=True)

        missing = secrets.validate_all()
        assert len(missing) == 0

    def test_validate_all_missing(self):
        """Test validate_all with missing secrets."""
        os.environ.pop("MISSING_KEY1", None)
        os.environ.pop("MISSING_KEY2", None)

        secrets = SecretsManager()
        secrets.register("key1", env_var="MISSING_KEY1", required=True)
        secrets.register("key2", env_var="MISSING_KEY2", required=True)

        missing = secrets.validate_all()
        assert len(missing) == 2

    def test_is_registered(self):
        """Test is_registered check."""
        secrets = SecretsManager()
        secrets.register("registered_key", env_var="REG_KEY")

        assert secrets.is_registered("registered_key") is True
        assert secrets.is_registered("unregistered_key") is False

    def test_get_registered_secrets(self):
        """Test getting registered secrets list."""
        secrets = SecretsManager()
        secrets.register("key1", "ENV_KEY1")
        secrets.register("key2", "ENV_KEY2")

        names = secrets.get_registered_secrets()
        assert "key1" in names
        assert "key2" in names


class TestSecretsManagerLatency:
    """Test SecretsManager latency targets."""

    @pytest.mark.asyncio
    async def test_cached_secret_under_10ms(self):
        """Cached secret retrieval should be under 10ms."""
        os.environ["TEST_SECRET_VALUE"] = "test123"

        secrets = SecretsManager()
        secrets.register("test_secret", env_var="TEST_SECRET_VALUE", default="test123")

        # First access (cache miss)
        _ = secrets.get("test_secret")

        # Second access (cache hit) - measure latency
        start = time.perf_counter()
        value = secrets.get("test_secret")
        latency_ms = (time.perf_counter() - start) * 1000

        assert latency_ms < 10, f"Secret retrieval {latency_ms}ms exceeds 10ms target"
        assert value == "test123"


class TestSecretsManagerGlobal:
    """Test global secrets manager functions."""

    def test_get_secrets_manager_singleton(self):
        """Test that get_secrets_manager returns singleton."""
        sm1 = get_secrets_manager()
        sm2 = get_secrets_manager()

        assert sm1 is sm2

    def test_register_secret_global(self):
        """Test register_secret global function."""
        os.environ["GLOBAL_KEY"] = "global_value"

        register_secret("global_key", "GLOBAL_KEY")

        sm = get_secrets_manager()
        assert sm.is_registered("global_key")

    def test_get_secret_global(self):
        """Test get_secret global function."""
        os.environ["GET_KEY"] = "get_value"

        sm = get_secrets_manager()
        sm.register("get_key", "GET_KEY")

        value = get_secret("get_key")
        assert value == "get_value"


class TestSecretsManagerStats:
    """Test SecretsManager stats."""

    def test_get_stats(self):
        """Test getting stats."""
        secrets = SecretsManager(enable_audit_log=True)
        secrets.register("test", "TEST_ENV", default="value")

        stats = secrets.get_stats()

        assert stats["registered_count"] == 1
        assert stats["audit_enabled"] is True
        assert stats["cache_enabled"] is True
