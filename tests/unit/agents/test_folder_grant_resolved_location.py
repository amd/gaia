# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A folder grant covers a file only where that file really lands.

"Always allow" on a write in a folder covers later writes to files in that
folder. The PATH, home, drive-root, hidden, ``.git`` and program-shadow rules
apply to the file's resolved location as well as to the folder that was asked
about, and a file whose resolved location is in another folder is asked about
on its own.

Real links are created where the OS allows it; the same cases also run with
``os.path.realpath`` replaced, so every OS exercises the logic.
"""

import os

import pytest

from gaia.agents.base.tool_grants import grant_scope


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A granted folder, a sibling, a PATH folder and a home, all in tmp_path."""
    root = tmp_path.resolve()
    home = root / "home"
    work = root / "home" / "work"
    granted = work / "proj"
    sibling = work / "other"
    path_dir = root / "bin"
    for folder in (granted, sibling, path_dir):
        folder.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", str(path_dir))
    return {
        "home": home,
        "granted": granted,
        "sibling": sibling,
        "bin": path_dir,
    }


def _scope(path):
    return grant_scope(
        "write_file", {"file_path": str(path), "_resolved_file_path": str(path)}
    )


def _folder_key(folder):
    return f"write_file:{folder.as_posix()}/*"


def _covered(path, folder):
    return _folder_key(folder) in _scope(path).keys


def _symlink_or_skip(link, target):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"this OS does not allow creating a symlink here: {e}")


def _hardlink_or_skip(link, target):
    try:
        os.link(target, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"this filesystem does not allow a hard link here: {e}")


def _target(tree, where, name):
    if where == "home":
        return tree["home"] / name
    return tree[where] / name


class TestRealLinks:
    def test_a_plain_file_in_the_folder_is_covered(self, tree):
        target = tree["granted"] / "notes.md"
        target.write_text("x")
        assert _covered(target, tree["granted"])

    @pytest.mark.parametrize("where", ["bin", "home", "sibling"])
    def test_a_link_to_another_folder_is_not_covered(self, tree, where):
        target = _target(tree, where, "tool.cmd")
        target.write_text("x")
        link = tree["granted"] / "notes.md"
        _symlink_or_skip(link, target)
        scope = _scope(link)
        assert _folder_key(tree["granted"]) not in scope.keys
        assert not any(key.endswith("/*") for key in scope.keys)

    def test_a_link_to_a_file_in_the_same_folder_is_still_covered(self, tree):
        target = tree["granted"] / "real.md"
        target.write_text("x")
        link = tree["granted"] / "notes.md"
        _symlink_or_skip(link, target)
        assert _covered(link, tree["granted"])

    def test_a_chain_of_links_is_followed_to_its_end(self, tree):
        target = tree["bin"] / "tool.cmd"
        target.write_text("x")
        middle = tree["granted"] / "middle.md"
        _symlink_or_skip(middle, target)
        first = tree["granted"] / "notes.md"
        _symlink_or_skip(first, middle)
        assert not _covered(first, tree["granted"])

    def test_a_chain_that_stays_in_the_folder_is_covered(self, tree):
        target = tree["granted"] / "real.md"
        target.write_text("x")
        middle = tree["granted"] / "middle.md"
        _symlink_or_skip(middle, target)
        first = tree["granted"] / "notes.md"
        _symlink_or_skip(first, middle)
        assert _covered(first, tree["granted"])

    def test_a_dangling_link_out_of_the_folder_is_not_covered(self, tree):
        link = tree["granted"] / "notes.md"
        _symlink_or_skip(link, tree["bin"] / "missing.cmd")
        assert not _covered(link, tree["granted"])

    def test_a_dangling_link_inside_the_folder_is_covered(self, tree):
        link = tree["granted"] / "notes.md"
        _symlink_or_skip(link, tree["granted"] / "missing.md")
        assert _covered(link, tree["granted"])

    def test_a_hard_link_is_asked_about_on_its_own(self, tree):
        """A hard link resolves to itself, but writes go into the shared file,
        so a file with more than one name is never covered by a folder grant."""
        target = tree["bin"] / "tool.cmd"
        target.write_text("x")
        link = tree["granted"] / "notes.md"
        _hardlink_or_skip(link, target)
        assert not _covered(link, tree["granted"])

    def test_a_requested_folder_that_is_a_link_to_a_path_folder_is_not_granted(
        self, tree
    ):
        linked = tree["granted"] / "tools"
        if os.name == "nt":
            _winapi = pytest.importorskip("_winapi")
            try:
                _winapi.CreateJunction(str(tree["bin"]), str(linked))
            except OSError as e:
                pytest.skip(f"could not create a junction here: {e}")
        else:
            _symlink_or_skip(linked, tree["bin"])
        scope = _scope(linked / "notes.md")
        assert not any(key.endswith("/*") for key in scope.keys)


