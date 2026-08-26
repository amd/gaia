# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Coverage for terminal-hub installation in installer/scripts/install.{sh,ps1}.

Two layers, because a static check alone is what let the previous version ship
pointing at a channel nothing published to:

* static — the Agent Hub URL shape, the platform keys, the binary name, and the
  absence of bash-only syntax (the documented one-liner pipes into ``sh``);
* functional — ``install_tui`` run for real against a throwaway HTTP server that
  serves a manifest in the Worker's shape, asserting it installs on a match and
  installs *nothing* on a checksum mismatch.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "installer" / "scripts"
INSTALL_SH = SCRIPTS_DIR / "install.sh"
INSTALL_PS1 = SCRIPTS_DIR / "install.ps1"
COMPONENT_MANIFEST = (
    REPO_ROOT / "hub" / "components" / "terminal-hub" / "gaia-agent.yaml"
)

# uname -m value -> Agent Hub platform-key architecture segment.
UNAME_TO_HUB_ARCH = {
    "x86_64": "x64",
    "amd64": "x64",
    "arm64": "arm64",
    "aarch64": "arm64",
}


@pytest.fixture(scope="module")
def sh_text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ps1_text() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


# ── the channel: Agent Hub R2, not GitHub release assets ────────────────────


def test_sh_reads_the_agent_hub_not_release_assets(sh_text):
    assert "releases/latest/download" not in sh_text
    assert "gaia-tui-SHA256SUMS.txt" not in sh_text
    assert "hub.amd-gaia.ai" in sh_text
    assert 'TERMINAL_HUB_ID="terminal-hub"' in sh_text
    assert "/agents/$TERMINAL_HUB_ID/manifest.json" in sh_text
    assert "/agents/$TERMINAL_HUB_ID/$version/$filename" in sh_text


def test_ps1_reads_the_agent_hub(ps1_text):
    assert "releases/latest/download" not in ps1_text
    assert "hub.amd-gaia.ai" in ps1_text
    assert '$TERMINAL_HUB_ID = "terminal-hub"' in ps1_text
    assert "/agents/$TERMINAL_HUB_ID/manifest.json" in ps1_text
    assert "/agents/$TERMINAL_HUB_ID/$version/$filename" in ps1_text


