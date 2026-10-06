# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Protected folders refuse writes as well as reads, and approval never exempts."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import gaia.security as security
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_edit import file_read_record
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.security import MAX_WRITE_SIZE_BYTES, PathValidator

CREDENTIAL_DIRS = [
    ".aws",
    ".kube",
    ".docker",
    ".azure",
    ".config/gcloud",
    "AppData/Roaming/gcloud",
    ".ssh",
    ".gnupg",
]

MIXINS = [
    (FileSearchToolsMixin, "register_file_search_tools"),
    (FileIOToolsMixin, "register_file_io_tools"),
]


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for rel in CREDENTIAL_DIRS:
        folder = home / rel
        folder.mkdir(parents=True)
        (folder / "config").write_text("original", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(security, "SECRET_DIRECTORIES", security._secret_directories())
    monkeypatch.setattr(
        security, "_system_temp_roots", lambda: {str(tmp_path / "systemp")}
    )
    return home


def _tools(mixin_cls, register, validator):
    host = type("_Host", (mixin_cls,), {})()
    host.path_validator = validator
    host._path_validator = validator
    getattr(host, register)()
    return (
        host,
        _TOOL_REGISTRY["write_file"]["function"],
        _TOOL_REGISTRY["edit_file"]["function"],
    )


class TestCredentialFoldersRefuseWrites:
    @pytest.mark.parametrize("rel", CREDENTIAL_DIRS)
    def test_is_write_blocked(self, fake_home, rel):
        validator = PathValidator(allowed_paths=[str(fake_home)])

        blocked, reason = validator.is_write_blocked(str(fake_home / rel / "config"))

        assert blocked
        assert "Write blocked" in reason

    @pytest.mark.parametrize("mixin_cls, register", MIXINS)
    @pytest.mark.parametrize("rel", CREDENTIAL_DIRS)
    def test_write_and_edit_tools_refuse(self, fake_home, rel, mixin_cls, register):
        # A scope wider than any host grants, so the denylist does the refusing.
        validator = PathValidator(allowed_paths=[str(fake_home)])
        host, write_file, edit_file = _tools(mixin_cls, register, validator)
        target = fake_home / rel / "config"
        file_read_record(host).note(target)

        with patch.object(security, "_is_interactive", return_value=False):
            written = write_file(str(target), "planted")
            edited = edit_file(str(target), "original", "planted")

        assert written["status"] == "error"
        assert edited["status"] == "error"
        assert target.read_text(encoding="utf-8") == "original"

    def test_ordinary_home_file_stays_writable(self, fake_home):
        validator = PathValidator(allowed_paths=[str(fake_home)])

        assert validator.is_write_blocked(str(fake_home / "notes.md")) == (False, "")


class TestApprovalIsNotAnExemption:
    @pytest.fixture
    def project(self, fake_home, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        return PathValidator(allowed_paths=[str(project)]), outside

    def _approve_everything(self, validator):
        def approve(path):
            validator.allowed_paths.add(path)
            return True

        return patch.object(validator, "_prompt_user_for_access", approve)

    def test_approved_edit_to_blocklisted_file_is_refused(self, project):
        validator, outside = project
        target = outside / ".env"
        target.write_text("KEY=original", encoding="utf-8")
        host, _, edit_file = _tools(
            FileSearchToolsMixin, "register_file_search_tools", validator
        )
        file_read_record(host).note(target)

        with self._approve_everything(validator):
            result = edit_file(str(target), "original", "planted")

        assert result["status"] == "error"
        assert "Write blocked" in result["error"]
        assert target.read_text(encoding="utf-8") == "KEY=original"

    def test_approved_edit_still_enforces_size_limit(self, project):
        validator, outside = project
        target = outside / "notes.md"
        target.write_text("original", encoding="utf-8")
        host, _, edit_file = _tools(
            FileSearchToolsMixin, "register_file_search_tools", validator
        )
        file_read_record(host).note(target)

        with (
            patch.object(security, "_is_interactive", return_value=True),
            patch("builtins.input", return_value="y"),
        ):
            result = edit_file(
                str(target), "original", "x" * (MAX_WRITE_SIZE_BYTES + 1)
            )

        assert result["status"] == "error"
        assert "exceeds" in result["error"]
        assert target.read_text(encoding="utf-8") == "original"

    def test_approved_out_of_folder_edit_still_works(self, project):
        validator, outside = project
        target = outside / "notes.md"
        target.write_text("hello world", encoding="utf-8")
        host, _, edit_file = _tools(
            FileSearchToolsMixin, "register_file_search_tools", validator
        )
        file_read_record(host).note(target)

        with (
            patch.object(security, "_is_interactive", return_value=True),
            patch("builtins.input", return_value="y"),
        ):
            result = edit_file(str(target), "world", "there")

        assert result["status"] == "success", result
        assert target.read_text(encoding="utf-8") == "hello there"


class TestTerminalPromptRefusesNeverGrantablePaths:
    @pytest.mark.parametrize(
        "rel", [".aws/config", ".kube/config", ".ssh/id_rsa", "outside/.env"]
    )
    def test_no_prompt_is_offered(self, fake_home, tmp_path, rel):
        target = fake_home / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        validator = PathValidator(allowed_paths=[str(tmp_path / "project")])

        with (
            patch.object(security, "_is_interactive", return_value=True),
            patch("builtins.input", side_effect=AssertionError("offered approval")),
        ):
            assert validator.is_path_allowed(str(target)) is False

    def test_ordinary_outside_path_is_still_offered(self, fake_home, tmp_path):
        target = tmp_path / "outside" / "notes.md"
        validator = PathValidator(allowed_paths=[str(tmp_path / "project")])

        with (
            patch.object(security, "_is_interactive", return_value=True),
            patch("builtins.input", return_value="y") as asked,
        ):
            assert validator.is_path_allowed(str(target)) is True
        asked.assert_called_once()

    def test_saved_grant_to_a_credential_folder_is_ignored(
        self, fake_home, monkeypatch
    ):
        state = fake_home / ".gaia"
        (state / "cache").mkdir(parents=True)
        (state / "cache" / "allowed_paths.json").write_text(
            json.dumps({"paths": [str(fake_home / ".aws")]}), encoding="utf-8"
        )
        monkeypatch.setenv("GAIA_HOME", str(state))
        monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)

        validator = PathValidator()

        assert (fake_home / ".aws").resolve() not in validator.allowed_paths
