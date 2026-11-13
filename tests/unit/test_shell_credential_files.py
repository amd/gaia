# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A shell argument that reads a credential file is refused outright.

``security.is_read_blocked`` keeps ``.env``, ``id_rsa``, ``*.pem`` and the
rest out of the read tools; the shell must not be the way around it. No tier
is attached, so neither an approval nor full access lifts the refusal.
"""

import os
import sys

import pytest

from gaia.agents.tools import shell_tools
from gaia.agents.tools.shell_tools import TIER_REFUSE, ShellToolsMixin, _parse_line


class _Validator:
    """Allows everything under *root*; never prompts."""

    def __init__(self, root):
        self.root = os.path.realpath(root)

    def is_path_allowed(self, path, prompt_user=True):
        return os.path.realpath(path).startswith(self.root)


class _Console:
    def __init__(self, full_access):
        self.full_access = full_access
        self.auto_approve_gated_tools = full_access


class _Host(ShellToolsMixin):
    def __init__(self, root, full_access=False):
        self.console = _Console(full_access)
        self.debug = False
        self.path_validator = _Validator(root)


def _refusal(command, cwd, root):
    host = _Host(root)
    steps, error = _parse_line(command, bypass_gates=True)
    assert error is None, error
    for step in steps:
        refusal = host._path_traversal_refusal(step, str(cwd), frozenset())
        if refusal:
            return refusal
    return None


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temp HOME holding ``proj/`` with credentials beside ordinary files."""
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    for name, text in {
        ".env": "TOKEN=abc\n",
        "secrets.json": "{}\n",
        "secrets.md": "# notes\n",
        "env.txt": "plain\n",
        "data.json": "{}\n",
    }.items():
        (proj / name).write_text(text)
    (home / ".npmrc").write_text("//registry/:_authToken=x\n")
    clean = proj / "src"
    clean.mkdir()
    (clean / "a.py").write_text("x = 1\n")
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    return home


@pytest.mark.parametrize(
    "command",
    [
        "findstr TOKEN .env",
        "cat .env",
        "more .env",
        "type .env",
        "head -n 1 .env",
        "tail -n 1 .env",
        "grep -r TOKEN .",
        "grep -rn TOKEN",
        "grep --recursive TOKEN ../proj",
        "grep -R -e TOKEN .",
        "powershell Get-Content .env",
        'powershell -Command "Get-Content -Path .env"',
        'bash -c "cat $HOME/.npmrc"',
        "cat ~/.npmrc",
        "python -c \"open('.env')\"",
        "cat ../proj/.env",
        "cat SECRETS.JSON",
        "cat Secrets.Json",
        "cat ./x/server.PEM",
        "cat id_rsa.pub.key",
        "cat *.json",
        "findstr /s TOKEN *",
        "findstr /S /I TOKEN *.*",
        "findstr /s /c:TOKEN *",
        'powershell -Command "Get-ChildItem . -Recurse | Select-String TOKEN"',
        'powershell -Command "gci -r | sls TOKEN"',
        'powershell -Command "Select-String -Path * -Pattern TOKEN -Recurse"',
        "cp .env leak.txt",
        "base64 .env",
    ],
)
def test_reading_a_credential_file_is_refused(home, command):
    refusal = _refusal(command, home / "proj", home)

    assert refusal is not None, command
    assert refusal["executed"] is False
    assert "credentials" in refusal["error"]
    assert "tier" not in refusal


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path separators")
def test_a_backslash_path_to_a_credential_file_is_refused(home):
    refusal = _refusal(r"type ..\proj\.env", home / "proj", home)

    assert refusal is not None and refusal["executed"] is False


def test_a_cmd_caret_escaped_name_is_refused(home, monkeypatch):
    monkeypatch.setattr(shell_tools, "_pipe_dialect", lambda: "cmd")

    for command in ("type .e^nv", "type secrets.j^son", "type ^.^e^n^v"):
        refusal = _refusal(command, home / "proj", home)
        assert refusal is not None and refusal["executed"] is False, command


