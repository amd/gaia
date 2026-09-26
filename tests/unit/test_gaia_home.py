# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GAIA_HOME must relocate every listed write site, not only config.json."""

from pathlib import Path


def test_gaia_home_prefers_gaia_home_over_config_dir(tmp_path, monkeypatch):
    from gaia.config import gaia_home

    relocated = tmp_path / "relocated"
    other = tmp_path / "config-dir"
    monkeypatch.setenv("GAIA_HOME", str(relocated))
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(other))
    assert gaia_home() == relocated


def test_gaia_home_falls_back_to_config_dir(tmp_path, monkeypatch):
    from gaia.config import gaia_home

    other = tmp_path / "config-dir"
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(other))
    assert gaia_home() == other


def test_gaia_home_defaults_to_dot_gaia(tmp_path, monkeypatch):
    from gaia.config import gaia_home

    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert gaia_home() == tmp_path / ".gaia"


def test_listed_write_sites_stay_out_of_real_home(tmp_path, monkeypatch):
    """HOME/.gaia must stay empty when GAIA_HOME points somewhere else."""
    fake_home = tmp_path / "home"
    relocated = tmp_path / "gaia-home"
    fake_home.mkdir()
    relocated.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("GAIA_HOME", str(relocated))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    import pytest

    from gaia.filesystem.index import FileSystemIndexService
    from gaia.mcp.context7_cache import Context7Cache, Context7RateLimiter
    from gaia.scratchpad.service import ScratchpadService
    from gaia.security import PathValidator
    from gaia.ui.database import ChatDatabase, default_db_path

    pytest.importorskip("gaia_agent_chat")
    from gaia_agent_chat.agent import ChatAgentConfig

    validator = PathValidator(allowed_paths=[])
    cache = Context7Cache()
    limiter = Context7RateLimiter()
    index = FileSystemIndexService()
    scratchpad = ScratchpadService()
    db = ChatDatabase()
    try:
        config = ChatAgentConfig()
        assert validator.cache_dir == relocated / "cache"
        assert cache.cache_dir == relocated / "cache" / "context7"
        assert limiter.state_file.parent == relocated / "cache" / "context7"
        assert Path(db._db_path) == default_db_path()
        assert default_db_path() == relocated / "chat" / "gaia_chat.db"
        assert Path(config.filesystem_index_path) == relocated / "file_index.db"
        assert Path(config.scratchpad_db_path) == relocated / "scratchpad.db"
        assert (relocated / "file_index.db").exists()
        assert (relocated / "scratchpad.db").exists()
        assert not (fake_home / ".gaia").exists()
    finally:
        db.close()
