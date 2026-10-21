# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Coverage for the binaries installer/scripts/install.{sh,ps1} download.

The terminal hub (`gaia-tui`) comes from the GitHub release, verified against
the release's ``gaia-tui-SHA256SUMS.txt``; the flagship agent comes from the
Agent Hub, verified against its manifest. Two layers, because a static check
alone is what once let these scripts ship pointing at a channel nothing
published to:

* static — the URL shapes, the asset and platform names (checked against what
  publish.yml actually builds and attaches), the binary names, and the absence
  of bash-only syntax (the documented one-liner pipes into ``sh``);
* functional — ``install_tui`` / ``install_flagship_agent`` and their
  PowerShell twins run for real against a throwaway HTTP server shaped like
  GitHub releases plus the Agent Hub. The hub is mandatory and the agent is
  optional, and only running them can tell a survivable failure (no build,
  download blip) from a fatal one (bad digest).
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
PUBLISH_YML = REPO_ROOT / ".github" / "workflows" / "publish.yml"
FLAGSHIP_LOCK = REPO_ROOT / "hub" / "agents" / "gaia" / "npm" / "binaries.lock.json"
SUMS_FILE = "gaia-tui-SHA256SUMS.txt"

# uname -m value -> (Agent Hub arch segment, GOARCH segment of the release asset).
UNAME_ARCH = {
    "x86_64": ("x64", "amd64"),
    "amd64": ("x64", "amd64"),
    "arm64": ("arm64", "arm64"),
    "aarch64": ("arm64", "arm64"),
}


@pytest.fixture(scope="module")
def sh_text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ps1_text() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


def _shell_function_body(sh_text: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{$(.*?)^\}}$", sh_text, re.DOTALL | re.MULTILINE)
    assert match, f"{name}() not found in install.sh"
    return match.group(1)


def _ps1_function_body(ps1_text: str, name: str) -> str:
    match = re.search(
        rf"^function {name} \{{\r?$(.*?)^\}}\r?$", ps1_text, re.DOTALL | re.MULTILINE
    )
    assert match, f"function {name} not found in install.ps1"
    return match.group(1)


# ── the channels: GitHub release for the hub, Agent Hub for the agent ───────


def test_sh_takes_the_terminal_hub_from_the_github_release(sh_text):
    assert "GAIA_RELEASES_URL:-https://github.com/amd/gaia/releases" in sh_text
    assert f'TUI_SUMS_FILE="{SUMS_FILE}"' in sh_text
    body = _shell_function_body(sh_text, "install_tui")
    assert 'release_url="$GAIA_RELEASES_URL/download/$RELEASE_TAG"' in body
    # No second channel to fall back to.
    assert "hub.amd-gaia.ai" not in body and "GAIA_HUB_BASE_URL" not in body
    assert "terminal-hub" not in sh_text


def test_ps1_takes_the_terminal_hub_from_the_github_release(ps1_text):
    assert '"https://github.com/amd/gaia/releases"' in ps1_text
    assert f'$TUI_SUMS_FILE = "{SUMS_FILE}"' in ps1_text
    body = _ps1_function_body(ps1_text, "Resolve-TuiAsset")
    assert '$releaseUrl = "$GAIA_RELEASES_URL/download/$tag"' in body
    assert "GAIA_HUB_BASE_URL" not in _ps1_function_body(ps1_text, "Install-Tui")
    assert "terminal-hub" not in ps1_text


def test_both_scripts_take_the_agent_from_the_agent_hub(sh_text, ps1_text):
    assert "GAIA_HUB_BASE_URL:-https://hub.amd-gaia.ai" in sh_text
    assert '"https://hub.amd-gaia.ai"' in ps1_text
    assert "/agents/$FLAGSHIP_AGENT_ID/manifest.json" in sh_text
    assert "/agents/$AgentId/manifest.json" in ps1_text


def test_latest_is_read_from_the_redirect_not_the_rate_limited_api(sh_text, ps1_text):
    assert "api.github.com" not in sh_text + ps1_text
    body = _shell_function_body(sh_text, "resolve_release_tag")
    assert '"$GAIA_RELEASES_URL/latest"' in body
    assert "%{redirect_url}" in body and "--max-redirect=0" in body
    ps_body = _ps1_function_body(ps1_text, "Resolve-ReleaseTag")
    assert "AllowAutoRedirect = $false" in ps_body


