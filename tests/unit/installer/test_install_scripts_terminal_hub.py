# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Coverage for terminal-hub installation in installer/scripts/install.{sh,ps1}.

Two layers, because a static check alone is what let the previous version ship
pointing at a channel nothing published to:

* static — the Agent Hub URL shape, the platform keys, the binary name, and the
  absence of bash-only syntax (the documented one-liner pipes into ``sh``);
* functional — ``install_tui`` and ``install_flagship_agent`` run for real
  against a throwaway HTTP server serving manifests in the Worker's shape. The
  hub is mandatory and the agent is optional, and only running them can tell a
  survivable failure (no build, download blip) from a fatal one (bad digest).
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
    # The hub id is a parameter now — one resolver serves the terminal hub and
    # the agent — so the URL is built from $AgentId rather than a literal.
    assert "/agents/$AgentId/manifest.json" in ps1_text
    assert "/agents/$AgentId/$version/$Filename" in ps1_text
    assert "-AgentId $TERMINAL_HUB_ID" in ps1_text


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
    # PowerShell installs through a shared helper, so the leaf name is passed
    # as -DestName and joined to $GAIA_BIN inside it.
    assert '-DestName "gaia-tui.exe"' in ps1_text
    assert "$GAIA_BIN\\$DestName" in ps1_text
    assert '"$GAIA_BIN/gaia"' not in sh_text
    assert '-DestName "gaia.exe"' not in ps1_text


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
    # Every failure branch aborts. Download and checksum handling moved into
    # download_and_verify, so install_tui turns its non-zero return into an
    # exit rather than owning that branch itself.
    assert "download_and_verify" in body
    assert body.count("exit 1") >= 5
    assert re.search(r"if ! download_and_verify.*?\n.*?exit 1", body, re.DOTALL)


def test_install_tui_refuses_an_unverified_binary(sh_text, ps1_text):
    # Both live in the shared helpers now — one copy of the rule for the
    # terminal hub and the agent, rather than a copy per caller.
    assert "publishes %s with no SHA-256" in _shell_function_body(
        sh_text, "read_hub_manifest"
    )
    assert "Checksum mismatch" in _shell_function_body(sh_text, "download_and_verify")
    assert "no SHA-256" in ps1_text
    assert "Checksum mismatch" in ps1_text


# ── the flagship agent sidecar ─────────────────────────────────────────────
#
# `gaia-tui` spawns `gaia-agent` as a child, so a bootstrap that installs only
# the hub leaves a UI with no agent under it.


def test_both_scripts_install_the_flagship_agent(sh_text, ps1_text):
    assert 'FLAGSHIP_AGENT_ID="gaia"' in sh_text
    assert '$FLAGSHIP_AGENT_ID = "gaia"' in ps1_text
    assert '"$GAIA_BIN/gaia-agent"' in sh_text
    assert '-DestName "gaia-agent.exe"' in ps1_text


def test_agent_is_fetched_under_the_hub_key_it_is_published_as(ps1_text):
    """The sidecar publishes as win32-x64; the terminal hub uses win-x64.

    Constructing `gaia-agent-win-x64.exe` 404s against a hub that only ever
    published `gaia-agent-win32-x64.exe`, and only on a cold fetch.
    """
    assert "-replace '^win-', 'win32-'" in ps1_text


def test_the_closing_banner_only_promises_an_agent_that_installed(sh_text, ps1_text):
    """Every early return above leaves no agent; the banner must not claim one."""
    assert "FLAGSHIP_INSTALLED=1" in sh_text
    assert 'if [ "${FLAGSHIP_INSTALLED:-0}" = "1" ]' in sh_text
    assert "-FlagshipInstalled" in ps1_text


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


HUB_PLATFORMS = ("linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64")
HUB_VERSION = "0.99.0"