def test_component_id_matches_the_published_manifest():
    manifest = yaml.safe_load(COMPONENT_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["id"] == "terminal-hub"


# ── platform keys: the hub's keys, not GOARCH ───────────────────────────────


def test_platform_keys_match_the_component_manifest(sh_text, ps1_text):
    """The scripts must build keys the component actually declares."""
    manifest = yaml.safe_load(COMPONENT_MANIFEST.read_text(encoding="utf-8"))
    declared = set(manifest["requirements"]["platforms"])

    both = sh_text + ps1_text
    for key in declared:
        os_part, _, arch_part = key.partition("-")
        assert f'"{os_part}"' in both or f'"{key}"' in both, key
        assert f'"{arch_part}"' in both, key

    # `amd64` is GOARCH, not a hub platform key. It may appear only as a
    # `uname -m` input value, never as the arch half of a constructed key.
    assert not any(k.endswith("-amd64") for k in declared)
    assert "gaia-${os}-amd64" not in sh_text
    assert '_arch="amd64"' not in sh_text


@pytest.mark.parametrize("uname_m,expected", sorted(UNAME_TO_HUB_ARCH.items()))
def test_sh_maps_uname_arch_to_hub_arch(sh_text, uname_m, expected):
    assert f"{uname_m}" in sh_text
    assert f'_arch="{expected}"' in sh_text


def test_ps1_maps_processor_architecture_to_hub_keys(ps1_text):
    assert '"AMD64" { return "win-x64" }' in ps1_text
    assert '"ARM64" { return "win-arm64" }' in ps1_text


# ── the binary keeps the name `gaia-tui` ───────────────────────────────────


def test_binary_is_installed_as_gaia_tui(sh_text, ps1_text):
    """tui/internal/daemon/client.go resolves `gaia` on PATH to start the
    Python-owned daemon; a Go binary named `gaia` would find itself."""
    assert '"$GAIA_BIN/gaia-tui"' in sh_text
    assert "$GAIA_BIN\\gaia-tui.exe" in ps1_text
    assert '"$GAIA_BIN/gaia"' not in sh_text
    assert "$GAIA_BIN\\gaia.exe" not in ps1_text


def test_path_registration_covers_the_terminal_hub_bin_dir(sh_text, ps1_text):
    """Registering only the venv left gaia-tui installed but unreachable."""
    assert "$GAIA_VENV/bin:$GAIA_BIN" in sh_text
    assert '@("$GAIA_VENV\\Scripts", $GAIA_BIN)' in ps1_text


# ── the core install carries the daemon extras ─────────────────────────────


@pytest.mark.parametrize("script", ["sh", "ps1"])
def test_every_core_install_requests_the_daemon_extras(sh_text, ps1_text, script):
    """gaia-tui is useless without `gaia daemon`, which bare amd-gaia can't run.

    Both the fresh-install and the `--upgrade` call site must ask for [api];
    dropping it from either leaves that path installing a core that refuses to
    start the daemon (fastapi/uvicorn/psutil missing).
    """
    text = sh_text if script == "sh" else ps1_text
    call_sites = [
        line.strip()
        for line in text.splitlines()
        if "uv pip install" in line and "amd-gaia" in line
    ]
    assert len(call_sites) == 2, (
        f"expected the fresh-install and --upgrade pip call sites in "
        f"install.{script}; found {len(call_sites)}: {call_sites}"
    )
    bare = [line for line in call_sites if '"amd-gaia[api]"' not in line]
    assert not bare, (
        f"install.{script} installs amd-gaia without the [api] extra — the "
        "daemon needs fastapi/uvicorn/psutil and no `gaia init` profile "
        f"supplies them. Offending line(s): {bare}"
    )


# ── macOS is no longer gated out ───────────────────────────────────────────


def test_sh_supports_macos(sh_text):
    assert "This installer is for Linux only" not in sh_text
    # The old gate sent macOS users away with a bare pip install.
    assert "For macOS, please use" not in sh_text
    assert 'Darwin) OS_LABEL="macOS" ;;' in sh_text
    assert "GAIA Installer for Linux and macOS" in sh_text


def test_sh_names_windows_as_the_ps1_path(sh_text):
    assert "install.ps1 | iex" in sh_text


# ── fail loudly ────────────────────────────────────────────────────────────


def _shell_function_body(sh_text: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{$(.*?)^\}}$", sh_text, re.DOTALL | re.MULTILINE)
    assert match, f"{name}() not found in install.sh"
    return match.group(1)


def test_install_tui_never_soft_skips(sh_text):
    body = _shell_function_body(sh_text, "install_tui")
    assert "return 0" not in body
    assert "skipping" not in body
    # Every failure branch aborts.
    assert body.count("exit 1") >= 6


def test_install_tui_refuses_an_unverified_binary(sh_text, ps1_text):
    body = _shell_function_body(sh_text, "install_tui")
    assert "publishes %s with no SHA-256" in body
    assert "Checksum mismatch" in body
    assert "no SHA-256" in ps1_text
    assert "Checksum mismatch" in ps1_text


def test_elevation_is_announced_before_it_is_needed(sh_text, ps1_text):
    assert "announce_elevation" in sh_text
    assert sh_text.index("announce_elevation\n") < sh_text.index("install_uv\n\n")
    assert "Show-ElevationNotice" in ps1_text


# ── the documented one-liner pipes into `sh`, so no bash-only syntax ───────


BASH_ONLY = [
    "[[",
    "$OSTYPE",
    "set -o pipefail",
    "echo -e",
    "\nsource ",
    "trap 'rm -rf \"$tmp\"' RETURN",
]


@pytest.mark.parametrize("construct", BASH_ONLY)
def test_sh_is_free_of_bash_only_syntax(sh_text, construct):
    """`curl … | sh` runs under dash on Debian/Ubuntu, which dies on these."""
    # Comments may name a construct to explain why it is avoided; only the
    # executable lines have to be free of it.
    code = "\n".join(
        line for line in sh_text.splitlines() if not line.lstrip().startswith("#")
    )
    assert construct not in code


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_sh_parses_under_posix_sh():
    result = subprocess.run(
        ["sh", "-n", str(INSTALL_SH)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
def test_sh_parses_under_dash():
    result = subprocess.run(
        ["dash", "-n", str(INSTALL_SH)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not installed")
def test_ps1_parses_without_errors():
    """install.ps1 had no automated verification of any kind before this."""
    script = (
        "$e=$null;$t=$null;"
        "$null=[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{INSTALL_PS1}',[ref]$t,[ref]$e);"
        "if($e.Count){$e|ForEach-Object{"
        '"line $($_.Extent.StartLineNumber): $($_.Message)"};exit 1}'
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ── functional: run install_tui against a fake Agent Hub ───────────────────


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102 - silence the test log
        pass


@pytest.fixture
def fake_hub(tmp_path):
    """Serve an Agent-Hub-shaped manifest plus one binary over loopback."""
    root = tmp_path / "hub"
    version_dir = root / "agents" / "terminal-hub" / "0.99.0"
    version_dir.mkdir(parents=True)

    payload = b"#!/bin/sh\necho gaia-tui 0.99.0\n"
    digests = {}
    for key in ("linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"):
        (version_dir / f"gaia-{key}").write_bytes(payload + key.encode())
        digests[key] = hashlib.sha256(payload + key.encode()).hexdigest()

    def write_manifest(overrides=None):
        artifacts = [
            {
                "filename": f"gaia-{key}",
                "path": f"agents/terminal-hub/0.99.0/gaia-{key}",
                "size_bytes": len(payload) + len(key),
                "sha256": (overrides or {}).get(key, digest),
                "content_type": "application/octet-stream",
            }
            for key, digest in digests.items()
        ]
        manifest = {
            "id": "terminal-hub",
            "latest_version": "0.99.0",
            "versions": {
                "0.99.0": {
                    "version": "0.99.0",
                    "artifact": artifacts[0],
                    "artifacts": artifacts,
                }
            },
        }
        (root / "agents" / "terminal-hub" / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    write_manifest()

    handler = type(
        "Handler",
        (_QuietHandler,),
        {
            "__init__": lambda s, *a, **k: _QuietHandler.__init__(
                s, *a, directory=str(root), **k
            )
        },
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", write_manifest
    finally:
        server.shutdown()
        server.server_close()


def _run_sh_function(tmp_path, snippet, env_overrides=None, shell="sh"):
    """Load install.sh's functions (without main) and run `snippet`."""
    funcs = tmp_path / "funcs.sh"
    body = INSTALL_SH.read_text(encoding="utf-8").replace('\nmain "$@"\n', "\n")
    funcs.write_text(body, encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)

    env = dict(os.environ)
    env["HOME"] = str(home)
    env.update(env_overrides or {})

    result = subprocess.run(
        [shell, "-c", f'. "{funcs}"; {snippet}'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result, home


def _run_install_tui(tmp_path, base_url, downloader="curl", shell="sh"):
    result, home = _run_sh_function(
        tmp_path,
        f"DOWNLOAD_CMD={downloader}; install_tui",
        {"GAIA_HUB_BASE_URL": base_url},
        shell=shell,
    )
    return result, home / ".gaia" / "bin" / "gaia-tui"


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.parametrize("downloader", ["curl", "wget"])
def test_install_tui_installs_a_verified_binary(tmp_path, fake_hub, downloader):
    if shutil.which(downloader) is None:
        pytest.skip(f"{downloader} not installed")
    base_url, _ = fake_hub
    result, installed = _run_install_tui(tmp_path, base_url, downloader=downloader)

    assert result.returncode == 0, result.stdout + result.stderr
    assert installed.is_file()
    assert os.access(installed, os.X_OK)
    assert "0.99.0" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
def test_install_tui_works_under_dash(tmp_path, fake_hub):
    """`curl … | sh` is dash on Debian/Ubuntu; macOS `sh` is bash, so a
    curl-only, sh-only run would never exercise the real Linux shell."""
    base_url, _ = fake_hub
    result, installed = _run_install_tui(tmp_path, base_url, shell="dash")

    assert result.returncode == 0, result.stdout + result.stderr
    assert installed.is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
def test_install_tui_installs_nothing_on_a_checksum_mismatch(tmp_path, fake_hub):
    base_url, write_manifest = fake_hub
    write_manifest(
        overrides={
            key: "d" * 64
            for key in ("linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64")
        }
    )

    result, installed = _run_install_tui(tmp_path, base_url)

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stdout + result.stderr
    assert not installed.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
def test_install_tui_fails_when_the_hub_is_unreachable(tmp_path):
    result, installed = _run_install_tui(tmp_path, "http://127.0.0.1:1")

    assert result.returncode != 0
    assert "Could not fetch the terminal hub manifest" in result.stdout + result.stderr
    assert not installed.exists()


# ── functional: add_to_path ────────────────────────────────────────────────


def _rc_files(home):
    return sorted(p.name for p in home.iterdir() if p.name.startswith("."))


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_writes_the_zsh_rc_and_is_idempotent(tmp_path):
    result, home = _run_sh_function(
        tmp_path, "OS_NAME=Darwin; add_to_path", {"SHELL": "/bin/zsh"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _rc_files(home) == [".zshrc"]

    rc = home / ".zshrc"
    first = rc.read_text(encoding="utf-8")
    assert ".gaia/venv/bin" in first and ".gaia/bin" in first

    result, _ = _run_sh_function(
        tmp_path, "OS_NAME=Darwin; add_to_path", {"SHELL": "/bin/zsh"}
    )
    assert result.returncode == 0
    assert rc.read_text(encoding="utf-8") == first


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_does_not_shadow_an_existing_profile_on_macos(tmp_path):
    """Creating ~/.bash_profile where only ~/.profile exists would stop bash
    login shells from ever reading the user's environment."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text("export MY_SETTING=1\n", encoding="utf-8")

    result, home = _run_sh_function(
        tmp_path, "OS_NAME=Darwin; add_to_path", {"SHELL": "/bin/bash"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (home / ".bash_profile").exists()
    assert ".gaia/bin" in (home / ".profile").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_upgrades_a_pre_existing_venv_only_export(tmp_path):
    """Older installs wrote a venv-only export; a line-exact idempotency check
    would leave the terminal hub off PATH forever."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text(
        f'export PATH="$PATH:{home}/.gaia/venv/bin"\n', encoding="utf-8"
    )

    result, home = _run_sh_function(
        tmp_path, "OS_NAME=Linux; add_to_path", {"SHELL": "/bin/zsh"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert ".gaia/bin" in (home / ".zshrc").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_does_not_write_a_file_an_unknown_shell_ignores(tmp_path):
    """fish never reads ~/.profile, and `source ~/.profile` errors in it."""
    result, home = _run_sh_function(
        tmp_path, "OS_NAME=Linux; add_to_path", {"SHELL": "/usr/bin/fish"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _rc_files(home) == []
    assert "Add these two directories to your PATH by hand" in result.stdout
