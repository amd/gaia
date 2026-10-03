# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""PathValidator's host access prompt: ask before denying an out-of-scope path."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.security import BLOCKED_DIRECTORIES, PathValidator


class _Recorder:
    """Access prompt that records each path it is asked about."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def __call__(self, path):
        self.asked.append(path)
        return self.answer


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """A scoped folder plus an out-of-scope Documents folder.

    pytest's tmp_path sits in the system temp dir, which is never askable, so the
    temp rule is pointed at a dedicated folder instead.
    """
    fake_temp = tmp_path / "systemp"
    fake_temp.mkdir()
    monkeypatch.setattr("gaia.security._system_temp_roots", lambda: {str(fake_temp)})
    scope = tmp_path / "scope"
    scope.mkdir()
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "fed_rate.txt").write_text("rates held", encoding="utf-8")
    (docs / "other.txt").write_text("other", encoding="utf-8")
    return {"scope": scope, "docs": docs, "temp": fake_temp}


def _validator(layout, answer):
    validator = PathValidator(allowed_paths=[str(layout["scope"])])
    prompt = _Recorder(answer)
    validator.set_access_prompt(prompt)
    return validator, prompt


def test_approval_grants_only_the_requested_file(layout):
    validator, prompt = _validator(layout, True)
    before = set(validator.allowed_paths)
    target = layout["docs"] / "fed_rate.txt"

    assert validator.validate_read(str(target)) == (True, "")
    assert prompt.asked == [target.resolve()]
    assert validator.allowed_paths - before == {target.resolve()}

    # The sibling was not granted: it is asked about on its own.
    validator.validate_read(str(layout["docs"] / "other.txt"))
    assert len(prompt.asked) == 2


def test_approving_a_folder_covers_its_files_without_asking_again(layout):
    validator, prompt = _validator(layout, True)
    before = set(validator.allowed_paths)

    assert validator.is_path_allowed(str(layout["docs"]))
    assert validator.validate_read(str(layout["docs"] / "fed_rate.txt")) == (True, "")
    assert prompt.asked == [layout["docs"].resolve()]
    assert validator.allowed_paths - before == {layout["docs"].resolve()}
    # The parent of the granted folder stays out of scope.
    assert not validator.is_path_allowed(str(layout["docs"].parent), prompt_user=False)


def test_denial_refuses_and_grants_nothing(layout):
    validator, prompt = _validator(layout, False)
    before = set(validator.allowed_paths)

    allowed, reason = validator.validate_read(str(layout["docs"] / "fed_rate.txt"))

    assert not allowed
    assert "Access denied" in reason
    assert len(prompt.asked) == 1
    assert validator.allowed_paths == before


def test_only_a_literal_true_approves(layout):
    validator, _ = _validator(layout, "yes")
    assert not validator.is_path_allowed(str(layout["docs"]))


def test_approval_is_never_persisted(layout):
    validator = PathValidator()  # no host scope: the persisted-grants mode
    validator.set_access_prompt(_Recorder(True))
    with patch.object(validator, "_save_persisted_path") as save:
        assert validator.is_path_allowed(str(layout["docs"]))
    save.assert_not_called()


def test_prompt_user_false_never_asks(layout):
    validator, prompt = _validator(layout, True)
    assert not validator.is_path_allowed(str(layout["docs"]), prompt_user=False)
    assert prompt.asked == []


@pytest.mark.parametrize("name", [".env", "id_rsa", "server.pem"])
def test_secret_files_are_refused_without_asking(layout, name):
    secret = layout["docs"] / name
    secret.write_text("x", encoding="utf-8")
    validator, prompt = _validator(layout, True)

    allowed, _ = validator.validate_read(str(secret))

    assert not allowed
    assert prompt.asked == []


def test_secret_directory_is_refused_without_asking(layout):
    validator, prompt = _validator(layout, True)
    assert not validator.is_path_allowed(str(Path.home() / ".ssh" / "config"))
    assert prompt.asked == []


def test_protected_directory_is_refused_without_asking(layout):
    validator, prompt = _validator(layout, True)
    protected = sorted(BLOCKED_DIRECTORIES)[0]
    assert not validator.is_path_allowed(str(Path(protected) / "hosts"))
    assert prompt.asked == []


def test_system_temp_is_refused_without_asking(layout):
    validator, prompt = _validator(layout, True)
    assert not validator.is_path_allowed(str(layout["temp"] / "dropped.txt"))
    assert prompt.asked == []


def test_real_system_temp_is_refused_without_asking(tmp_path):
    validator = PathValidator(allowed_paths=[str(tmp_path / "scope")])
    prompt = _Recorder(True)
    validator.set_access_prompt(prompt)
    assert not validator.is_path_allowed(str(Path(tempfile.gettempdir()) / "x.txt"))
    assert prompt.asked == []


@pytest.mark.parametrize(
    "broad", [Path.home(), Path(Path.home().anchor)], ids=["home", "drive-root"]
)
def test_home_and_drive_root_are_refused_without_asking(layout, broad):
    validator, prompt = _validator(layout, True)
    assert not validator.is_path_allowed(str(broad))
    assert prompt.asked == []


def test_without_a_prompt_behaviour_is_unchanged(layout):
    validator = PathValidator(allowed_paths=[str(layout["scope"])])
    with (
        patch("gaia.security._is_interactive", return_value=False),
        patch("builtins.input") as ask,
    ):
        assert not validator.is_path_allowed(str(layout["docs"]))
    ask.assert_not_called()


def test_clearing_the_prompt_restores_stdin_behaviour(layout):
    validator, prompt = _validator(layout, True)
    validator.set_access_prompt(None)
    with patch("gaia.security._is_interactive", return_value=False):
        assert not validator.is_path_allowed(str(layout["docs"]))
    assert prompt.asked == []


def test_a_folder_that_contains_the_temp_dir_is_never_granted(tmp_path):
    import tempfile

    temp = Path(os.path.realpath(tempfile.gettempdir()))
    validator = PathValidator(allowed_paths=[str(tmp_path)])
    asked = []
    validator.set_access_prompt(lambda p: asked.append(p) or True)

    assert validator._unaskable_reason(temp.parent)
    assert not validator.is_path_allowed(str(temp.parent))
    assert asked == []
