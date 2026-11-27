# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The session guard on the real ``~/.gaia`` fails a run that touched it (#8790)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit import real_home_guard as real_home_guard_module
from tests.unit.real_home_guard import (
    ALLOW_ENV,
    ENFORCE_ENV,
    FAIL,
    SKIP,
    WARN,
    account_home,
    changes,
    guard_mode,
    is_within,
    list_home,
    settle,
)

_GUARD_DIR = Path(__file__).resolve().parent


def _seed(home: Path) -> Path:
    gaia = home / ".gaia"
    (gaia / "chat").mkdir(parents=True)
    (gaia / "config.json").write_text("{}", encoding="utf-8")
    (gaia / "chat" / "history.db").write_bytes(b"rows")
    return gaia


def test_in_ci_a_session_that_writes_into_the_real_home_fails_and_names_the_paths(
    tmp_path,
):
    """One pytest run under a stand-in HOME: a child's write and an edit in place."""
    home = tmp_path / "home"
    _seed(home)
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "conftest.py").write_text(
        textwrap.dedent(f"""
            import sys

            sys.path.insert(0, {str(_GUARD_DIR)!r})
            import real_home_guard
            from real_home_guard import _guard_real_gaia_home_session  # noqa: F401

            # The stand-in HOME plays the real home, though it sits in tmp.
            real_home_guard.protected_gaia_home = real_home_guard.real_gaia_home
            """),
        encoding="utf-8",
    )
    (suite / "test_leaks.py").write_text(
        textwrap.dedent("""
            import subprocess
            import sys
            from pathlib import Path

            GAIA = Path.home() / ".gaia"


            def test_a_child_process_writes_a_log():
                code = "import sys; open(sys.argv[1], 'w').write('x')"
                subprocess.run(
                    [sys.executable, "-c", code, str(GAIA / "gaia.log")], check=True
                )


            def test_a_database_grows_in_place():
                with open(GAIA / "chat" / "history.db", "ab") as handle:
                    handle.write(b"more rows")
            """),
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CI": "true"}
    env.pop("RUNNER_ENVIRONMENT", None)
    env.pop(ALLOW_ENV, None)
    env.pop(ENFORCE_ENV, None)
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "."]

    run = subprocess.run(
        command, cwd=suite, env=env, capture_output=True, text=True, check=False
    )

    report = run.stdout + run.stderr
    assert run.returncode == 1, report
    assert "2 passed, 1 error" in report
    assert "created gaia.log" in report
    assert f"changed {os.path.join('chat', 'history.db')}" in report
    assert "config.json" not in report
    assert ALLOW_ENV in report


@pytest.mark.parametrize(
    "environ, mode",
    [
        ({}, WARN),
        ({"CI": ""}, WARN),
        ({"CI": "false"}, WARN),
        ({"CI": "true"}, FAIL),
        ({"CI": "1"}, FAIL),
        ({"CI": "true", "RUNNER_ENVIRONMENT": "github-hosted"}, FAIL),
        ({"CI": "true", "RUNNER_ENVIRONMENT": "self-hosted"}, WARN),
        ({ENFORCE_ENV: "1", "RUNNER_ENVIRONMENT": "self-hosted"}, FAIL),
        ({ENFORCE_ENV: "1"}, FAIL),
        ({ALLOW_ENV: "1"}, SKIP),
        ({ALLOW_ENV: "1", "CI": "true", ENFORCE_ENV: "1"}, SKIP),
    ],
)
def test_the_environment_picks_fail_warn_or_skip(environ, mode):
    assert guard_mode(environ) == mode


def test_on_a_developer_machine_a_changed_home_is_one_warning(tmp_path):
    with pytest.warns(pytest.PytestWarning) as caught:
        settle(["created gaia.log"], tmp_path / ".gaia", WARN)

    (warning,) = caught
    text = str(warning.message)
    assert "created gaia.log" in text
    assert ALLOW_ENV in text
    assert ENFORCE_ENV in text


def test_enforced_a_changed_home_fails_with_the_same_paths(tmp_path):
    with pytest.raises(pytest.fail.Exception, match="created gaia.log"):
        settle(["created gaia.log"], tmp_path / ".gaia", FAIL)


@pytest.mark.parametrize("found, mode", [(["created gaia.log"], SKIP), ([], FAIL)])
def test_allowed_writes_and_an_untouched_home_say_nothing(
    tmp_path, recwarn, found, mode
):
    settle(found, tmp_path / ".gaia", mode)

    assert not recwarn.list


def test_a_home_that_is_left_alone_reports_nothing(tmp_path):
    gaia = _seed(tmp_path)
    before = list_home(gaia)

    assert changes(before, list_home(gaia)) == []
    assert set(before.entries) == {
        "",
        "chat",
        "config.json",
        os.path.join("chat", "history.db"),
    }


