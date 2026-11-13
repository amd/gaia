# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Coverage for the binaries installer/scripts/install.{sh,ps1} download.

The frozen ``gaia`` and the terminal hub (`gaia-tui`) come from the GitHub
release, verified against its ``gaia-core-SHA256SUMS.txt`` and
``gaia-tui-SHA256SUMS.txt``; the flagship agent comes from the Agent Hub,
verified against its manifest. Neither script installs Python or a PyPI
package. Two layers, because a static check
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
import tarfile
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
CORE_SUMS_FILE = "gaia-core-SHA256SUMS.txt"
# Every platform build_core.yml can freeze; install.ps1 takes win32-x64 on Arm too.
CORE_PLATFORMS = ("linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64", "win32-x64")

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


def _publish_jobs() -> dict:
    return yaml.safe_load(PUBLISH_YML.read_text(encoding="utf-8"))["jobs"]


def test_publish_checksums_exactly_the_six_binaries_it_builds():
    """A failed or cancelled build-tui leg must stop the release: install.sh
    and install.ps1 fetch these from the latest release, and a missing one is a
    broken install for that platform until the next release."""
    job = _publish_jobs()["github-release"]
    assert "build-tui" in job["needs"]
    assert "needs.build-tui.result == 'success'" in job["if"]
    step = next(s for s in job["steps"] if s.get("name") == "Checksum TUI binaries")
    listed = re.search(r"expected=\(([^)]*)\)", step["run"])
    assert listed, "the checksum step no longer lists the binaries it expects"
    assert set(listed.group(1).split()) == _publish_tui_assets()
    assert 'sha256sum "${expected[@]}" > gaia-tui-SHA256SUMS.txt' in step["run"]
    assert "exit 1" in step["run"]
    # The final and RC release steps now pass every discovered asset to the
    # resumable helper rather than relying on a shell wildcard.
    release_step = next(
        s
        for s in job["steps"]
        if s.get("name") == "Create GitHub Release with bounded rate-limit retry"
    )
    assert "find release-assets dist -type f -print0" in release_step["run"]
    assert 'assets+=(--asset "$asset")' in release_step["run"]
    assert "release-assets/*" not in PUBLISH_YML.read_text(encoding="utf-8")


def test_publish_windows_binaries_carry_icon_and_unprefixed_version():
    steps = _publish_jobs()["build-tui"]["steps"]
    by_name = {s.get("name"): s for s in steps}
    winres = by_name["Generate Windows resources (icon + version)"]
    check = by_name["Verify the Windows binary carries its icon and version"]
    build = by_name["Build"]
    assert "tui/scripts/gen-winres.sh" in winres["run"]
    assert "util/check_pe_resources.py --expect-version" in check["run"]
    # One version string for all three, without the tag's `v`, as
    # tui_installers.yml stamps the copy inside the Windows setup.
    for step in (winres, check, build):
        assert step["env"]["VERSION"] == "${{ needs.validate.outputs.npm_version }}"
    assert "version=${VERSION}" in build["run"]
    assert "tag_name" not in build["run"]
    names = [s.get("name") for s in steps]
    assert (
        names.index(winres["name"]) < names.index("Build") < names.index(check["name"])
    )


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
    # as -DestName and joined to -DestDir, $GAIA_BIN by default, inside it.
    assert '-DestName "gaia-tui.exe"' in ps1_text
    assert "[string]$DestDir = $GAIA_BIN" in ps1_text
    assert "$DestDir\\$DestName" in ps1_text
    assert '"$GAIA_BIN/gaia"' not in _shell_function_body(sh_text, "install_tui")
    assert '-DestName "gaia.exe"' not in ps1_text


def test_path_registration_covers_the_terminal_hub_bin_dir(sh_text, ps1_text):
    """Registering only the venv left gaia-tui installed but unreachable."""
    body = _shell_function_body(sh_text, "add_to_path")
    assert 'path_export="export PATH=\\"\\$PATH:$GAIA_BIN\\""' in body
    assert 'PATH="$PATH:$GAIA_BIN"' in body
    body = _ps1_function_body(ps1_text, "Get-UpdatedPath")
    assert "$added = @($GAIA_BIN)" in body


def test_ps1_puts_only_gaia_bin_on_path(ps1_text):
    """The venv's Scripts holds python.exe and pip.exe; on PATH they shadow
    the user's own Python."""
    # The venv is named only to take it out, or to warn against adding it.
    update = _ps1_function_body(ps1_text, "Get-UpdatedPath")
    assert [ln.strip() for ln in update.splitlines() if "$GAIA_VENV" in ln] == [
        '$legacy = Get-NormalizedPathEntry "$GAIA_VENV\\Scripts"'
    ]
    add = _ps1_function_body(ps1_text, "Add-ToPath")
    venv_lines = [ln for ln in add.splitlines() if "$GAIA_VENV" in ln]
    assert venv_lines and all(
        "Do not add" in ln or "Removed" in ln for ln in venv_lines
    ), venv_lines


def test_ps1_copies_exactly_the_packages_console_scripts(ps1_text):
    """$GAIA_BIN is the only directory on PATH, so every console script the
    package ships must be copied there or it becomes unreachable."""
    match = re.search(r"^\$GAIA_COMMANDS = @\(([^)]*)\)\r?$", ps1_text, re.MULTILINE)
    assert match, "$GAIA_COMMANDS not found in install.ps1"
    listed = set(re.findall(r'"([\w-]+)"', match.group(1)))
    setup_py = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")
    block = re.search(r'"console_scripts": \[(.*?)\]', setup_py, re.DOTALL)
    assert block, "console_scripts not found in setup.py"
    shipped = set(re.findall(r'"([\w-]+) = ', block.group(1)))
    assert shipped and listed == shipped


def test_ps1_installs_gaia_before_it_edits_path(ps1_text):
    """A failed install must leave the old PATH, which still reaches `gaia`."""
    main = _ps1_function_body(ps1_text, "Main")
    assert main.index("Install-Core") < main.index("Add-ToPath")


def test_sh_links_exactly_the_packages_console_scripts(sh_text):
    """$GAIA_BIN is the only directory on PATH (#6603), so every console
    script the package ships must be linked there or it becomes unreachable."""
    match = re.search(r'^GAIA_COMMANDS="([^"]+)"$', sh_text, re.MULTILINE)
    assert match, "GAIA_COMMANDS not found in install.sh"
    setup_py = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")
    block = re.search(r'"console_scripts": \[(.*?)\]', setup_py, re.DOTALL)
    assert block, "console_scripts not found in setup.py"
    shipped = set(re.findall(r'"([\w-]+) = ', block.group(1)))
    assert shipped and set(match.group(1).split()) == shipped


