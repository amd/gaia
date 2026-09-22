# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The file-access boundary is the *file*, and it holds on reads too.

Three separate holes, one chain: a session's allowlist silently widened from an
attached document to the directory it sat in (``$HOME`` included); reads were
gated on the allowlist alone, so anything in scope could be read back including
private keys; and the write guard covered ``~/.ssh`` and ``LaunchAgents`` but not
``~/.bashrc``, autostart entries or git hooks — the shortest path there is from a
prompt injection to code that runs on the next login.
"""

from pathlib import Path

import pytest

from gaia.security import PathValidator


@pytest.fixture
def validator(tmp_path):
    return PathValidator(allowed_paths=[str(tmp_path)])


# ── Scope: grant the file, not its parent ─────────────────────────────────


class TestSessionScopeIsTheFile:
    def test_granting_a_file_does_not_grant_its_siblings(self, tmp_path):
        attached = tmp_path / "report.pdf"
        attached.write_text("report", encoding="utf-8")
        sibling = tmp_path / "tax-return.pdf"
        sibling.write_text("private", encoding="utf-8")

        scoped = PathValidator(allowed_paths=[str(attached)])

        assert scoped.is_path_allowed(str(attached), prompt_user=False)
        assert not scoped.is_path_allowed(str(sibling), prompt_user=False)

    def test_an_empty_allowlist_denies_rather_than_widening_to_cwd(self, tmp_path):
        """`[]` is a scope of nothing; only `None` means "no scope supplied"."""
        scoped = PathValidator(allowed_paths=[])

        assert scoped.allowed_paths == set()
        assert not scoped.is_path_allowed(str(tmp_path / "x.txt"), prompt_user=False)

    def test_no_allowlist_still_defaults_to_cwd(self):
        assert PathValidator().allowed_paths == {Path.cwd().resolve()}


# ── I13: a host-supplied scope is not the machine-global grant list ───────


class TestHostSuppliedScopeStandsAlone:
    def test_persisted_grants_are_not_unioned_into_a_host_scope(
        self, tmp_path, monkeypatch
    ):
        """The CLI's "[a]lways" must not become standing access for the UI."""
        fake_home = tmp_path / "home"
        (fake_home / ".gaia" / "cache").mkdir(parents=True)
        elsewhere = tmp_path / "someone-elses-project"
        elsewhere.mkdir()
        (fake_home / ".gaia" / "cache" / "allowed_paths.json").write_text(
            '{"paths": ["%s"]}' % elsewhere.as_posix(), encoding="utf-8"
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        session = PathValidator(allowed_paths=[str(tmp_path / "doc.pdf")])
        cli = PathValidator()

        assert not session.is_path_allowed(str(elsewhere), prompt_user=False)
        assert cli.is_path_allowed(str(elsewhere), prompt_user=False)

    def test_a_host_scope_never_writes_to_the_machine_global_file(
        self, tmp_path, monkeypatch
    ):
        fake_home = tmp_path / "home"
        (fake_home / ".gaia" / "cache").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        session = PathValidator(allowed_paths=[str(tmp_path)])
        session._save_persisted_path(tmp_path / "sneaky")

        assert not (fake_home / ".gaia" / "cache" / "allowed_paths.json").exists()


# ── Reads: in-scope is not the same as safe-to-read ──────────────────────


class TestSensitiveReadsAreRefused:
    @pytest.mark.parametrize(
        "name", [".env", "id_rsa", "credentials.json", ".netrc", "server.pem", "a.key"]
    )
    def test_a_secret_inside_the_allowlist_is_still_refused(
        self, validator, tmp_path, name
    ):
        secret = tmp_path / name
        secret.write_text("PRIVATE KEY", encoding="utf-8")

        allowed, reason = validator.validate_read(str(secret), prompt_user=False)

        assert not allowed
        assert "Read blocked" in reason

    def test_an_ordinary_document_still_reads(self, validator, tmp_path):
        doc = tmp_path / "notes.md"
        doc.write_text("hello", encoding="utf-8")

        assert validator.validate_read(str(doc), prompt_user=False) == (True, "")

    def test_out_of_scope_reads_say_what_to_do(self, validator, tmp_path):
        allowed, reason = validator.validate_read("/somewhere/else.txt", False)

        assert not allowed
        assert "allowed_paths" in reason

    def test_a_credential_directory_is_refused_whatever_the_file_is_called(
        self, tmp_path, monkeypatch
    ):
        fake_home = tmp_path / "home"
        ssh = fake_home / ".ssh"
        ssh.mkdir(parents=True)
        (ssh / "config").write_text("Host prod", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        import gaia.security as security

        monkeypatch.setattr(
            security, "SECRET_DIRECTORIES", security._secret_directories()
        )
        scoped = PathValidator(allowed_paths=[str(fake_home)])

        blocked, reason = scoped.is_read_blocked(str(ssh / "config"))

        assert blocked
        assert ".ssh" in reason


# ── The report's probe, driven through the tools a model actually calls ──


class TestReadToolsRefuseThePrivateKey:
    """``read_file("~/.ssh/id_rsa")`` returned the key. Both mixins define a
    ``read_file``; the flagship registers the ``file_search`` one, so a fix that
    only covered ``FileIOToolsMixin`` would have left the live path open."""

    @pytest.fixture
    def sandbox(self, tmp_path, monkeypatch):
        import gaia.security as security

        fake_home = tmp_path / "home"
        ssh = fake_home / ".ssh"
        ssh.mkdir(parents=True)
        key = ssh / "id_rsa"
        key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----", encoding="utf-8")
        doc = fake_home / "notes.md"
        doc.write_text("attached notes", encoding="utf-8")

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.setattr(security, "_is_interactive", lambda: False)
        monkeypatch.setattr(
            security, "SECRET_DIRECTORIES", security._secret_directories()
        )
        # A scope deliberately wider than the fix's own advice, so the denylist
        # is what refuses the key rather than the allowlist doing it by accident.
        return PathValidator(allowed_paths=[str(fake_home)]), key, doc

    def _read_file_tool(self, mixin_cls, register, validator):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        instance = type("_Stub", (mixin_cls,), {})()
        instance.path_validator = validator
        instance._path_validator = validator
        getattr(instance, register)()
        return _TOOL_REGISTRY["read_file"]["function"]

    def test_file_search_read_file_refuses_the_key(self, sandbox):
        from gaia.agents.tools.file_tools import FileSearchToolsMixin

        validator, key, _ = sandbox
        read_file = self._read_file_tool(
            FileSearchToolsMixin, "register_file_search_tools", validator
        )

        result = read_file(str(key))

        assert result["status"] == "error"
        assert "PRIVATE KEY" not in str(result)

    def test_file_io_read_file_refuses_the_key(self, sandbox):
        from gaia.agents.tools.file_io_tools import FileIOToolsMixin

        validator, key, _ = sandbox
        read_file = self._read_file_tool(
            FileIOToolsMixin, "register_file_io_tools", validator
        )

        result = read_file(str(key))

        assert result["status"] == "error"
        assert "PRIVATE KEY" not in str(result)

    def test_an_attached_document_is_still_readable(self, sandbox):
        """The positive control — the fix must not break the actual use case."""
        from gaia.agents.tools.file_io_tools import FileIOToolsMixin

        validator, _, doc = sandbox
        read_file = self._read_file_tool(
            FileIOToolsMixin, "register_file_io_tools", validator
        )

        result = read_file(str(doc))

        assert result["status"] == "success"
        assert "attached notes" in result["content"]

    def test_filesystem_read_file_refuses_the_key(self, sandbox):
        from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin

        validator, key, _ = sandbox
        read_file = self._read_file_tool(
            FileSystemToolsMixin, "register_filesystem_tools", validator
        )

        result = read_file(str(key))

        assert "Access denied" in str(result)
        assert "PRIVATE KEY" not in str(result)

    def test_filesystem_content_search_refuses_credentials(self, validator, tmp_path):
        from gaia.agents.base.tools import _TOOL_REGISTRY
        from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin

        (tmp_path / "credentials.json").write_text('{"secret": "hidden-marker"}')
        (tmp_path / "notes.txt").write_text("visible-marker")
        self._read_file_tool(
            FileSystemToolsMixin, "register_filesystem_tools", validator
        )
        find_files = _TOOL_REGISTRY["find_files"]["function"]

        result = find_files(query="marker", search_type="content", scope=str(tmp_path))

        assert "visible-marker" in result
        assert "hidden-marker" not in result
        assert "credentials.json" not in result


# ── Writes: files that execute on their own ──────────────────────────────


class TestStartupExecutionWritesAreBlocked:
    @pytest.mark.parametrize(
        "relative",
        [
            ".bashrc",
            ".zshrc",
            ".profile",
            ".bash_profile",
            ".gitconfig",
            "config.fish",
            ".config/autostart/evil.desktop",
            ".config/systemd/user/evil.service",
            "project/.git/hooks/pre-commit",
            "project/.git/config",
            "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
            "Library/LaunchAgents/evil.plist",
        ],
    )
    def test_write_is_blocked(self, tmp_path, relative):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        validator = PathValidator(allowed_paths=[str(tmp_path)])

        blocked, reason = validator.is_write_blocked(str(target))

        assert blocked, f"{relative} should be write-blocked"
        assert "run" in reason or "execut" in reason

    @pytest.mark.parametrize(
        "relative",
        [
            "notes.md",
            "report.pdf",
            "src/startup/main.py",
            "src/profile.py",
            "docs/gitconfig.md",
        ],
    )
    def test_ordinary_files_stay_writable(self, tmp_path, relative):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        validator = PathValidator(allowed_paths=[str(tmp_path)])

        blocked, reason = validator.is_write_blocked(str(target))

        assert not blocked, f"{relative} should stay writable: {reason}"

    def test_the_injection_chain_is_closed_end_to_end(self, tmp_path, monkeypatch):
        """`write_file("~/.bashrc", "curl … | sh")` from an attached document."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        attached = fake_home / "notes.txt"
        attached.write_text("notes", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        from gaia.ui._chat_helpers import _compute_allowed_paths

        scope = _compute_allowed_paths([str(attached)])
        validator = PathValidator(allowed_paths=scope)

        allowed, _ = validator.validate_write(
            str(fake_home / ".bashrc"), prompt_user=False
        )

        assert not allowed