# ── asset names: exactly what publish.yml builds and attaches ──────────────


def _publish_tui_assets() -> set[str]:
    wf = yaml.safe_load(PUBLISH_YML.read_text(encoding="utf-8"))
    job = wf["jobs"]["build-tui"]
    build = "\n".join(s.get("run", "") for s in job["steps"])
    assert (
        "-o ../release-assets/gaia-${{ matrix.goos }}-${{ matrix.goarch }}${EXT}"
        in build
    )
    names = set()
    for leg in job["strategy"]["matrix"]["include"]:
        ext = ".exe" if leg["goos"] == "windows" else ""
        names.add(f"gaia-{leg['goos']}-{leg['goarch']}{ext}")
    return names


def test_publish_checksums_every_terminal_binary_it_attaches():
    text = PUBLISH_YML.read_text(encoding="utf-8")
    assert f"sha256sum gaia-* > {SUMS_FILE}" in text
    # The final and the RC release steps both attach release-assets/*.
    assert text.count("release-assets/*") >= 2


def test_sh_asks_for_assets_publish_builds(sh_text):
    built = _publish_tui_assets()
    body = _shell_function_body(sh_text, "tui_asset_name")
    assert "printf 'gaia-%s-%s\\n'" in body
    for os_name in ("linux", "darwin"):
        assert f'_os="{os_name}"' in body
        for _, goarch in UNAME_ARCH.values():
            assert f"gaia-{os_name}-{goarch}" in built


def test_ps1_asks_for_assets_publish_builds(ps1_text):
    built = _publish_tui_assets()
    body = _ps1_function_body(ps1_text, "Get-TuiAssetName")
    asked = set(re.findall(r'return "(gaia-[a-z0-9-]+\.exe)"', body))
    assert asked == {"gaia-windows-amd64.exe", "gaia-windows-arm64.exe"}
    assert asked <= built


@pytest.mark.parametrize("uname_m,expected", sorted(UNAME_ARCH.items()))
def test_sh_maps_uname_arch_for_each_channel(sh_text, uname_m, expected):
    hub_arch, goarch = expected
    hub = _shell_function_body(sh_text, "hub_platform")
    tui = _shell_function_body(sh_text, "tui_asset_name")
    assert uname_m in hub and uname_m in tui
    assert f'_arch="{hub_arch}"' in hub
    assert f'_arch="{goarch}"' in tui


def test_agent_keys_are_the_ones_the_flagship_lock_publishes(ps1_text):
    lock = json.loads(FLAGSHIP_LOCK.read_text(encoding="utf-8"))
    published = set(lock["components"]["sidecar"]["platforms"])
    for key in ("linux-x64", "darwin-x64", "darwin-arm64", "win32-x64"):
        assert key in published
    # PowerShell renames win-x64 to the sidecar's win32-x64.
    assert "-replace '^win-', 'win32-'" in ps1_text
    assert '"AMD64" { return "win-x64" }' in _ps1_function_body(
        ps1_text, "Get-HubPlatform"
    )


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


@pytest.mark.parametrize("script", ["sh", "ps1"])
def test_every_core_install_compiles_bytecode(sh_text, ps1_text, script):
    """uv skips bytecode compilation by default, so the first daemon start
    compiled all of GAIA and overran its health wait on a fresh install."""
    text = sh_text if script == "sh" else ps1_text
    call_sites = [
        line.strip()
        for line in text.splitlines()
        if "uv pip install" in line and "amd-gaia" in line
    ]
    assert call_sites
    missing = [line for line in call_sites if "--compile-bytecode" not in line]
    assert not missing, f"install.{script} skips bytecode compilation: {missing}"