# ── gaia is the frozen build from the release, never a Python install ──────


def _code_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


@pytest.mark.parametrize("script", ["sh", "ps1"])
def test_neither_installer_installs_python_or_a_pypi_package(sh_text, ps1_text, script):
    """The apps run binaries only; a pip or uv install here is the PyPI install
    the user's PC must never get."""
    text = sh_text if script == "sh" else ps1_text
    hits = [
        ln.strip()
        for ln in _code_lines(text)
        if re.search(
            r"\buv\b|pip install|astral\.sh|amd-gaia\[|\bvenv\b(?!\\Scripts)", ln
        )
        and "GAIA_VENV" not in ln
    ]
    assert not hits, f"install.{script} still installs Python: {hits}"


def test_both_installers_verify_gaia_against_the_release_checksums(sh_text, ps1_text):
    assert f'CORE_SUMS_FILE="{CORE_SUMS_FILE}"' in sh_text
    body = _shell_function_body(sh_text, "install_core")
    assert "download_and_verify" in body and "$CORE_SUMS_FILE" in body
    assert f'$CORE_SUMS_FILE = "{CORE_SUMS_FILE}"' in ps1_text
    body = _ps1_function_body(ps1_text, "Install-Core")
    assert "-SumsName $CORE_SUMS_FILE" in body and "Install-VerifiedBinary" in body


def test_publish_attaches_the_gaia_the_installers_fetch():
    text = PUBLISH_YML.read_text(encoding="utf-8")
    assert CORE_SUMS_FILE in text
    for platform in ("win32-x64", "darwin-arm64", "linux-x64"):
        assert platform in text


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
    assert "no single valid SHA-256 for $filename" in _shell_function_body(
        sh_text, "install_tui"
    )
    assert "no single valid SHA-256 for $Filename" in _ps1_function_body(
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


def test_unix_install_promises_no_elevation(sh_text):
    """`gaia init` installs GAIA's own Lemonade on Linux and macOS: no sudo."""
    assert "needs your password" not in sh_text
    assert "asks for your password" not in sh_text
    assert "via sudo" not in sh_text


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
RC_TAG = "v0.99.1-rc1"
RC_AGENT_VERSION = "9.9.9"
RC_AGENT_PLATFORMS = (
    "linux-x64",
    "linux-arm64",
    "darwin-x64",
    "darwin-arm64",
    "win32-x64",
)
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
            location = state.get("latest_location") or (
                f"{state['base']}/releases/tag/{state['latest']}"
            )
            self.send_header("Location", location)
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


def _core_tarball(path: Path, tag: str) -> None:
    """A release's frozen gaia, shaped like build_core.yml's tarball.

    Its `gaia` hands its arguments to this Python, so it reads manifests with
    `-c` the way the frozen one does.
    """
    staged = path.parent / f"{path.name}.d"
    (staged / "_internal").mkdir(parents=True, exist_ok=True)
    (staged / "_internal" / "marker").write_text(tag, encoding="utf-8")
    gaia = staged / "gaia"
    gaia.write_text(
        f'#!/bin/sh\n# core {tag}\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
        encoding="utf-8",
    )
    gaia.chmod(0o755)
    (staged / "gaia.exe").write_text(f"core {tag}", encoding="utf-8")
    with tarfile.open(path, "w:gz") as tar:
        for item in ("gaia", "gaia.exe", "_internal"):
            tar.add(staged / item, arcname=item)
    shutil.rmtree(staged)


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
        raw_sums=None,
        core_digest_overrides=None,
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
        elif raw_sums is not None:
            sums.write_text(raw_sums(lines), encoding="utf-8")
        else:
            sums.write_text("".join(lines), encoding="utf-8")
        core_lines = []
        for key in CORE_PLATFORMS:
            name = f"gaia-core-{key}.tar.gz"
            _core_tarball(tag_dir / name, tag)
            digest = (core_digest_overrides or {}).get(
                key, hashlib.sha256((tag_dir / name).read_bytes()).hexdigest()
            )
            core_lines.append(f"{digest}  {name}\n")
        (tag_dir / CORE_SUMS_FILE).write_text("".join(core_lines), encoding="utf-8")

    def rc_agent(tag=RC_TAG, digest_overrides=None, platforms=None):
        """Stage a candidate's frozen agent + gaia-agent-release.json, the
        shape util/flagship_rc_assets.py attaches to a pre-release."""
        tag_dir = root / "releases" / "download" / tag
        tag_dir.mkdir(parents=True, exist_ok=True)
        entries = {}
        for key in platforms or RC_AGENT_PLATFORMS:
            filename = f"gaia-agent-{key}" + (".exe" if key.startswith("win") else "")
            blob = f"#!/bin/sh\necho rc-agent {tag} {key}\n".encode()
            (tag_dir / filename).write_bytes(blob)
            entries[key] = {
                "filename": filename,
                "sha256": (digest_overrides or {}).get(
                    key, hashlib.sha256(blob).hexdigest()
                ),
                "size": len(blob),
            }
        (tag_dir / "gaia-agent-release.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "agent": "gaia",
                    "agentVersion": RC_AGENT_VERSION,
                    "releaseTag": tag,
                    "platforms": entries,
                }
            ),
            encoding="utf-8",
        )

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
    release(tag=RC_TAG)
    rc_agent()
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
    fake.root = root
    fake.release = release
    fake.publish = publish
    fake.rc_agent = rc_agent
    fake.state = state
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
    assert "no single valid SHA-256 for" in result.stdout
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


# ── functional: install_core puts the release's frozen gaia in place ───────


def _run_install_core(tmp_path, base_url, extra=""):
    result, home = _run_sh_function(
        tmp_path,
        f"DOWNLOAD_CMD=curl; resolve_release_tag && install_core && link_gaia_commands{extra}",
        _channel_env(base_url),
    )
    return result, home / ".gaia"


