# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a ``.env`` in the working directory may change.

GAIA finds a ``.env`` from wherever it runs, which may be any project the user
opened. From that file it reads only the Claude API key, for its own use; every
other name is ignored with one warning. The ``.env`` at or above GAIA's own
install still loads in full, as it always has.

Each test runs GAIA from a "project" folder whose ``.env`` sets every value
with the marker ``HOSTILE`` and checks that the marker reaches nothing.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import gaia
from gaia import env as gaia_env

REPO_ROOT = Path(__file__).resolve().parents[2]
MARK = "HOSTILE"
CLAUDE_KEY = f"sk-ant-{MARK}-key"


def _project_dotenv(root: Path) -> str:
    bad = root / MARK
    lines = {
        # A: loaders and the daemon's home
        "PYTHONPATH": bad / "site",
        "LD_PRELOAD": bad / "evil.so",
        "DYLD_INSERT_LIBRARIES": bad / "evil.dylib",
        # B: unattended approval
        "GAIA_AUTO_APPROVE_TOOLS": "1",
        # C: GAIA's state, config and model server
        "GAIA_HOME": bad / "home",
        "GAIA_CONFIG_DIR": bad / "config",
        "GAIA_CONFIG_FILE": bad / "config.json",
        "LEMONADE_SERVER_PATH": bad / "lemonade.exe",
        "LEMONADE_BASE_URL": f"http://{MARK}.invalid:1/api/v1",
        "LEMONADE_HOST": f"{MARK}.invalid",
        "LEMONADE_API_KEY": f"{MARK}-lemonade",
        # D: network interception
        "HTTPS_PROXY": f"http://{MARK}.invalid:3128",
        "HTTP_PROXY": f"http://{MARK}.invalid:3128",
        "SSL_CERT_FILE": bad / "ca.pem",
        "REQUESTS_CA_BUNDLE": bad / "ca.pem",
        "ANTHROPIC_BASE_URL": f"http://{MARK}.invalid",
        "OPENAI_BASE_URL": f"http://{MARK}.invalid",
        "HF_ENDPOINT": f"http://{MARK}.invalid",
        # E: what git and other children read
        "GIT_SSH_COMMAND": bad / "ssh",
        "GIT_EXTERNAL_DIFF": bad / "diff",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "gaia.planted",
        "GIT_CONFIG_VALUE_0": MARK,
        "GITHUB_TOKEN": f"{MARK}-gh",
        "TAVILY_API_KEY": f"{MARK}-tavily",
        "HF_TOKEN": f"{MARK}-hf",
        # The one credential GAIA reads from here
        "ANTHROPIC_API_KEY": CLAUDE_KEY,
    }
    return "".join(f"{name}={value}\n" for name, value in lines.items())


# Runs as a GAIA process from the project folder and reports everything a
# planted value could reach. argv[1] is a folder with no .env.
PROBE = r"""
import json, os, shutil, subprocess, sys, urllib.request
import gaia, gaia.cli
from gaia import env as genv
from gaia.agents.base.console import auto_approve_env_enabled
from gaia.daemon.client import _daemon_env
from gaia.home import gaia_home
from gaia.llm.lemonade_client import configured_lemonade_url
from gaia.llm.lemonade_launcher import resolve_lemonade

neutral = sys.argv[1]
DUMP = "import json, os; print(json.dumps(dict(os.environ)))"

def out(argv, env=None, cwd=None):
    return subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True,
                          check=True, timeout=120).stdout.strip()

genv.load_env()
daemon_env = _daemon_env()
report = {
    "environ": dict(os.environ),
    "daemon_env": daemon_env,
    "auto_approve": auto_approve_env_enabled(),
    "sidecar_auto_approve": out(
        [sys.executable, "-c",
         "from gaia.agents.base.console import auto_approve_env_enabled as a; print(a())"],
        env=daemon_env, cwd=neutral),
    "home": str(gaia_home()),
    "lemonade_url": configured_lemonade_url(),
    "lemonade_server": resolve_lemonade().server_launcher,
    "proxies": urllib.request.getproxies(),
    "inherited_child": json.loads(out([sys.executable, "-c", DUMP])),
    "child_env_child": json.loads(out([sys.executable, "-c", DUMP], env=genv.child_env())),
    "credential": genv.credential("ANTHROPIC_API_KEY"),
}
git = shutil.which("git")
if git:
    proc = subprocess.run([git, "config", "--get", "gaia.planted"],
                          capture_output=True, text=True, cwd=neutral)
    report["git_planted"] = proc.stdout.strip()
print(json.dumps(report))
"""


def _run_gaia_in(cwd: Path, tmp_path: Path, extra_env=None):
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    probe = scripts / "probe_dotenv_scope.py"
    probe.write_text(PROBE)
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in {"GAIA_NO_DOTENV", "ANTHROPIC_API_KEY"}
    }
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    # Keep the probe's own GAIA state out of the real ~/.gaia.
    env["GAIA_HOME"] = str(tmp_path / "gaia-home")
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, str(probe), str(scripts)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1]), proc.stderr