def test_a_link_to_a_credential_file_is_refused(home):
    proj = home / "proj"
    try:
        os.symlink(proj / ".env", proj / "notes.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")

    refusal = _refusal("cat notes.txt", proj, home)

    assert refusal is not None and refusal["executed"] is False
    assert ".env" in refusal["error"]


def test_a_resolved_link_is_judged_by_its_target(home, monkeypatch):
    """The same check without symlink privilege: the real path decides."""
    proj = home / "proj"
    (proj / "notes.txt").write_text("innocent\n")
    real = os.path.realpath

    def fake_realpath(path, *args, **kwargs):
        if os.path.basename(os.fspath(path)) == "notes.txt":
            return str(proj / ".env")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(shell_tools.os.path, "realpath", fake_realpath)

    refusal = _refusal("cat notes.txt", proj, home)

    assert refusal is not None and refusal["executed"] is False
    assert ".env" in refusal["error"]


def test_a_link_named_like_a_credential_is_refused_by_name(home):
    proj = home / "proj"
    refusal = _refusal("cat config/.netrc", proj, home)

    assert refusal is not None and refusal["executed"] is False


@pytest.mark.parametrize(
    "command",
    [
        "cat secrets.md",
        "cat env.txt",
        "cat data.json",
        "ls",
        "ls .",
        "ls ../proj",
        "dir",
        "grep -r TOKEN src",
        "grep -rn --include=*.py TOKEN .",
        "grep -r --exclude=.env --exclude=*.json TOKEN .",
        "grep TOKEN env.txt",
        "findstr TOKEN env.txt",
        "findstr /s TOKEN *.py",
        "getent passwd",
        "jq .data.key data.json",
        "echo .key",
        "cat *.md",
        "ls *.json",
        "git status",
        "git log --oneline -3",
        'powershell -Command "Get-ChildItem . -Recurse"',
        'powershell -Command "Get-ChildItem src -Recurse | Select-String TOKEN"',
        "curl https://example.com/.env",
    ],
)
def test_ordinary_commands_near_credentials_stay_allowed(home, command):
    assert _refusal(command, home / "proj", home) is None, command


def test_a_folder_named_like_a_credential_stays_listable(home):
    venv = home / "proj" / ".env"
    venv.unlink()
    venv.mkdir()

    assert _refusal("ls .env", home / "proj", home) is None


def test_a_credential_in_a_subfolder_is_not_found_by_a_recursive_search(home):
    """Documented limit: only the root's own entries are listed."""
    (home / "proj" / ".env").unlink()
    (home / "proj" / "secrets.json").unlink()
    (home / "proj" / "src" / ".env").write_text("TOKEN=abc\n")

    assert _refusal("grep -r TOKEN .", home / "proj", home) is None


def test_the_line_verdict_refuses_with_no_prompt(home):
    host = _Host(home)
    error, _ = host._line_verdict("cat .env", str(home / "proj"))

    assert error is not None
    assert error["tier"] == TIER_REFUSE
    assert error["executed"] is False


@pytest.fixture
def run_shell(monkeypatch):
    def build(host):
        captured = {}

        def fake_tool(**kwargs):
            def wrap(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return wrap

        import gaia.agents.base.tools as tools_mod

        monkeypatch.setattr(tools_mod, "tool", fake_tool)
        host.register_shell_tools()
        return captured["run_shell_command"]

    return build


@pytest.mark.parametrize(
    "command",
    [
        "findstr TOKEN .env" if os.name == "nt" else "cat .env",
        "findstr x SECRETS.JSON" if os.name == "nt" else "cat SECRETS.JSON",
        "findstr /s TOKEN *" if os.name == "nt" else "grep -r TOKEN .",
        "python -c \"print(open('.env').read())\"",
    ],
)
def test_full_access_does_not_lift_the_refusal(home, run_shell, command):
    run = run_shell(_Host(home, full_access=True))

    result = run(command, working_directory=str(home / "proj"))

    assert result["executed"] is False, result
    assert "credentials" in result["error"]
    assert "TOKEN=abc" not in str(result)