def test_every_ps1_uv_call_trusts_the_windows_certificate_store(ps1_text):
    """Behind a TLS-inspecting proxy, uv's bundled roots reject the proxy's
    certificate, so the Python download in `uv venv` failed on a fresh PC with
    `invalid peer certificate: UnknownIssuer` and the install stopped there."""
    uv_calls = [
        line.strip()
        for line in ps1_text.splitlines()
        if re.match(r"\s*& uv (venv|pip)\b", line)
    ]
    assert len(uv_calls) == 3, uv_calls
    missing = [line for line in uv_calls if "--native-tls" not in line]
    assert not missing, f"uv call(s) without --native-tls: {missing}"


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
    # No digest from the release's checksum file, no install.
    assert "publishes no SHA-256 for $filename" in _shell_function_body(
        sh_text, "install_tui"
    )
    assert "publishes no SHA-256 for $Filename" in _ps1_function_body(
        ps1_text, "Resolve-TuiAsset"
    )
    # The agent's digest comes from its hub manifest instead.
    assert "publishes %s with no SHA-256" in _shell_function_body(
        sh_text, "read_hub_manifest"
    )
    # One verify step shared by both binaries, rather than a copy per caller.
    assert "Checksum mismatch" in _shell_function_body(sh_text, "download_and_verify")
    assert "Checksum mismatch" in _ps1_function_body(ps1_text, "Install-VerifiedBinary")


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


PALETTE_GO = REPO_ROOT / "tui" / "internal" / "ui" / "chat" / "palette.go"


def _tui_commands() -> set[str]:
    source = PALETTE_GO.read_text(encoding="utf-8")
    block = re.search(
        r"^var paletteCommands = \[\]paletteCommand\{$(.*?)^\}$",
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert block, f"paletteCommands not found in {PALETTE_GO}"
    commands = set(re.findall(r'\{"(/[a-z-]+)",', block.group(1)))
    assert commands, "paletteCommands parsed as empty"
    return commands


def _banner_commands(text: str, start: str, end: str) -> set[str]:
    banner = text[text.index(start) : text.index(end, text.index(start))]
    # printf wraps the command in colour placeholders: '%s/agents%s'.
    banner = banner.replace("%s", " ")
    # A `/word` not glued to a URL, path, or variable.
    return set(re.findall(r"(?<![\w/.:~$%-])/[a-z][a-z-]*\b", banner))


@pytest.mark.parametrize("script", ["sh", "ps1"])
def test_banner_slash_commands_exist_in_the_tui(sh_text, ps1_text, script):
    """An unknown `/command` is sent to the model as a question, not run."""
    if script == "sh":
        banner = _banner_commands(sh_text, "show_next_steps() {", "\n}\n")
    else:
        banner = _banner_commands(ps1_text, "function Show-NextSteps {", "\n}\n")
    assert banner, f"no /command found in the install.{script} closing banner"
    unknown = banner - _tui_commands()
    assert not unknown, (
        f"install.{script} tells users to type {sorted(unknown)}, which "
        f"paletteCommands in {PALETTE_GO.relative_to(REPO_ROOT)} does not define"
    )


def test_elevation_is_announced_before_it_is_needed(sh_text):
    assert "announce_elevation" in sh_text
    assert sh_text.index("announce_elevation\n") < sh_text.index("install_uv\n\n")


def test_windows_install_promises_no_elevation(ps1_text):
    """`gaia init` installs GAIA's own Lemonade on Windows: no MSI, no UAC."""
    assert "UAC" not in ps1_text
    assert "administrator approval" not in ps1_text
    assert "refreshenv" not in ps1_text


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


# ── install.ps1 runs inside the user's own session under `irm | iex` ───────
#
# Off Windows the script refuses to run, which is a real failure path that
# needs no stubbing. On Windows the same run would install for real, so skip.

needs_pwsh = pytest.mark.skipif(shutil.which("pwsh") is None, reason="no pwsh")
off_windows = pytest.mark.skipif(sys.platform == "win32", reason="would install")


def _run_pwsh(args):
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


@needs_pwsh
@off_windows
def test_a_failed_iex_install_leaves_the_session_alive_and_clean():
    """`exit 1` under iex closed the user's terminal along with the Fix: hint,
    and a run left Stop and a shadowed Write-Error behind in their session."""
    script = (
        f"$src = Get-Content -Raw -LiteralPath '{INSTALL_PS1}';"
        'try { Invoke-Expression $src } catch { Write-Host "caught: $_" };'
        "Write-Host 'alive';"
        'Write-Host "eap=$ErrorActionPreference";'
        'Write-Host "write-error=$((Get-Command Write-Error).CommandType)";'
        'Write-Host "write-warning=$((Get-Command Write-Warning).CommandType)";'
        'Write-Host "leaked=$([bool](Get-Command Install-Gaia -ErrorAction Ignore))"'
    )
    result = _run_pwsh(["-Command", script])
    out = result.stdout + result.stderr

    assert "This installer is for Windows" in out
    assert "alive" in result.stdout, out
    assert "caught: " in result.stdout
    assert "eap=Continue" in result.stdout
    assert "write-error=Cmdlet" in result.stdout
    assert "write-warning=Cmdlet" in result.stdout
    assert "leaked=False" in result.stdout


@needs_pwsh
@off_windows
def test_a_failed_file_install_still_exits_non_zero():
    """`pwsh -File install.ps1` must keep reporting failure to its caller."""
    result = _run_pwsh(["-File", str(INSTALL_PS1)])
    assert result.returncode != 0
    assert "This installer is for Windows" in result.stdout + result.stderr


def test_ps1_never_calls_exit(ps1_text):
    """Under iex, `exit` ends the user's PowerShell session, not the script."""
    code = [
        line
        for line in ps1_text.splitlines()
        if re.match(r"^\s*exit\b", line) or re.search(r"[;{]\s*exit\b", line)
    ]
    assert not code, code


# ── functional: a fake GitHub release + Agent Hub ──────────────────────────


RELEASE_TAG = "v0.99.0"
OLDER_TAG = "v0.98.0"
TUI_ASSETS = (
    "gaia-linux-amd64",
    "gaia-linux-arm64",
    "gaia-darwin-amd64",
    "gaia-darwin-arm64",
    "gaia-windows-amd64.exe",
    "gaia-windows-arm64.exe",
)
HUB_PLATFORMS = ("linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64")
WIN_HUB_PLATFORMS = ("win32-x64", "win32-arm64")
HUB_VERSION = "0.99.0"


def _make_handler(root: Path, state: dict):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, *args):  # noqa: D102 - silence the test log
            pass

        def _redirect_latest(self) -> bool:
            if self.path.rstrip("/") != "/releases/latest":
                return False
            self.send_response(302)
            self.send_header(
                "Location", f"{state['base']}/releases/tag/{state['latest']}"
            )
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

        def do_GET(self):  # noqa: N802 - http.server API
            if not self._redirect_latest():
                super().do_GET()

        def do_HEAD(self):  # noqa: N802 - http.server API
            if not self._redirect_latest():
                super().do_HEAD()

    return Handler