@posix_only
@needs_curl
def test_install_core_installs_the_releases_gaia_and_links_it(tmp_path, fake_hub):
    result, gaia_home = _run_install_core(tmp_path, fake_hub.base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"# core {RELEASE_TAG}" in (gaia_home / "core" / "gaia").read_text(
        encoding="utf-8"
    )
    assert (gaia_home / "core" / "_internal" / "marker").is_file()
    for name in ("gaia", "gaia-cli", "gaia-mcp"):
        assert os.readlink(gaia_home / "bin" / name) == str(gaia_home / "core" / "gaia")
    assert not (gaia_home / "venv").exists()


@posix_only
@needs_curl
def test_install_core_installs_nothing_on_a_checksum_mismatch(tmp_path, fake_hub):
    fake_hub.release(core_digest_overrides={k: "d" * 64 for k in CORE_PLATFORMS})

    result, gaia_home = _run_install_core(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stdout + result.stderr
    assert not (gaia_home / "core").exists()


@posix_only
@needs_curl
def test_a_release_without_gaia_is_refused(tmp_path, fake_hub):
    """A release from before GAIA shipped as a binary has no checksums for it."""
    (fake_hub.root / "releases" / "download" / RELEASE_TAG / CORE_SUMS_FILE).unlink()

    result, gaia_home = _run_install_core(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "Could not fetch the GAIA checksums" in result.stdout
    assert not (gaia_home / "core").exists()


# ── functional: GAIA_CLI_ONLY, for a package that already owns the binaries ──

_MAIN_STEPS = (
    "detect_environment",
    "check_download_tool",
    "resolve_pin",
    "resolve_release_tag",
    "install_core",
    "link_gaia_commands",
    "add_to_path",
    "install_tui",
    "install_flagship_agent",
)


def _run_main_with_stubbed_steps(tmp_path, cli_only):
    stubs = "; ".join(f'{step}() {{ echo "STEP {step}"; }}' for step in _MAIN_STEPS)
    return _run_sh_function(
        tmp_path, f"{stubs}; main", {"GAIA_CLI_ONLY": cli_only}, shell="dash"
    )[0]


def _steps_run(result):
    return [
        line.split(" ", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith("STEP ")
    ]


@posix_only
@pytest.mark.skipif(shutil.which("dash") is None, reason="needs dash")
def test_cli_only_installs_the_cli_and_leaves_the_packaged_binaries(tmp_path):
    result = _run_main_with_stubbed_steps(tmp_path, "1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _steps_run(result) == list(_MAIN_STEPS[:-2])


@posix_only
@pytest.mark.skipif(shutil.which("dash") is None, reason="needs dash")
def test_without_cli_only_main_still_installs_everything(tmp_path):
    result = _run_main_with_stubbed_steps(tmp_path, "")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _steps_run(result) == list(_MAIN_STEPS)


@posix_only
@pytest.mark.skipif(shutil.which("dash") is None, reason="needs dash")
@pytest.mark.parametrize("value", ["true", "yes", "0", "2"])
def test_a_bad_cli_only_value_is_refused_before_anything_installs(tmp_path, value):
    result = _run_main_with_stubbed_steps(tmp_path, value)

    assert result.returncode != 0
    assert "GAIA_CLI_ONLY" in result.stdout + result.stderr
    assert _steps_run(result) == []


# ── functional: the flagship sidecar is optional, but never unverified ──────
#
# The whole point of the sidecar step is that it can fail without taking a
# complete install down with it — except on a digest mismatch. Asserting that
# over the script's source text cannot tell the two apart, so these run it.


def _run_bootstrap(tmp_path, base_url):
    """Run install_tui then install_flagship_agent, as main() does."""
    result, home = _run_sh_function(
        tmp_path,
        "DOWNLOAD_CMD=curl; resolve_release_tag; install_core; install_tui; "
        'install_flagship_agent; echo "FLAGSHIP_INSTALLED=${FLAGSHIP_INSTALLED:-0}"',
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
    assert "no single valid SHA-256 for gaia-windows-amd64.exe" in result.stdout
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


# ── #7187: the daemon starts the agent the installer placed ────────────────
#
# A fresh home laid out by the real install functions, then the daemon's own
# user-mode resolve and `gaia init --check`'s agent test, run as the frozen
# gaia in that bin dir with no source-checkout lock -- an installed GAIA.


def _as_installed_gaia(monkeypatch, tmp_path, gaia_dir: Path, exe: str) -> None:
    from gaia.daemon.sidecars import platform as plat

    monkeypatch.setenv("GAIA_HOME", str(gaia_dir))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.setattr(
        plat, "default_lock_path", lambda agent_id="email": tmp_path / "no.lock"
    )
    monkeypatch.setattr("gaia.frozen.is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(gaia_dir / "bin" / exe))


def _daemon_spawn_argv():
    from gaia.daemon.sidecars.manager import AgentSidecarManager
    from gaia.daemon.sidecars.spec import builtin_specs

    manager = AgentSidecarManager(builtin_specs()["gaia"], mode="user")
    argv, _ = manager.build_spawn_command(port=9321)
    return argv, manager.installed_binary_version


def _check_counts_agent():
    from gaia.installer.init_command import _agent_ready_for_check

    return _agent_ready_for_check("gaia")


# install.ps1 places gaia-agent.exe; only a Windows daemon looks for that name.
windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="install.ps1 only installs on Windows"
)


def _ps1_install_agent(tmp_path, base_url):
    """install.ps1's Install-FlagshipAgent, writing into the profile's ~/.gaia/bin."""
    result, _ = _run_ps1(
        tmp_path,
        base_url,
        '$GAIA_BIN = Join-Path $env:USERPROFILE ".gaia\\bin"\n'
        '$ok = Install-FlagshipAgent; Write-Host "FLAGSHIP=$ok"',
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FLAGSHIP=True" in result.stdout
    return tmp_path / "profile" / ".gaia"


@needs_powershell
@windows_only
def test_daemon_starts_the_agent_install_ps1_placed(tmp_path, fake_hub, monkeypatch):
    gaia_dir = _ps1_install_agent(tmp_path, fake_hub.base)
    agent = gaia_dir / "bin" / "gaia-agent.exe"
    _as_installed_gaia(monkeypatch, tmp_path, gaia_dir, "gaia.exe")

    argv, version = _daemon_spawn_argv()

    assert Path(argv[0]) == agent
    assert version == HUB_VERSION
    assert _check_counts_agent() is True


@needs_powershell
@windows_only
def test_daemon_refuses_an_agent_replaced_after_install_ps1(
    tmp_path, fake_hub, monkeypatch
):
    from gaia.daemon.sidecars.errors import SidecarSpawnError

    gaia_dir = _ps1_install_agent(tmp_path, fake_hub.base)
    (gaia_dir / "bin" / "gaia-agent.exe").write_bytes(b"MZ swapped in later")
    _as_installed_gaia(monkeypatch, tmp_path, gaia_dir, "gaia.exe")

    with pytest.raises(SidecarSpawnError) as exc_info:
        _daemon_spawn_argv()
    msg = str(exc_info.value)
    assert "does not match the SHA-256 its installer recorded" in msg
    assert "gaia init" in msg
    assert _check_counts_agent() is False


@needs_powershell
@windows_only
def test_an_rc_pin_records_the_candidates_agent_version(
    tmp_path, fake_hub, monkeypatch
):
    result, _ = _run_ps1(
        tmp_path,
        fake_hub.base,
        '$GAIA_BIN = Join-Path $env:USERPROFILE ".gaia\\bin"\n'
        "$pin = Resolve-Pin; $ok = Install-FlagshipAgent -Pin $pin; "
        'Write-Host "FLAGSHIP=$ok"',
        tag=RC_TAG,
    )
    assert "FLAGSHIP=True" in result.stdout, result.stdout + result.stderr
    gaia_dir = tmp_path / "profile" / ".gaia"
    _as_installed_gaia(monkeypatch, tmp_path, gaia_dir, "gaia.exe")

    _, version = _daemon_spawn_argv()

    assert version == RC_AGENT_VERSION


@posix_only
@needs_curl
def test_daemon_starts_the_agent_install_sh_placed(tmp_path, fake_hub, monkeypatch):
    result, _, agent = _run_bootstrap(tmp_path, fake_hub.base)
    assert "FLAGSHIP_INSTALLED=1" in result.stdout, result.stdout + result.stderr
    gaia_dir = tmp_path / "home" / ".gaia"
    _as_installed_gaia(monkeypatch, tmp_path, gaia_dir, "gaia")

    argv, version = _daemon_spawn_argv()

    assert Path(argv[0]) == agent
    assert version == HUB_VERSION
    assert _check_counts_agent() is True

    agent.write_bytes(b"#!/bin/sh\necho swapped\n")
    from gaia.daemon.sidecars.errors import SidecarSpawnError

    with pytest.raises(SidecarSpawnError, match="does not match"):
        _daemon_spawn_argv()
    assert _check_counts_agent() is False


# ── a pinned release: core, terminal and agent from the same release ───────


PIN_CASES = [
    ("", ""),
    ("v0.25.0", "final"),
    ("v0.25.1-rc2", "rc"),
    ("v1.10.20-rc13", "rc"),
]
BAD_PINS = [
    "0.25.0",
    "v0.25",
    "v0.25.1-rc0",
    "v0.25.1-beta1",
    "v0.25.0;id",
    "v0.25.0\nv0.25.0",
    "latest",
]


def _sh_with_tag(tmp_path, base_url, snippet, tag):
    env = _channel_env(base_url)
    env["GAIA_RELEASE_TAG"] = tag
    return _run_sh_function(tmp_path, snippet, env)


@posix_only
@pytest.mark.parametrize("tag,kind", PIN_CASES)
def test_sh_pins_the_install_to_the_tag(tmp_path, tag, kind):
    result, _ = _sh_with_tag(
        tmp_path, "http://127.0.0.1:1", 'resolve_pin; echo "KIND=$PIN_KIND"', tag
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"KIND={kind}" in result.stdout


@posix_only
@pytest.mark.parametrize("tag", BAD_PINS)
def test_sh_refuses_a_tag_it_cannot_pin(tmp_path, tag):
    result, home = _sh_with_tag(
        tmp_path, "http://127.0.0.1:1", "resolve_pin; echo REACHED", tag
    )
    assert result.returncode != 0
    assert "is not a GAIA release tag" in result.stdout
    assert "REACHED" not in result.stdout
    assert not (home / ".gaia").exists()


def _rc_bootstrap_sh(tmp_path, base_url):
    result, home = _sh_with_tag(
        tmp_path,
        base_url,
        "DOWNLOAD_CMD=curl; resolve_pin; resolve_release_tag; install_core; "
        'install_tui; install_flagship_agent; echo "FLAGSHIP_INSTALLED=${FLAGSHIP_INSTALLED:-0}"',
        RC_TAG,
    )
    bin_dir = home / ".gaia" / "bin"
    return result, bin_dir / "gaia-tui", bin_dir / "gaia-agent"


@posix_only
@needs_curl
def test_an_rc_pin_installs_the_candidates_own_agent(tmp_path, fake_hub):
    """An RC core installs the flagship frozen on its pre-release, never the
    hub's, so the bootstrap must hand it the same one."""
    result, tui, agent = _rc_bootstrap_sh(tmp_path, fake_hub.base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert RC_TAG in tui.read_text(encoding="utf-8")
    assert f"rc-agent {RC_TAG}" in agent.read_text(encoding="utf-8")
    assert f"GAIA agent {RC_AGENT_VERSION} installed" in result.stdout
    assert "FLAGSHIP_INSTALLED=1" in result.stdout


@posix_only
@needs_curl
def test_an_rc_pin_never_installs_a_tampered_agent(tmp_path, fake_hub):
    fake_hub.rc_agent(digest_overrides={k: "e" * 64 for k in RC_AGENT_PLATFORMS})

    result, tui, agent = _rc_bootstrap_sh(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stdout + result.stderr
    assert not agent.exists()


@posix_only
@needs_curl
def test_an_rc_without_this_platforms_agent_still_installs_the_hub(tmp_path, fake_hub):
    fake_hub.rc_agent(platforms=("win32-x64",))

    result, tui, agent = _rc_bootstrap_sh(tmp_path, fake_hub.base)

    assert result.returncode == 0, result.stdout + result.stderr
    assert tui.is_file()
    assert not agent.exists()
    assert "carries no agent for" in result.stdout + result.stderr
    assert "FLAGSHIP_INSTALLED=0" in result.stdout


@posix_only
@needs_curl
def test_a_duplicate_checksum_entry_is_refused(tmp_path, fake_hub):
    fake_hub.release(raw_sums=lambda lines: "".join(lines + lines))

    result, installed = _run_install_tui(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "no single valid SHA-256" in result.stdout
    assert not installed.exists()


@posix_only
@needs_curl
def test_an_uppercase_checksum_is_refused(tmp_path, fake_hub):
    """sha256sum writes lowercase; both scripts accept exactly that."""
    fake_hub.release(
        raw_sums=lambda lines: "".join(line[:64].upper() + line[64:] for line in lines)
    )

    result, installed = _run_install_tui(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "no single valid SHA-256" in result.stdout
    assert not installed.exists()


@posix_only
@needs_curl
def test_latest_redirecting_to_something_other_than_a_tag_is_refused(
    tmp_path, fake_hub
):
    fake_hub.state["latest_location"] = f"{fake_hub.base}/releases"

    result, installed = _run_install_tui(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "Could not find the GAIA release" in result.stdout
    assert not installed.exists()


# ── PowerShell: the same pin and the same refusals ─────────────────────────


@needs_powershell
@pytest.mark.parametrize("tag,kind", PIN_CASES)
def test_ps1_pins_the_install_to_the_tag(tmp_path, tag, kind):
    result, _ = _run_ps1(
        tmp_path,
        "http://127.0.0.1:1",
        '$p = Resolve-Pin; Write-Host "KIND=$($p.Kind)"',
        tag=tag,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"KIND={kind}" in result.stdout


@needs_powershell
@pytest.mark.parametrize("tag", [t for t in BAD_PINS if "\n" not in t])
def test_ps1_refuses_a_tag_it_cannot_pin(tmp_path, tag):
    result, bin_dir = _run_ps1(
        tmp_path, "http://127.0.0.1:1", "$p = Resolve-Pin; Write-Host REACHED", tag=tag
    )
    assert result.returncode != 0
    assert "is not a GAIA release tag" in result.stdout
    assert "REACHED" not in result.stdout


@needs_powershell
def test_ps1_an_rc_pin_installs_the_candidates_own_agent(tmp_path, fake_hub):
    result, bin_dir = _run_ps1(
        tmp_path,
        fake_hub.base,
        "$p = Resolve-Pin; Install-Tui; $ok = Install-FlagshipAgent -Pin $p; "
        'Write-Host "FLAGSHIP=$ok"',
        tag=RC_TAG,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert RC_TAG in (bin_dir / "gaia-tui.exe").read_text(encoding="utf-8")
    agent = (bin_dir / "gaia-agent.exe").read_text(encoding="utf-8")
    assert f"rc-agent {RC_TAG} win32-x64" in agent
    assert "FLAGSHIP=True" in result.stdout


@needs_powershell
def test_ps1_an_rc_pin_never_installs_a_tampered_agent(tmp_path, fake_hub):
    fake_hub.rc_agent(digest_overrides={"win32-x64": "e" * 64})

    result, bin_dir = _run_ps1(
        tmp_path,
        fake_hub.base,
        "$p = Resolve-Pin; Install-FlagshipAgent -Pin $p",
        tag=RC_TAG,
    )
    assert result.returncode != 0
    assert "Checksum mismatch" in result.stdout
    assert not (bin_dir / "gaia-agent.exe").exists()


@needs_powershell
def test_ps1_fails_when_github_is_unreachable(tmp_path):
    result, bin_dir = _run_ps1(tmp_path, "http://127.0.0.1:1", "Install-Tui")

    assert result.returncode != 0
    assert "Could not reach the GAIA releases" in result.stdout
    assert not bin_dir.exists()


@needs_powershell
def test_ps1_refuses_a_release_without_its_checksum_file(tmp_path, fake_hub):
    fake_hub.release(omit_sums_file=True)

    result, bin_dir = _run_ps1(tmp_path, fake_hub.base, "Install-Tui")

    assert result.returncode != 0
    assert f"Could not fetch {SUMS_FILE} for" in result.stdout
    assert "still be uploading" in result.stdout
    assert not bin_dir.exists()


@needs_powershell
def test_ps1_refuses_latest_redirecting_to_something_other_than_a_tag(
    tmp_path, fake_hub
):
    fake_hub.state["latest_location"] = f"{fake_hub.base}/releases"

    result, bin_dir = _run_ps1(tmp_path, fake_hub.base, "Install-Tui")

    assert result.returncode != 0
    assert "Could not find the latest GAIA release" in result.stdout
    assert not bin_dir.exists()


@needs_powershell
@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda lines: "".join(lines + lines), id="duplicate"),
        pytest.param(
            lambda lines: "".join(line[:64].upper() + line[64:] for line in lines),
            id="uppercase",
        ),
    ],
)
def test_ps1_refuses_what_install_sh_refuses(tmp_path, fake_hub, mangle):
    fake_hub.release(raw_sums=mangle)

    result, bin_dir = _run_ps1(tmp_path, fake_hub.base, "Install-Tui")

    assert result.returncode != 0
    assert "no single valid SHA-256" in result.stdout
    assert not (bin_dir / "gaia-tui.exe").exists()


# ── functional: add_to_path ────────────────────────────────────────────────


def _rc_files(home):
    return sorted(p.name for p in home.iterdir() if p.name.startswith("."))


# What each installer generation appended; {h} is the home directory.
_LEGACY_BLOCK = '\n# Added by GAIA installer\nexport PATH="$PATH:{h}/.gaia/venv/bin:{h}/.gaia/bin"\n'
_VENV_ONLY_BLOCK = (
    '\n# Added by GAIA installer\nexport PATH="$PATH:{h}/.gaia/venv/bin"\n'
)
_CURRENT_BLOCK = '\n# Added by GAIA installer\nexport PATH="$PATH:{h}/.gaia/bin"\n'
_USER_TEXT = (
    "# my settings\n"
    "alias ll='ls -l'\n"
    'export PATH="$HOME/tools:$PATH"\n'
    "# a note about /opt/x/.gaia/venv/bin stays\n"
)


def _home_with(tmp_path, files):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    for name, text in files.items():
        (home / name).write_text(text.format(h=home), encoding="utf-8")
    return home


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_writes_the_zsh_rc_and_is_idempotent(tmp_path):
    result, home = _run_sh_function(
        tmp_path, "OS_NAME=Darwin; add_to_path", {"SHELL": "/bin/zsh"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _rc_files(home) == [".zshrc"]

    rc = home / ".zshrc"
    first = rc.read_text(encoding="utf-8")
    assert first == _CURRENT_BLOCK.format(h=home)

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
def test_add_to_path_rewrites_the_legacy_venv_line_in_place(tmp_path):
    """#6603: the old line put the venv's python3 on PATH. It is rewritten
    where it stands; everything around it is kept byte for byte."""
    home = _home_with(tmp_path, {".bashrc": _USER_TEXT + _LEGACY_BLOCK + "set -o vi\n"})
    expected = (_USER_TEXT + _CURRENT_BLOCK + "set -o vi\n").format(h=home)

    for _ in range(2):
        result, _ = _run_sh_function(
            tmp_path, "OS_NAME=Linux; add_to_path", {"SHELL": "/bin/bash"}
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (home / ".bashrc").read_text(encoding="utf-8") == expected


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_rewrites_a_pre_0_23_venv_only_export(tmp_path):
    """The oldest installs wrote the venv alone, without the terminal hub's
    directory."""
    home = _home_with(tmp_path, {".zshrc": _USER_TEXT + _VENV_ONLY_BLOCK})

    result, _ = _run_sh_function(
        tmp_path, "OS_NAME=Linux; add_to_path", {"SHELL": "/bin/zsh"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".zshrc").read_text(encoding="utf-8") == (
        _USER_TEXT + _CURRENT_BLOCK
    ).format(h=home)


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_collapses_stacked_installer_blocks(tmp_path):
    """A pre-0.23 install followed by a later one left two blocks; both
    rewrite to the same line, so only the first is kept."""
    home = _home_with(
        tmp_path, {".zshrc": _USER_TEXT + _VENV_ONLY_BLOCK + _LEGACY_BLOCK}
    )

    result, _ = _run_sh_function(
        tmp_path, "OS_NAME=Linux; add_to_path", {"SHELL": "/bin/zsh"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".zshrc").read_text(encoding="utf-8") == (
        _USER_TEXT + _CURRENT_BLOCK
    ).format(h=home)


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_migrates_rc_files_another_shell_wrote(tmp_path):
    """A user who installed under sh and now runs zsh still has the old line
    in ~/.profile; it is rewritten though zsh never appends there."""
    home = _home_with(tmp_path, {".profile": _USER_TEXT + _LEGACY_BLOCK})

    result, _ = _run_sh_function(
        tmp_path, "OS_NAME=Linux; add_to_path", {"SHELL": "/bin/zsh"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".profile").read_text(encoding="utf-8") == (
        _USER_TEXT + _CURRENT_BLOCK
    ).format(h=home)
    assert (home / ".zshrc").read_text(encoding="utf-8") == _CURRENT_BLOCK.format(
        h=home
    )


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_rewrites_through_a_symlinked_rc_file(tmp_path):
    """Dotfile managers symlink ~/.bashrc; the edit lands in the target and
    keeps the link and the file's mode."""
    home = _home_with(tmp_path, {})
    target = tmp_path / "dotfiles-bashrc"
    target.write_text((_USER_TEXT + _LEGACY_BLOCK).format(h=home), encoding="utf-8")
    target.chmod(0o600)
    (home / ".bashrc").symlink_to(target)

    result, _ = _run_sh_function(
        tmp_path, "OS_NAME=Linux; add_to_path", {"SHELL": "/bin/bash"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".bashrc").is_symlink()
    assert target.read_text(encoding="utf-8") == (_USER_TEXT + _CURRENT_BLOCK).format(
        h=home
    )
    assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_leaves_a_hand_written_line_alone(tmp_path):
    """Only the exact lines the installer wrote are rewritten."""
    hand = 'export PATH="{h}/.gaia/venv/bin:{h}/.gaia/bin:$PATH"\n'
    home = _home_with(tmp_path, {".zshrc": _USER_TEXT + hand})

    result, _ = _run_sh_function(
        tmp_path, "OS_NAME=Linux; add_to_path", {"SHELL": "/bin/zsh"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".zshrc").read_text(encoding="utf-8") == (_USER_TEXT + hand).format(
        h=home
    )


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_add_to_path_does_not_write_a_file_an_unknown_shell_ignores(tmp_path):
    """fish never reads ~/.profile, and `source ~/.profile` errors in it."""
    result, home = _run_sh_function(
        tmp_path, "OS_NAME=Linux; add_to_path", {"SHELL": "/usr/bin/fish"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _rc_files(home) == []
    assert "Add this directory to your PATH by hand" in result.stdout
    assert f"fish_add_path --append {home}/.gaia/bin" in result.stdout
    # The venv is named only to warn against adding it.
    venv_lines = [ln for ln in result.stdout.splitlines() if ".gaia/venv/bin" in ln]
    assert venv_lines and all("Do not add" in ln for ln in venv_lines)


# ── functional: link_gaia_commands and the PATH a new shell gets ───────────


def _fake_core(home):
    core = home / ".gaia" / "core"
    core.mkdir(parents=True)
    gaia = core / "gaia"
    gaia.write_text("#!/bin/sh\necho core-gaia\n", encoding="utf-8")
    gaia.chmod(0o755)
    return gaia


def _fake_venv(home, names):
    venv_bin = home / ".gaia" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    for name in names:
        exe = venv_bin / name
        exe.write_text(f"#!/bin/sh\necho venv-{name}\n", encoding="utf-8")
        exe.chmod(0o755)
    return venv_bin


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_link_gaia_commands_links_only_gaias_commands(tmp_path):
    home = _home_with(tmp_path, {})
    gaia = _fake_core(home)
    gaia_bin = home / ".gaia" / "bin"
    gaia_bin.mkdir()
    (gaia_bin / "gaia-tui").write_text("tui", encoding="utf-8")
    # A stale link from an earlier run is replaced.
    (gaia_bin / "gaia").symlink_to(tmp_path / "elsewhere")

    for _ in range(2):
        result, _ = _run_sh_function(tmp_path, "link_gaia_commands")
        assert result.returncode == 0, result.stdout + result.stderr

    assert sorted(p.name for p in gaia_bin.iterdir()) == [
        "gaia",
        "gaia-cli",
        "gaia-mcp",
        "gaia-tui",
    ]
    for name in ("gaia", "gaia-cli", "gaia-mcp"):
        assert os.readlink(gaia_bin / name) == str(gaia)


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_link_gaia_commands_fails_when_gaia_is_missing(tmp_path):
    _home_with(tmp_path, {})

    result, _ = _run_sh_function(tmp_path, "link_gaia_commands")
    assert result.returncode != 0
    assert "core/gaia is missing" in result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
def test_link_gaia_commands_never_replaces_a_real_file(tmp_path):
    home = _home_with(tmp_path, {})
    _fake_core(home)
    gaia_bin = home / ".gaia" / "bin"
    gaia_bin.mkdir()
    (gaia_bin / "gaia").write_text("mine", encoding="utf-8")

    result, _ = _run_sh_function(tmp_path, "link_gaia_commands")
    assert result.returncode != 0
    assert "not a link GAIA made" in result.stdout
    assert (gaia_bin / "gaia").read_text(encoding="utf-8") == "mine"


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")
@pytest.mark.parametrize("rc_text", ["", _LEGACY_BLOCK], ids=["fresh", "upgrade"])
def test_a_new_shell_finds_gaia_but_not_the_venvs_python(tmp_path, rc_text):
    """Source the rc file as a new shell does: `gaia` runs through
    ~/.gaia/bin, and python3 and pip are not GAIA's."""
    home = _home_with(tmp_path, {".bashrc": _USER_TEXT + rc_text})
    _fake_core(home)
    # An earlier install's venv: its python3 and pip must not reach PATH.
    _fake_venv(home, ("gaia", "python3", "pip"))

    result, _ = _run_sh_function(
        tmp_path,
        "OS_NAME=Linux; link_gaia_commands && add_to_path",
        {"SHELL": "/bin/bash"},
    )
    assert result.returncode == 0, result.stdout + result.stderr

    probe = subprocess.run(
        [
            "sh",
            "-c",
            '. "$HOME/.bashrc"; gaia; '
            "for c in gaia gaia-cli gaia-mcp python3 pip; do "
            'command -v "$c" || echo "none:$c"; done',
        ],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    lines = probe.stdout.splitlines()
    gaia_bin = home / ".gaia" / "bin"
    assert lines[:4] == [
        "core-gaia",
        f"{gaia_bin}/gaia",
        f"{gaia_bin}/gaia-cli",
        f"{gaia_bin}/gaia-mcp",
    ]
    assert len(lines) == 6
    assert not any(".gaia" in found for found in lines[4:]), lines


# ── functional: install.ps1's Add-ToPath against a scratch registry key ────
#
# Add-ToPath edits the key named by $USER_ENV_KEY, so pointing that at a
# throwaway HKCU subkey exercises the real registry code without touching the
# runner's own PATH.

needs_windows_registry = pytest.mark.skipif(
    sys.platform != "win32" or _PS_EXE is None, reason="needs the Windows registry"
)


@pytest.fixture
def scratch_env_key(tmp_path):
    import uuid
    import winreg

    subkey = rf"Software\GAIA-install-test-{uuid.uuid4().hex}"
    real = _real_user_path()
    yield subkey
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except FileNotFoundError:
        pass
    # Catches the scratch-key hook going stale and writing the real PATH.
    after = _real_user_path()
    if after != real and after is not None and str(tmp_path) in after[0]:
        if real is None:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, "Path")
        else:
            _seed_user_path("Environment", *real)
        pytest.fail("Add-ToPath changed the runner's real user PATH; restored it")


def _real_user_path():
    try:
        return _read_user_path("Environment")
    except FileNotFoundError:
        return None


def _seed_user_path(subkey, value, kind):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as key:
        winreg.SetValueEx(key, "Path", 0, kind, value)


def _read_user_path(subkey):
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
        return winreg.QueryValueEx(key, "Path")


def _run_add_to_path(tmp_path, subkey, times=1, broadcast=""):
    # Stubbed so a test run never broadcasts WM_SETTINGCHANGE to the desktop.
    snippet = (
        f"$USER_ENV_KEY = '{subkey}'\n"
        '$GAIA_BIN = "$GAIA_HOME\\bin"\n'
        f"function Send-EnvironmentChange {{ {broadcast} }}\n" + "Add-ToPath\n" * times
    )
    result, _ = _run_ps1(tmp_path, "http://127.0.0.1:1", snippet)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Failed to add GAIA to PATH" not in result.stdout, result.stdout
    return result


@needs_windows_registry
def test_ps1_add_to_path_keeps_unexpanded_entries(tmp_path, scratch_env_key):
    """Reading PATH expanded and writing it back as REG_SZ froze %VARS%."""
    import winreg

    _seed_user_path(
        scratch_env_key,
        r"%USERPROFILE%\tools;%LOCALAPPDATA%\other",
        winreg.REG_EXPAND_SZ,
    )

    _run_add_to_path(tmp_path, scratch_env_key)

    value, kind = _read_user_path(scratch_env_key)
    assert kind == winreg.REG_EXPAND_SZ
    entries = value.split(";")
    assert entries[:2] == [r"%USERPROFILE%\tools", r"%LOCALAPPDATA%\other"]
    assert entries[2:] == [str(tmp_path / "profile" / ".gaia" / "bin")]


@needs_windows_registry
def test_ps1_add_to_path_matches_whole_entries(tmp_path, scratch_env_key):
    """A substring check counted .gaia/bin2 as .gaia/bin."""
    import winreg

    bin2 = str(tmp_path / "profile" / ".gaia" / "bin2")
    _seed_user_path(scratch_env_key, bin2, winreg.REG_SZ)

    _run_add_to_path(tmp_path, scratch_env_key)

    value, kind = _read_user_path(scratch_env_key)
    assert kind == winreg.REG_EXPAND_SZ
    assert str(tmp_path / "profile" / ".gaia" / "bin") in value.split(";")
    assert value.split(";")[0] == bin2


@needs_windows_registry
def test_ps1_add_to_path_recognises_an_unexpanded_gaia_entry(tmp_path, scratch_env_key):
    """An entry written with %USERPROFILE% names the same directory."""
    import winreg

    seeded = r"%USERPROFILE%\tools;%USERPROFILE%\.gaia\bin\;"
    _seed_user_path(scratch_env_key, seeded, winreg.REG_EXPAND_SZ)

    result = _run_add_to_path(tmp_path, scratch_env_key, times=2)

    assert _read_user_path(scratch_env_key) == (seeded, winreg.REG_EXPAND_SZ)
    assert "GAIA is already in PATH" in result.stdout


@needs_windows_registry
def test_ps1_add_to_path_is_idempotent(tmp_path, scratch_env_key):
    import winreg

    _seed_user_path(scratch_env_key, r"%USERPROFILE%\tools", winreg.REG_EXPAND_SZ)

    _run_add_to_path(tmp_path, scratch_env_key, times=2)

    value, _ = _read_user_path(scratch_env_key)
    assert len(value.split(";")) == 2


@needs_windows_registry
def test_ps1_add_to_path_a_failed_broadcast_still_reports_the_saved_path(
    tmp_path, scratch_env_key
):
    """A blocked Add-Type used to print "add it manually" over a saved PATH."""
    import winreg

    _seed_user_path(scratch_env_key, r"%USERPROFILE%\tools", winreg.REG_EXPAND_SZ)

    result = _run_add_to_path(
        tmp_path, scratch_env_key, broadcast="throw 'Add-Type blocked'"
    )

    assert "Added GAIA to PATH" in result.stdout
    assert "PATH saved, but Windows could not be notified" in result.stdout
    assert "Add-Type blocked" in result.stdout
    value, _ = _read_user_path(scratch_env_key)
    assert len(value.split(";")) == 2


@needs_windows_registry
def test_ps1_add_to_path_moves_an_earlier_install_off_the_venv(
    tmp_path, scratch_env_key
):
    """An install from before this fix had the venv's Scripts on PATH; a
    re-run takes it off, keeps every other entry as written, and tells
    Windows so new terminals pick it up."""
    import winreg

    seeded = (
        r"%USERPROFILE%\tools;%USERPROFILE%\.gaia\venv\Scripts;"
        r"%USERPROFILE%\.gaia\bin;D:\py\venv\Scripts"
    )
    _seed_user_path(scratch_env_key, seeded, winreg.REG_EXPAND_SZ)

    first = _run_add_to_path(
        tmp_path, scratch_env_key, broadcast="Write-Host 'BROADCAST'"
    )
    assert "so your own python and pip come first again" in first.stdout
    assert "BROADCAST" in first.stdout
    assert _read_user_path(scratch_env_key) == (
        r"%USERPROFILE%\tools;%USERPROFILE%\.gaia\bin;D:\py\venv\Scripts",
        winreg.REG_EXPAND_SZ,
    )

    second = _run_add_to_path(
        tmp_path, scratch_env_key, broadcast="Write-Host 'BROADCAST'"
    )
    assert "GAIA is already in PATH" in second.stdout
    assert "BROADCAST" not in second.stdout


# ── functional: Get-UpdatedPath on plain strings, no registry ──────────────


def _updated_paths(tmp_path, currents):
    """Run install.ps1's Get-UpdatedPath on each string; return its results."""
    cases = ", ".join("'" + c.replace("'", "''") + "'" for c in currents)
    snippet = (
        '$GAIA_BIN = "$GAIA_HOME\\bin"\n'
        f"$out = @(@({cases}) | ForEach-Object {{ Get-UpdatedPath -Current $_ }})\n"
        "Write-Output ('JSON:' + (ConvertTo-Json -InputObject $out -Compress))\n"
    )
    result, _ = _run_ps1(tmp_path, "http://127.0.0.1:1", snippet)
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("JSON:"))
    return json.loads(line[len("JSON:") :])


@needs_powershell
def test_get_updated_path_cases(tmp_path):
    gaia_bin = f"{tmp_path / 'profile'}\\.gaia\\bin"
    venv = r"%USERPROFILE%\.gaia\venv\Scripts"
    cases = [
        # (current, expected value, added, removed)
        (r"C:\tools;%LOCALAPPDATA%\x", rf"C:\tools;%LOCALAPPDATA%\x;{gaia_bin}", 1, 0),
        # 0.23 up to this fix: venv and bin.
        (
            rf"C:\tools;{venv};%USERPROFILE%\.gaia\bin",
            r"C:\tools;%USERPROFILE%\.gaia\bin",
            0,
            1,
        ),
        # Pre-0.23: the venv alone, quoted, with a trailing slash, twice.
        (rf'"{venv}\";C:\tools;{venv.upper()}', rf"C:\tools;{gaia_bin}", 1, 2),
        # Look-alikes and other venvs are the user's.
        (
            rf"{venv}2;D:\py\venv\Scripts",
            rf"{venv}2;D:\py\venv\Scripts;{gaia_bin}",
            1,
            0,
        ),
        # Already current; empty elements are dropped, never kept as ".".
        (rf"C:\tools;;{gaia_bin}\;", "C:\\tools;" + gaia_bin + "\\", 0, 0),
        ("", gaia_bin, 1, 0),
    ]

    results = _updated_paths(tmp_path, [c[0] for c in cases])

    for (current, value, added, removed), got in zip(cases, results):
        assert got == {
            "Value": value,
            "Added": bool(added),
            "Removed": removed,
        }, current


# ── functional: Install-Core puts the release's frozen gaia in $GAIA_BIN ────


def _install_core(tmp_path, base_url, extra=""):
    snippet = '$GAIA_BIN = "$GAIA_HOME\\bin"\nInstall-Core\n' + extra
    result, _ = _run_ps1(tmp_path, base_url, snippet)
    return result, tmp_path / "profile" / ".gaia" / "bin"


@needs_powershell
@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows' own tar.exe")
def test_install_core_puts_gaia_and_its_commands_in_gaia_bin(tmp_path, fake_hub):
    gaia_bin = tmp_path / "profile" / ".gaia" / "bin"
    gaia_bin.mkdir(parents=True)
    (gaia_bin / "gaia-tui.exe").write_bytes(b"tui")
    (gaia_bin / "_internal").mkdir()
    (gaia_bin / "_internal" / "stale").write_text("old", encoding="utf-8")

    result, _ = _install_core(tmp_path, fake_hub.base)
    assert result.returncode == 0, result.stdout + result.stderr

    assert sorted(p.name for p in gaia_bin.iterdir()) == [
        "_internal",
        "gaia-cli.exe",
        "gaia-mcp.exe",
        "gaia-tui.exe",
        "gaia.exe",
    ]
    for name in ("gaia", "gaia-cli", "gaia-mcp"):
        assert (gaia_bin / f"{name}.exe").read_text(encoding="utf-8") == (
            f"core {RELEASE_TAG}"
        )
    assert sorted(p.name for p in (gaia_bin / "_internal").iterdir()) == ["marker"]
    assert not (tmp_path / "profile" / ".gaia" / "venv").exists()


@needs_powershell
def test_install_core_installs_nothing_on_a_checksum_mismatch_ps1(tmp_path, fake_hub):
    fake_hub.release(core_digest_overrides={"win32-x64": "d" * 64})

    result, gaia_bin = _install_core(tmp_path, fake_hub.base)

    assert result.returncode != 0
    assert "Checksum mismatch" in result.stdout
    assert not (gaia_bin / "gaia.exe").exists()


@pytest.mark.skipif(
    sys.platform != "win32" or _PS_EXE is None, reason="resolves .exe on PATH"
)
@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "upgrade"])
def test_a_new_terminal_finds_gaia_but_not_the_venvs_python(tmp_path, legacy, fake_hub):
    """Resolve commands on the PATH install.ps1 computes, as a new terminal
    would: `gaia` is the frozen one in ~\\.gaia\\bin; python and pip are not GAIA's."""
    # An earlier install's venv, whose PATH entry an upgrade takes out.
    scripts = tmp_path / "profile" / ".gaia" / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    for name in ("gaia", "python", "pip"):
        (scripts / f"{name}.exe").write_bytes(f"launcher-{name}".encode())
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    current = f"{system};{scripts}" if legacy else system
    extra = (
        f"$env:PATH = (Get-UpdatedPath -Current '{current}').Value\n"
        "foreach ($c in 'gaia', 'gaia-cli', 'gaia-mcp', 'python', 'pip') {\n"
        '  $found = Get-Command "$c.exe" -CommandType Application '
        "-ErrorAction SilentlyContinue | Select-Object -First 1\n"
        "  Write-Output \"RESOLVED:$c=$(if ($found) { $found.Source } else { 'none' })\"\n"
        "}\n"
    )
    result, gaia_bin = _install_core(tmp_path, fake_hub.base, extra)
    assert result.returncode == 0, result.stdout + result.stderr

    resolved = dict(
        ln[len("RESOLVED:") :].split("=", 1)
        for ln in result.stdout.splitlines()
        if ln.startswith("RESOLVED:")
    )
    for name in ("gaia", "gaia-cli", "gaia-mcp"):
        assert resolved[name].lower() == str(gaia_bin / f"{name}.exe").lower()
    for name in ("python", "pip"):
        assert ".gaia" not in resolved[name], resolved