def test_created_changed_and_removed_paths_are_each_named(tmp_path):
    gaia = _seed(tmp_path)
    before = list_home(gaia)

    (gaia / "config.json").write_text('{"default_model": "x"}', encoding="utf-8")
    (gaia / "chat" / "history.db").unlink()
    (gaia / "logs").mkdir()

    found = changes(before, list_home(gaia))
    assert "changed config.json" in found
    assert f"removed {os.path.join('chat', 'history.db')}" in found
    assert "created logs" in found


def test_a_missing_home_is_not_created_and_its_creation_is_reported(tmp_path):
    gaia = tmp_path / ".gaia"
    before = list_home(gaia)

    assert before.entries is None
    assert not gaia.exists()
    assert changes(before, list_home(gaia)) == []
    gaia.mkdir()
    assert changes(before, list_home(gaia)) == ["created ."]


def test_a_listing_cut_short_still_reports_a_changed_file(tmp_path):
    gaia = _seed(tmp_path)
    for index in range(20):
        (gaia / f"extra-{index:02}.txt").write_text("x", encoding="utf-8")
    before = list_home(gaia, max_entries=5)
    assert not before.complete
    assert len(before.entries) == 5

    # Sorts first, so it pushes another entry out of the cut listing.
    (gaia / "a-new-file.txt").write_text("x", encoding="utf-8")
    (gaia / "config.json").write_text('{"changed": true}', encoding="utf-8")

    found = changes(before, list_home(gaia, max_entries=5))
    assert "changed config.json" in found
    assert not [line for line in found if line.startswith(("created", "removed"))]


def test_on_windows_a_missing_registry_value_does_not_trust_the_overridden_home(
    monkeypatch, tmp_path
):
    """#9021: on Windows, if the registry cannot be read, the (possibly
    tampered) USERPROFILE is not trusted and no account home is returned."""
    fake = types.ModuleType("winreg")
    fake.HKEY_CURRENT_USER = 0x80000001
    fake.OpenKey = lambda *a, **k: (_ for _ in ()).throw(OSError("no"))
    fake.QueryValueEx = lambda *a, **k: (_ for _ in ()).throw(OSError("no"))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(
        real_home_guard_module,
        "os",
        SimpleNamespace(name="nt", environ=os.environ, getuid=lambda: 0),
    )
    try:
        import pwd

        monkeypatch.setattr(
            pwd, "getpwuid", lambda *a, **k: (_ for _ in ()).throw(OSError("no"))
        )
    except ImportError:
        pass
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert real_home_guard_module.account_home() is None


def test_is_within_still_catches_a_protected_path_when_realpath_is_broken(monkeypatch):
    """#9021: a broken realpath must not silently allow a write into the
    protected home; the normalised path is still checked."""

    def _broken(_path, *_args, **_kwargs):
        raise OSError("broken")

    monkeypatch.setattr(os.path, "realpath", _broken)
    home = account_home()
    assert home is not None
    assert is_within(home / ".gaia" / "cache" / "x", home / ".gaia") is True
    assert is_within(Path(tempfile.gettempdir()) / "scratch", home / ".gaia") is False