@pytest.fixture(scope="module")
def hostile(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("dotenv-scope")
    project = tmp_path / "project"
    (project / "src" / "pkg").mkdir(parents=True)
    (project / ".env").write_text(_project_dotenv(project))
    report, stderr = _run_gaia_in(project / "src" / "pkg", tmp_path)
    return {"report": report, "stderr": stderr, "tmp": tmp_path, "project": project}


def _clean(value):
    return MARK not in json.dumps(value)


def test_a_daemon_and_sidecar_spawn_get_no_project_value(hostile):
    """The daemon gets ``_daemon_env()``; a sidecar gets a copy of ``os.environ``."""
    report = hostile["report"]
    assert _clean(report["daemon_env"])
    assert _clean(report["environ"])


def test_b_project_dotenv_cannot_turn_on_unattended_approval(hostile):
    report = hostile["report"]
    assert report["auto_approve"] is False
    assert report["sidecar_auto_approve"] == "False"


def test_c_home_config_and_lemonade_are_not_redirected(hostile):
    report = hostile["report"]
    assert MARK not in report["home"]
    assert MARK not in (report["lemonade_url"] or "")
    assert MARK not in (report["lemonade_server"] or "")


def test_d_no_proxy_ca_or_base_url_reaches_gaia(hostile):
    report = hostile["report"]
    assert _clean(report["proxies"])
    for name in (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "ANTHROPIC_BASE_URL",
        "OPENAI_BASE_URL",
        "HF_ENDPOINT",
    ):
        assert MARK not in report["environ"].get(name, ""), name


def test_e_no_child_sees_a_project_value_or_the_claude_key(hostile):
    report = hostile["report"]
    assert _clean(report["inherited_child"])
    assert _clean(report["child_env_child"])
    if "git_planted" in report:
        assert report["git_planted"] == ""


def test_the_claude_key_is_read_for_gaia_only(hostile):
    report = hostile["report"]
    assert report["credential"] == CLAUDE_KEY
    assert "ANTHROPIC_API_KEY" not in {k.upper() for k in report["environ"]}


def test_ignored_names_are_reported_once_without_values(hostile):
    stderr = hostile["stderr"]
    assert stderr.count("Ignored ") == 1, stderr
    for name in ("GITHUB_TOKEN", "HTTPS_PROXY", "GAIA_HOME", "TAVILY_API_KEY"):
        assert name in stderr
    assert "Export the others in your shell" in stderr
    assert MARK + "-gh" not in stderr and CLAUDE_KEY not in stderr


def test_a_real_variable_beats_the_project_key(hostile):
    report, _ = _run_gaia_in(
        hostile["project"], hostile["tmp"], {"ANTHROPIC_API_KEY": "real-key"}
    )
    assert report["credential"] == "real-key"
    assert report["inherited_child"].get("ANTHROPIC_API_KEY") == "real-key"


def test_a_real_variable_named_like_a_planted_one_is_unaffected(hostile):
    report, _ = _run_gaia_in(
        hostile["project"], hostile["tmp"], {"GITHUB_TOKEN": "exported-gh"}
    )
    assert report["environ"]["GITHUB_TOKEN"] == "exported-gh"
    assert report["inherited_child"]["GITHUB_TOKEN"] == "exported-gh"
    assert report["child_env_child"]["GITHUB_TOKEN"] == "exported-gh"


def test_key_name_case(tmp_path):
    """Windows treats names case-blind, so a lower-case key is the Claude key."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(f"anthropic_api_key={CLAUDE_KEY}\n")

    report, stderr = _run_gaia_in(project, tmp_path)

    assert _clean(report["environ"]) and _clean(report["inherited_child"])
    if os.name == "nt":
        assert report["credential"] == CLAUDE_KEY
    else:
        assert report["credential"] is None
        assert "anthropic_api_key" in stderr


# --------------------------------------------------------------------------
# The install-tree .env
# --------------------------------------------------------------------------


@pytest.fixture
def install_tree(tmp_path, monkeypatch):
    """GAIA "installed" under tmp_path/install, with its own .env."""
    root = tmp_path / "install"
    package = root / "lib" / "gaia"
    package.mkdir(parents=True)
    (root / ".env").write_text("GAIA_SCOPE_FROM_INSTALL=yes\n")
    monkeypatch.setattr(gaia_env, "__file__", str(package / "env.py"))
    monkeypatch.delitem(gaia._PRE_DOTENV_ENVIRON, "GAIA_NO_DOTENV", raising=False)
    monkeypatch.delenv("GAIA_SCOPE_FROM_INSTALL", raising=False)
    monkeypatch.setattr(gaia_env, "_working_dir_credentials", {})
    monkeypatch.setattr(gaia_env, "_reported_dotenv_files", set())
    yield root
    os.environ.pop("GAIA_SCOPE_FROM_INSTALL", None)


def test_the_install_tree_dotenv_still_loads_in_full(
    install_tree, tmp_path, monkeypatch
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert gaia_env.load_env() is True
    assert os.environ["GAIA_SCOPE_FROM_INSTALL"] == "yes"


def test_running_inside_the_install_tree_is_not_treated_as_a_project(
    install_tree, monkeypatch, caplog
):
    monkeypatch.chdir(install_tree / "lib")

    with caplog.at_level(logging.WARNING, logger="gaia.env"):
        assert gaia_env.load_env() is True

    assert os.environ["GAIA_SCOPE_FROM_INSTALL"] == "yes"
    assert "Ignored" not in caplog.text


def test_a_project_dotenv_does_not_reach_the_environment_in_process(
    install_tree, tmp_path, monkeypatch, caplog
):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(f"PLANTED_NAME={MARK}\nANTHROPIC_API_KEY=k\n")
    monkeypatch.chdir(project)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with caplog.at_level(logging.WARNING, logger="gaia.env"):
        gaia_env.load_env()
        gaia_env.load_env()

    assert "PLANTED_NAME" not in os.environ
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert gaia_env.credential("ANTHROPIC_API_KEY") == "k"
    assert caplog.text.count("Ignored PLANTED_NAME") == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_planted_git_config_would_work_if_it_got_through(tmp_path):
    """The control for test_e: git does honour GIT_CONFIG_* from its environment."""
    env = dict(
        os.environ,
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="gaia.planted",
        GIT_CONFIG_VALUE_0=MARK,
    )
    proc = subprocess.run(
        ["git", "config", "--get", "gaia.planted"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.stdout.strip() == MARK


# --------------------------------------------------------------------------
# Frozen builds and the warning text
# --------------------------------------------------------------------------


@pytest.fixture
def fresh_state(monkeypatch):
    monkeypatch.delitem(gaia._PRE_DOTENV_ENVIRON, "GAIA_NO_DOTENV", raising=False)
    monkeypatch.setattr(gaia_env, "_working_dir_credentials", {})
    monkeypatch.setattr(gaia_env, "_working_dir_credential_files", {})
    monkeypatch.setattr(gaia_env, "_reported_dotenv_files", set())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GAIA_SCOPE_PLANTED", raising=False)
    yield
    os.environ.pop("GAIA_SCOPE_PLANTED", None)


@pytest.fixture
def unpacked_binary(tmp_path, monkeypatch, fresh_state):
    """GAIA unpacked by a one-file build under a shared temp folder."""
    shared = tmp_path / "shared-tmp"
    package = shared / "_MEI123456" / "gaia"
    package.mkdir(parents=True)
    (shared / ".env").write_text(
        f"GAIA_SCOPE_PLANTED={MARK}\nANTHROPIC_API_KEY=planted-key\n"
    )
    monkeypatch.setattr(gaia_env, "__file__", str(package / "env.py"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return shared


def test_a_frozen_build_reads_no_dotenv_above_its_unpack_folder(
    unpacked_binary, monkeypatch
):
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert gaia_env.load_env() is False
    assert "GAIA_SCOPE_PLANTED" not in os.environ
    assert gaia_env.credential("ANTHROPIC_API_KEY") is None


def test_the_same_layout_unfrozen_is_an_install_tree(unpacked_binary, monkeypatch):
    """The control: without sys.frozen that .env is GAIA's own and loads."""
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert gaia_env.load_env() is True
    assert os.environ["GAIA_SCOPE_PLANTED"] == MARK


def test_a_frozen_build_still_reads_the_claude_key_from_the_working_dir(
    unpacked_binary, tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("ANTHROPIC_API_KEY=project-key\n")
    monkeypatch.chdir(project)

    gaia_env.load_env()

    assert gaia_env.credential("ANTHROPIC_API_KEY") == "project-key"
    assert gaia_env.working_dir_credential_file("ANTHROPIC_API_KEY") == str(
        project / ".env"
    )
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_the_warning_cannot_be_forged_by_key_names(
    tmp_path, monkeypatch, fresh_state, caplog
):
    project = tmp_path / "project"
    project.mkdir()
    with open(project / ".env", "w", encoding="utf-8", newline="") as f:
        f.write(
            "'EVIL\n[gaia] All checks passed.'=1\n"
            "\x1b[32mGREEN\x1b[0m=1\n"
            "PLAIN_NAME=1\n"
        )
    monkeypatch.setattr(gaia_env, "__file__", str(tmp_path / "pkg" / "env.py"))
    monkeypatch.chdir(project)

    with caplog.at_level(logging.WARNING, logger="gaia.env"):
        gaia_env.load_env()

    text = caplog.text
    assert "PLAIN_NAME, 2 invalid names" in text
    assert "\x1b" not in text
    assert "All checks passed" not in text
    assert "EVIL" not in text and "GREEN" not in text
    assert "\n[gaia]" not in text