@pytest.fixture
def fake_hub(tmp_path):
    """Serve Agent-Hub-shaped manifests plus their binaries over loopback.

    Yields ``(base_url, publish)``. ``publish`` (re)writes one agent's manifest
    and artifacts, so a test can stage exactly the failure it is about:
    a tampered digest, a manifest that resolves but whose binary 404s, or a
    manifest that publishes nothing for this machine's platform.
    """
    root = tmp_path / "hub"

    def publish(
        agent_id,
        prefix,
        platforms=HUB_PLATFORMS,
        digest_overrides=None,
        omit_binaries=False,
        also_prefixes=(),
    ):
        # `also_prefixes` puts a SECOND program in the same lane. The real
        # agents/gaia/ lane carries both the stdio child and the REST sidecar,
        # so a fixture with only one cannot tell "installed the agent" from
        # "installed the wrong agent" -- the failure this file has to catch.
        version_dir = root / "agents" / agent_id / HUB_VERSION
        version_dir.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for pfx in (prefix, *also_prefixes):
            for key in platforms:
                blob = f"#!/bin/sh\necho {pfx} {HUB_VERSION} {key}\n".encode()
                filename = f"{pfx}-{key}"
                if omit_binaries:
                    # Published in the manifest, absent from the bucket: the
                    # transient-download-failure case, which must not be fatal.
                    (version_dir / filename).unlink(missing_ok=True)
                else:
                    (version_dir / filename).write_bytes(blob)
                artifacts.append(
                    {
                        "filename": filename,
                        "path": f"agents/{agent_id}/{HUB_VERSION}/{filename}",
                        "size_bytes": len(blob),
                        "sha256": (digest_overrides or {}).get(
                            key, hashlib.sha256(blob).hexdigest()
                        ),
                        "content_type": "application/octet-stream",
                    }
                )
        manifest = {
            "id": agent_id,
            "latest_version": HUB_VERSION,
            "versions": {
                HUB_VERSION: {
                    "version": HUB_VERSION,
                    "artifact": artifacts[0],
                    "artifacts": artifacts,
                }
            },
        }
        (root / "agents" / agent_id / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    publish("terminal-hub", "gaia")
    publish("gaia", "gaia-agent-stdio", also_prefixes=("gaia-agent",))

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
        yield f"http://127.0.0.1:{server.server_port}", publish
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
    base_url, publish = fake_hub
    publish(
        "terminal-hub", "gaia", digest_overrides={k: "d" * 64 for k in HUB_PLATFORMS}
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


# ── functional: the flagship sidecar is optional, but never unverified ──────
#
# The whole point of the sidecar step is that it can fail without taking a
# complete install down with it — except on a digest mismatch. Asserting that
# over the script's source text cannot tell the two apart, so these run it.


def _run_bootstrap(tmp_path, base_url):
    """Run install_tui then install_flagship_agent, as main() does."""
    result, home = _run_sh_function(
        tmp_path,
        "DOWNLOAD_CMD=curl; install_tui; install_flagship_agent; "
        'echo "FLAGSHIP_INSTALLED=${FLAGSHIP_INSTALLED:-0}"',
        {"GAIA_HUB_BASE_URL": base_url},
    )
    bin_dir = home / ".gaia" / "bin"
    return result, bin_dir / "gaia-tui", bin_dir / "gaia-agent"


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
def test_a_verified_agent_installs_and_arms_the_banner(tmp_path, fake_hub):
    base_url, _ = fake_hub
    result, tui, agent = _run_bootstrap(tmp_path, base_url)

    assert result.returncode == 0, result.stdout + result.stderr
    assert tui.is_file() and agent.is_file()
    assert os.access(agent, os.X_OK)
    assert "FLAGSHIP_INSTALLED=1" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
def test_an_unpublished_agent_platform_leaves_the_hub_installed(tmp_path, fake_hub):
    """No build for this machine is a warning, not a failed install."""
    base_url, publish = fake_hub
    publish("gaia", "gaia-agent-stdio", platforms=("aix-ppc64",))

    result, tui, agent = _run_bootstrap(tmp_path, base_url)

    assert result.returncode == 0, result.stdout + result.stderr
    assert tui.is_file()
    assert not agent.exists()
    assert "Could not resolve the flagship agent" in result.stdout
    assert "FLAGSHIP_INSTALLED=0" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
def test_a_failed_agent_download_leaves_the_hub_installed(tmp_path, fake_hub):
    """A network blip after uv, the package, the hub and PATH are all in place
    must not throw that away — the sidecar is the only thing missing."""
    base_url, publish = fake_hub
    publish("gaia", "gaia-agent-stdio", omit_binaries=True)

    result, tui, agent = _run_bootstrap(tmp_path, base_url)

    assert result.returncode == 0, result.stdout + result.stderr
    assert tui.is_file()
    assert not agent.exists()
    assert "The GAIA agent did not install" in result.stdout
    assert "FLAGSHIP_INSTALLED=0" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
def test_a_tampered_agent_aborts_and_writes_nothing(tmp_path, fake_hub):
    """Integrity is the one agent failure that is never downgraded."""
    base_url, publish = fake_hub
    publish(
        "gaia",
        "gaia-agent-stdio",
        digest_overrides={k: "d" * 64 for k in HUB_PLATFORMS},
    )

    result, tui, agent = _run_bootstrap(tmp_path, base_url)

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stdout + result.stderr
    assert not agent.exists()
    # The hub landed before the agent step ran; only the agent is refused.
    assert tui.is_file()
    assert "FLAGSHIP_INSTALLED=1" not in result.stdout


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


# ── the agent must be the STDIO build, not the REST sidecar ────────────────
#
# `agents/gaia/` publishes two programs whose artifact names differ by one word.
# `gaia-agent-<platform>` is the REST sidecar the daemon supervises: it binds a
# port and never reads stdin. Installed as `gaia-agent` it satisfies the hub's
# readiness check and then feeds uvicorn's startup log to a JSON line scanner
# (#3062) — a green gate followed by a broken chat, which is worse than an
# honest "not installed".


@pytest.mark.parametrize("script", ["sh", "ps1"])
def test_the_agent_artifact_is_the_stdio_build(sh_text, ps1_text, script):
    text = sh_text if script == "sh" else ps1_text
    assert "gaia-agent-stdio" in text

    # The bare sidecar name must never be what gets constructed. Prose may
    # mention it to explain why; only executable lines are checked.
    code = "\n".join(
        line
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and line.strip()
    )
    for wrong in ('"gaia-agent-${platform}"', '"gaia-agent-$lockPlatform.exe"'):
        assert wrong not in code, f"install.{script} asks for the REST sidecar"


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
def test_the_installed_agent_is_the_stdio_artifact(tmp_path, fake_hub):
    """Both programs are published in the lane, so assert on the bytes.

    Checking only that `gaia-agent` exists passes just as happily when the
    sidecar was the thing downloaded.
    """
    base_url, _ = fake_hub
    result, _tui, agent = _run_bootstrap(tmp_path, base_url)

    assert result.returncode == 0, result.stdout + result.stderr
    # Each fake artifact echoes its own prefix, so the first token names which
    # of the two programs in the lane was downloaded. Substring-matching the
    # stdio name alone would not do: the sidecar's name is a prefix of it.
    body = agent.read_text(encoding="utf-8")
    assert "echo gaia-agent-stdio " in body, f"installed the wrong artifact: {body!r}"