def test_is_within_refuses_a_symlink_alias_when_realpath_is_broken(
    monkeypatch, tmp_path
):
    """#9021: when realpath cannot be resolved, a symlink that aliases the
    protected home must not slip through the lexical fallback."""
    home = account_home()
    assert home is not None
    protected = home / ".gaia"
    link = tmp_path / "alias"
    try:
        link.symlink_to(protected, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this filesystem")

    def _broken(_path, *_args, **_kwargs):
        raise OSError("broken")

    monkeypatch.setattr(os.path, "realpath", _broken)
    assert is_within(link / "cache" / "x", protected) is True
    # A plain (non-symlink) path under the temp folder is still allowed.
    assert is_within(tmp_path / "scratch", protected) is False


def test_is_within_refuses_a_relative_path_inside_the_protected_home(monkeypatch):
    """#9021: a relative target resolved against a working directory inside the
    protected home must still be caught when realpath() fails."""
    home = account_home()
    assert home is not None
    protected = home / ".gaia"
    if not protected.is_dir():
        pytest.skip("the real GAIA home is not present on this machine")
    monkeypatch.chdir(protected)

    def _broken(_path, *_args, **_kwargs):
        raise OSError("broken")

    monkeypatch.setattr(os.path, "realpath", _broken)
    assert is_within("cache/x", protected) is True


def test_is_within_refuses_when_the_root_is_a_symlink_alias(monkeypatch, tmp_path):
    """#9021: when realpath() fails, a root that is a symlink alias to the
    protected home must not slip through the lexical fallback."""
    home = account_home()
    assert home is not None
    protected = home / ".gaia"
    if not protected.is_dir():
        pytest.skip("the real GAIA home is not present on this machine")
    alias = tmp_path / "gaia-alias"
    try:
        alias.symlink_to(protected, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this filesystem")

    def _broken(_path, *_args, **_kwargs):
        raise OSError("broken")

    monkeypatch.setattr(os.path, "realpath", _broken)
    # A path under the real root, compared against the alias root.
    assert is_within(protected / "cache" / "x", alias) is True


def test_is_within_refuses_when_an_ancestor_cannot_be_statted(monkeypatch, tmp_path):
    """#9021: an ancestor that cannot be stat()ed is not treated as having no
    symlink; the guard refuses rather than allowing the write."""
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("permission checks are bypassed when running as root")

    parent = tmp_path / "locked"
    parent.mkdir()
    child = parent / "child" / "x"
    parent.chmod(0)
    try:

        def _broken(_path, *_args, **_kwargs):
            raise OSError("broken")

        monkeypatch.setattr(os.path, "realpath", _broken)
        home = account_home()
        assert home is not None
        protected = home / ".gaia"
        assert is_within(child, protected) is True
    finally:
        parent.chmod(0o755)


def test_a_missing_account_home_fails_closed(monkeypatch):
    """#9021: when the real account home cannot be identified on a temporary
    run, the guard refuses rather than allowing the write."""
    import tests.unit.conftest as unit_conftest

    monkeypatch.setattr(unit_conftest, "_RUN_HOME_IS_TEMPORARY", True)
    monkeypatch.setattr(unit_conftest, "_REAL_GAIA_HOME", None)
    # The guard's teardown skips its exists() check only when this flag says so.
    monkeypatch.setattr(unit_conftest, "_real_gaia_home_existed", [True])
    assert unit_conftest._in_real_gaia_home(Path("/tmp/anything")) is True


def test_a_bytes_path_under_a_temporary_home_is_checked_not_raised(monkeypatch):
    """A bytes path from an audited write must be judged, not crash the hook."""
    import tests.unit.conftest as unit_conftest

    protected = Path(tempfile.gettempdir()).parent / "account" / ".gaia"
    monkeypatch.setattr(unit_conftest, "_RUN_HOME_IS_TEMPORARY", True)
    monkeypatch.setattr(unit_conftest, "_REAL_GAIA_HOME", protected)
    inside = os.fsencode(str(protected / "cache" / "x"))
    outside = os.fsencode(str(Path(tempfile.gettempdir()) / "scratch"))
    assert unit_conftest._in_real_gaia_home(inside) is True
    assert unit_conftest._in_real_gaia_home(outside) is False


@pytest.mark.skipif(os.name != "nt", reason="reads the real Windows registry")
def test_the_real_windows_registry_yields_the_account_home():
    """Unfaked: the registry call must match winreg's real signature."""
    from tests.unit.real_home_guard import _windows_account_home

    home = _windows_account_home()
    assert home is not None
    assert not real_home_guard_module.is_temporary_home(home)


class _FakeWinregKey:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def QueryValueEx(self, name):
        if name in self.values:
            return self.values[name], 1
        raise KeyError(name)


def _fake_winreg_module(profile_image_path) -> types.ModuleType:
    module = types.ModuleType("winreg")
    module.HKEY_CURRENT_USER = 0x80000001
    module.OpenKey = lambda root, subkey: _FakeWinregKey(
        {"USERPROFILE": str(profile_image_path)}
    )
    # Same arity as the real winreg.QueryValueEx(key, name).
    module.QueryValueEx = lambda key, name: key.QueryValueEx(name)
    return module


def test_the_windows_account_home_is_the_registry_not_the_overridden_home(
    monkeypatch, tmp_path
):
    """#9021: on Windows the account home comes from the registry's Volatile Environment,
    not from a ``USERPROFILE`` the test run overwrote with a temporary home."""
    fake_winreg = _fake_winreg_module(tmp_path / "the-account")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(
        real_home_guard_module,
        "os",
        SimpleNamespace(name="nt", environ=os.environ, getuid=lambda: 0),
    )
    try:
        import pwd

        monkeypatch.setattr(
            pwd,
            "getpwuid",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no")),
        )
    except ImportError:
        pass

    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    assert real_home_guard_module.account_home() == tmp_path / "the-account"


def test_a_few_thousand_files_are_listed_whole(tmp_path):
    gaia = tmp_path / ".gaia"
    for folder in range(30):
        (gaia / f"d{folder}").mkdir(parents=True)
        for index in range(100):
            (gaia / f"d{folder}" / f"f{index}").write_bytes(b"")

    listing = list_home(gaia)

    assert listing.complete
    assert len(listing.entries) == 3031