@pytest.fixture
def fake_hub(tmp_path):
    """Serve GitHub-release-shaped terminal binaries and Agent-Hub-shaped
    agent manifests over loopback.

    Yields an object with ``base`` and two stagers, so a test can stage exactly
    the failure it is about: ``release`` (re)writes one tag's binaries and
    checksum file, ``publish`` one hub agent's manifest and artifacts.
    """
    root = tmp_path / "srv"
    state = {"latest": RELEASE_TAG}

    def release(
        tag=RELEASE_TAG,
        digest_overrides=None,
        omit_sums_for=(),
        sums_style="text",
        omit_sums_file=False,
    ):
        tag_dir = root / "releases" / "download" / tag
        tag_dir.mkdir(parents=True, exist_ok=True)
        lines = []
        for name in TUI_ASSETS:
            blob = f"#!/bin/sh\necho gaia {tag} {name}\n".encode()
            (tag_dir / name).write_bytes(blob)
            if name in omit_sums_for:
                continue
            digest = (digest_overrides or {}).get(
                name, hashlib.sha256(blob).hexdigest()
            )
            sep = " *" if sums_style == "binary" else "  "
            lines.append(f"{digest}{sep}{name}\n")
        sums = tag_dir / SUMS_FILE
        if omit_sums_file:
            sums.unlink(missing_ok=True)
        else:
            sums.write_text("".join(lines), encoding="utf-8")

    def publish(
        agent_id,
        prefix,
        platforms=HUB_PLATFORMS + WIN_HUB_PLATFORMS,
        digest_overrides=None,
        omit_binaries=False,
    ):
        version_dir = root / "agents" / agent_id / HUB_VERSION
        version_dir.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for key in platforms:
            blob = f"#!/bin/sh\necho {prefix} {HUB_VERSION} {key}\n".encode()
            filename = f"{prefix}-{key}" + (".exe" if key.startswith("win") else "")
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

    release()
    release(tag=OLDER_TAG)
    publish("gaia", "gaia-agent")

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _make_handler(root, state)
    )
    state["base"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class Fake:
        base = state["base"]

    fake = Fake()
    fake.release = release
    fake.publish = publish
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()


def _channel_env(base_url):
    return {
        "GAIA_RELEASES_URL": f"{base_url}/releases",
        "GAIA_HUB_BASE_URL": base_url,
    }


def _run_sh_function(tmp_path, snippet, env_overrides=None, shell="sh"):
    """Load install.sh's functions (without main) and run `snippet`."""
    funcs = tmp_path / "funcs.sh"
    body = INSTALL_SH.read_text(encoding="utf-8").replace('\nmain "$@"\n', "\n")
    funcs.write_text(body, encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)

    env = dict(os.environ)
    env.pop("GAIA_RELEASE_TAG", None)
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


def _run_install_tui(tmp_path, base_url, downloader="curl", shell="sh", tag=None):
    env = _channel_env(base_url)
    if tag is not None:
        env["GAIA_RELEASE_TAG"] = tag
    result, home = _run_sh_function(
        tmp_path, f"DOWNLOAD_CMD={downloader}; install_tui", env, shell=shell
    )
    return result, home / ".gaia" / "bin" / "gaia-tui"


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
needs_curl = pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")


@posix_only
@pytest.mark.parametrize("downloader", ["curl", "wget"])
def test_install_tui_installs_the_latest_release(tmp_path, fake_hub, downloader):
    if shutil.which(downloader) is None:
        pytest.skip(f"{downloader} not installed")
    result, installed = _run_install_tui(tmp_path, fake_hub.base, downloader=downloader)

    assert result.returncode == 0, result.stdout + result.stderr
    assert installed.is_file()
    assert os.access(installed, os.X_OK)
    # The latest redirect, not the other tag the release server also carries.
    assert RELEASE_TAG in installed.read_text(encoding="utf-8")
    assert f"GAIA Terminal UI {RELEASE_TAG} installed" in result.stdout


@posix_only
@pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
def test_install_tui_works_under_dash(tmp_path, fake_hub):
    """`curl … | sh` is dash on Debian/Ubuntu; macOS `sh` is bash, so a
    curl-only, sh-only run would never exercise the real Linux shell."""
    result, installed = _run_install_tui(tmp_path, fake_hub.base, shell="dash")

    assert result.returncode == 0, result.stdout + result.stderr
    assert installed.is_file()


@posix_only
@needs_curl
def test_a_pinned_tag_installs_that_release(tmp_path, fake_hub):
    result, installed = _run_install_tui(tmp_path, fake_hub.base, tag=OLDER_TAG)

    assert result.returncode == 0, result.stdout + result.stderr
    assert OLDER_TAG in installed.read_text(encoding="utf-8")


@posix_only
@needs_curl
def test_binary_mode_checksum_lines_are_accepted(tmp_path, fake_hub):
    """`sha256sum -b` writes `<digest> *<name>`; the digest is the same."""
    fake_hub.release(sums_style="binary")
    result, installed = _run_install_tui(tmp_path, fake_hub.base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert installed.is_file()


@posix_only
@needs_curl
def test_install_tui_installs_nothing_on_a_checksum_mismatch(tmp_path, fake_hub):
    fake_hub.release(digest_overrides={n: "d" * 64 for n in TUI_ASSETS})

    result, installed = _run_install_tui(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stdout + result.stderr
    assert not installed.exists()


@posix_only
@needs_curl
def test_a_binary_missing_from_the_checksums_is_refused(tmp_path, fake_hub):
    fake_hub.release(omit_sums_for=TUI_ASSETS)

    result, installed = _run_install_tui(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "publishes no SHA-256 for" in result.stdout
    assert not installed.exists()


@posix_only
@needs_curl
def test_a_release_without_its_checksum_file_is_refused(tmp_path, fake_hub):
    """The window while a release is still uploading its assets."""
    fake_hub.release(omit_sums_file=True)

    result, installed = _run_install_tui(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "Could not fetch the GAIA Terminal UI checksums" in result.stdout
    assert "still be uploading" in result.stdout
    assert not installed.exists()


@posix_only
@needs_curl
@pytest.mark.parametrize(
    "tag", ["v0.25.0;id", "v0.25.0/../x", "0.25.0", "latest", "v0.25.0 x"]
)
def test_a_malformed_tag_is_refused_before_any_download(tmp_path, fake_hub, tag):
    result, installed = _run_install_tui(tmp_path, fake_hub.base, tag=tag)

    assert result.returncode != 0
    assert "is not a release tag" in result.stdout
    assert not installed.exists()


@posix_only
@needs_curl
def test_install_tui_fails_when_github_is_unreachable(tmp_path):
    result, installed = _run_install_tui(tmp_path, "http://127.0.0.1:1")

    assert result.returncode != 0
    assert "Could not find the GAIA release" in result.stdout + result.stderr
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
        _channel_env(base_url),
    )
    bin_dir = home / ".gaia" / "bin"
    return result, bin_dir / "gaia-tui", bin_dir / "gaia-agent"


@posix_only
@needs_curl
def test_a_verified_agent_installs_and_arms_the_banner(tmp_path, fake_hub):
    result, tui, agent = _run_bootstrap(tmp_path, fake_hub.base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert tui.is_file() and agent.is_file()
    assert os.access(agent, os.X_OK)
    assert "FLAGSHIP_INSTALLED=1" in result.stdout


@posix_only
@needs_curl
def test_an_unpublished_agent_platform_leaves_the_hub_installed(tmp_path, fake_hub):
    """No build for this machine is a warning, not a failed install."""
    fake_hub.publish("gaia", "gaia-agent", platforms=("aix-ppc64",))

    result, tui, agent = _run_bootstrap(tmp_path, fake_hub.base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert tui.is_file()
    assert not agent.exists()
    assert "Could not resolve the flagship agent" in result.stdout
    assert "FLAGSHIP_INSTALLED=0" in result.stdout


@posix_only
@needs_curl
def test_a_failed_agent_download_leaves_the_hub_installed(tmp_path, fake_hub):
    """A network blip after uv, the package, the hub and PATH are all in place
    must not throw that away — the sidecar is the only thing missing."""
    fake_hub.publish("gaia", "gaia-agent", omit_binaries=True)

    result, tui, agent = _run_bootstrap(tmp_path, fake_hub.base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert tui.is_file()
    assert not agent.exists()
    assert "The GAIA agent did not install" in result.stdout
    assert "FLAGSHIP_INSTALLED=0" in result.stdout


@posix_only
@needs_curl
def test_a_tampered_agent_aborts_and_writes_nothing(tmp_path, fake_hub):
    """Integrity is the one agent failure that is never downgraded."""
    fake_hub.publish(
        "gaia", "gaia-agent", digest_overrides={k: "d" * 64 for k in HUB_PLATFORMS}
    )

    result, tui, agent = _run_bootstrap(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stdout + result.stderr
    assert not agent.exists()
    # The hub landed before the agent step ran; only the agent is refused.
    assert tui.is_file()
    assert "FLAGSHIP_INSTALLED=1" not in result.stdout


# ── functional: the same two installs in install.ps1 ───────────────────────
#
# Main is cut off, so nothing touches PATH, uv or the package. $GAIA_BIN and
# TEMP are pointed into tmp_path so the run is the same on Windows and on the
# Linux pwsh CI uses.

_PS_EXE = shutil.which("pwsh") or (
    shutil.which("powershell") if sys.platform == "win32" else None
)
needs_powershell = pytest.mark.skipif(_PS_EXE is None, reason="no PowerShell")


def _run_ps1(tmp_path, base_url, snippet, tag=None):
    text = INSTALL_PS1.read_text(encoding="utf-8")
    cut = text.rindex("\nMain\n") if "\nMain\n" in text else text.rindex("\r\nMain\r\n")
    bin_dir = tmp_path / "ps-bin"
    script = tmp_path / "run.ps1"
    script.write_text(
        text[:cut] + "\n$GAIA_BIN = $env:GAIA_TEST_BIN\n" + snippet + "\n}\n",
        encoding="utf-8-sig",
    )
    env = dict(os.environ)
    env.pop("GAIA_RELEASE_TAG", None)
    env.pop("PROCESSOR_ARCHITEW6432", None)
    env.update(_channel_env(base_url))
    env.update(
        {
            "GAIA_TEST_BIN": str(bin_dir),
            "PROCESSOR_ARCHITECTURE": "AMD64",
            "TEMP": str(tmp_path),
            "USERPROFILE": str(tmp_path / "profile"),
            "PSModuleAnalysisCachePath": str(tmp_path / "ps-module-cache"),
        }
    )
    if tag is not None:
        env["GAIA_RELEASE_TAG"] = tag
    result = subprocess.run(
        [
            _PS_EXE,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
        timeout=120,
    )
    return result, bin_dir


@needs_powershell
def test_ps1_installs_the_latest_release_and_the_agent(tmp_path, fake_hub):
    result, bin_dir = _run_ps1(
        tmp_path,
        fake_hub.base,
        'Install-Tui; $ok = Install-FlagshipAgent; Write-Host "FLAGSHIP=$ok"',
    )
    out = result.stdout + result.stderr

    assert result.returncode == 0, out
    tui = bin_dir / "gaia-tui.exe"
    assert RELEASE_TAG in tui.read_text(encoding="utf-8")
    assert "gaia-windows-amd64.exe" in tui.read_text(encoding="utf-8")
    assert "win32-x64" in (bin_dir / "gaia-agent.exe").read_text(encoding="utf-8")
    assert "FLAGSHIP=True" in result.stdout


@needs_powershell
def test_ps1_a_pinned_tag_installs_that_release(tmp_path, fake_hub):
    result, bin_dir = _run_ps1(tmp_path, fake_hub.base, "Install-Tui", tag=OLDER_TAG)

    assert result.returncode == 0, result.stdout + result.stderr
    assert OLDER_TAG in (bin_dir / "gaia-tui.exe").read_text(encoding="utf-8")


@needs_powershell
def test_ps1_installs_nothing_on_a_checksum_mismatch(tmp_path, fake_hub):
    fake_hub.release(digest_overrides={n: "d" * 64 for n in TUI_ASSETS})

    result, bin_dir = _run_ps1(tmp_path, fake_hub.base, "Install-Tui")

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stdout
    assert not (bin_dir / "gaia-tui.exe").exists()


@needs_powershell
def test_ps1_refuses_a_binary_missing_from_the_checksums(tmp_path, fake_hub):
    fake_hub.release(omit_sums_for=("gaia-windows-amd64.exe",))

    result, bin_dir = _run_ps1(tmp_path, fake_hub.base, "Install-Tui")

    assert result.returncode != 0
    assert "publishes no SHA-256 for gaia-windows-amd64.exe" in result.stdout
    assert not (bin_dir / "gaia-tui.exe").exists()


@needs_powershell
@pytest.mark.parametrize("tag", ["v0.25.0;id", "0.25.0", "v0.25.0/../x"])
def test_ps1_refuses_a_malformed_tag(tmp_path, fake_hub, tag):
    result, bin_dir = _run_ps1(tmp_path, fake_hub.base, "Install-Tui", tag=tag)

    assert result.returncode != 0
    assert "is not a GAIA release tag" in result.stdout
    assert not bin_dir.exists()


@needs_powershell
def test_ps1_a_tampered_agent_fails_the_install(tmp_path, fake_hub):
    """-Optional softens a missing agent, never a bad digest."""
    fake_hub.publish(
        "gaia", "gaia-agent", digest_overrides={k: "d" * 64 for k in WIN_HUB_PLATFORMS}
    )

    result, bin_dir = _run_ps1(tmp_path, fake_hub.base, "Install-FlagshipAgent")

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stdout
    assert not (bin_dir / "gaia-agent.exe").exists()


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