class TestPatchedResolution:
    """The same rules, with link resolution faked so they run on every OS."""

    @pytest.fixture
    def resolve_to(self, monkeypatch):
        real = os.path.realpath

        def _install(mapping):
            mapped = {os.path.normcase(str(k)): str(v) for k, v in mapping.items()}

            def fake(path, *args, **kwargs):
                hit = mapped.get(os.path.normcase(os.path.abspath(path)))
                return hit if hit is not None else real(path, *args, **kwargs)

            monkeypatch.setattr(os.path, "realpath", fake)

        return _install

    @pytest.mark.parametrize("where", ["bin", "home", "sibling"])
    def test_a_file_resolving_to_another_folder_is_not_covered(
        self, tree, resolve_to, where
    ):
        link = tree["granted"] / "notes.md"
        resolve_to({link: _target(tree, where, "notes.md")})
        assert not _covered(link, tree["granted"])

    def test_a_file_resolving_into_the_same_folder_is_covered(self, tree, resolve_to):
        link = tree["granted"] / "notes.md"
        resolve_to({link: tree["granted"] / "real.md"})
        assert _covered(link, tree["granted"])

    def test_a_file_resolving_into_a_hidden_folder_is_not_covered(
        self, tree, resolve_to
    ):
        hidden = tree["granted"].parent / ".config"
        link = tree["granted"] / "notes.md"
        resolve_to({link: hidden / "notes.md"})
        assert not _covered(link, tree["granted"])

    def test_a_file_resolving_under_git_is_not_covered(self, tree, resolve_to):
        link = tree["granted"] / "notes.md"
        resolve_to({link: tree["granted"] / ".git" / "hooks" / "pre-commit"})
        assert not _covered(link, tree["granted"])

    def test_a_requested_folder_resolving_to_a_path_folder_is_not_granted(
        self, tree, resolve_to
    ):
        folder = tree["granted"] / "tools"
        resolve_to({folder: tree["bin"], folder / "notes.md": tree["bin"] / "notes.md"})
        scope = _scope(folder / "notes.md")
        assert not any(key.endswith("/*") for key in scope.keys)

    def test_a_resolution_error_falls_back_to_the_exact_file(self, tree, monkeypatch):
        def broken(path, *args, **kwargs):
            raise OSError("cannot resolve")

        monkeypatch.setattr(os.path, "realpath", broken)
        path = tree["granted"] / "notes.md"
        assert _scope(path).keys == (f"write_file:{path.as_posix()}",)


class TestAGrantedConsole:
    """End to end through the console that records and checks grants."""

    def test_a_link_out_of_a_granted_folder_still_asks(self, tree):
        from gaia.agents.base.console import SilentConsole

        console = SilentConsole()
        plain = tree["granted"] / "notes.md"
        args = {"file_path": str(plain), "_resolved_file_path": str(plain)}
        assert console.grant_call_for_session("write_file", args)

        sibling = tree["granted"] / "todo.md"
        assert console.call_is_granted(
            "write_file",
            {"file_path": str(sibling), "_resolved_file_path": str(sibling)},
        )

        target = tree["bin"] / "tool.cmd"
        target.write_text("x")
        link = tree["granted"] / "run.md"
        _symlink_or_skip(link, target)
        assert not console.call_is_granted(
            "write_file", {"file_path": str(link), "_resolved_file_path": str(link)}
        )
