# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one Windows setup must agree with the desktop app setup it embeds.

It finds, upgrades and refuses around the desktop app by electron-builder's
registry identity, which electron-builder DERIVES from ``appId`` -- so a renamed
appId would leave the setup checking a key nothing writes, and every upgrade
would install a second copy. Nothing at runtime would notice. These tests tie
the .nsi, its build script and its CI smoke test to electron-builder.yml, and
exercise the fetcher that picks which desktop app setup gets embedded.
"""

from __future__ import annotations

import io
import re
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER_TUI = REPO_ROOT / "installer" / "tui"
NSI = INSTALLER_TUI / "nsis" / "gaia-setup.nsi"
BUILD_SETUP = INSTALLER_TUI / "nsis" / "build-setup.sh"
SMOKE = INSTALLER_TUI / "nsis" / "smoke-test.ps1"
ELECTRON_BUILDER = (
    REPO_ROOT / "src" / "gaia" / "apps" / "webui" / "electron-builder.yml"
)
sys.path.insert(0, str(INSTALLER_TUI))

import fetch_agent_ui_setup as fetch  # noqa: E402

# electron-builder's UUIDv5 namespace (app-builder-lib NsisTarget).
ELECTRON_BUILDER_NS = uuid.UUID("50e065bc-3134-11e6-9bab-38c9862bdaf3")


def _nsi() -> str:
    return NSI.read_text(encoding="utf-8")


def _define(name: str) -> str:
    m = re.search(rf'^!define {name}\s+"?([^"\s;]+)"?', _nsi(), re.MULTILINE)
    assert m, f"gaia-setup.nsi no longer defines {name}"
    return m.group(1)


def _electron(key: str) -> str:
    m = re.search(
        rf"^\s*{key}:\s*(\S+)",
        ELECTRON_BUILDER.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert m, f"electron-builder.yml no longer sets {key}"
    return m.group(1)


# ── The desktop app's identity ─────────────────────────────────────────────


def test_ui_guid_is_what_electron_builder_derives_from_app_id():
    expected = str(uuid.uuid5(ELECTRON_BUILDER_NS, _electron("appId")))
    assert _define("UI_GUID") == expected
    # The smoke test checks the same keys on a real install.
    assert f"$uiGuid    = '{expected}'" in SMOKE.read_text(encoding="utf-8")


def test_ui_exe_is_electron_builders_executable_name():
    assert _define("UI_EXE") == f"{_electron('executableName')}.exe"


def test_build_script_accepts_exactly_electron_builders_windows_artifact_name():
    artifact = re.search(
        r"^win:.*?artifactName:\s*(\S+)",
        ELECTRON_BUILDER.read_text(encoding="utf-8"),
        re.MULTILINE | re.DOTALL,
    ).group(1)
    produced = artifact.replace("${version}", "1.2.3").replace("${arch}", "x64")
    produced = produced.replace("${ext}", "exe")
    assert produced == fetch.setup_name("1.2.3")
    pattern = re.search(r"=~ (\^gaia-agent-ui-\S+\$) \]\]", BUILD_SETUP.read_text())
    assert pattern, "build-setup.sh no longer validates the --agent-ui-setup name"
    assert re.match(pattern.group(1), produced)


def test_the_desktop_app_installs_per_user_like_electron_builder_yml_asks():
    assert _electron("perMachine") == "false"
    assert "/S /currentuser" in _nsi()


# ── The component choice ───────────────────────────────────────────────────


def test_neither_component_is_forced_on():
    # The pre-consolidation setup marked the terminal `SectionIn RO`.
    for sec in ("SecUI", "SecMain"):
        body = re.search(
            rf'^Section "[^"]+" {sec}\n(.*?)^SectionEnd', _nsi(), re.M | re.S
        )
        assert body, f"no visible section {sec}"
        assert "SectionIn RO" not in body.group(1)


def test_an_empty_choice_is_refused_on_the_page_and_on_the_command_line():
    nsi = _nsi()
    leave = re.search(r"^Function ComponentsLeave\n(.*?)^FunctionEnd", nsi, re.M | re.S)
    assert leave and "Abort" in leave.group(1)
    assert "EnableWindow $0 0" in nsi, "Next is not disabled with nothing chosen"
    assert "must name at least one component" in nsi


def test_terminal_profile_runs_only_with_the_terminal():
    sync = re.search(
        r"^Function SyncHiddenSections\n(.*?)^FunctionEnd", _nsi(), re.M | re.S
    )
    assert sync and "${SecTerminalProfile}" in sync.group(1)
    assert "${SecMain}" in sync.group(1)


def test_exit_codes_are_distinct_and_the_smoke_test_checks_them():
    codes = {
        name: int(value)
        for name, value in re.findall(r"^!define (EXIT_\w+)\s+(\d+)", _nsi(), re.M)
    }
    assert set(codes) == {"EXIT_RUNNING", "EXIT_USAGE", "EXIT_CONFLICT", "EXIT_PARTIAL"}
    assert len(set(codes.values())) == len(codes)
    assert 0 not in codes.values() and 1 not in codes.values()
    smoke = SMOKE.read_text(encoding="utf-8")
    for name in ("EXIT_RUNNING", "EXIT_USAGE", "EXIT_CONFLICT"):
        assert f"-ne {codes[name]})" in smoke, f"smoke-test.ps1 never expects {name}"


def test_every_section_is_ordered_ahead_of_the_code_that_names_it():
    # A section index only exists below its Section line; naming one earlier
    # expands to nothing, which -WX turns into a build failure -- but only on a
    # Windows runner. This catches it anywhere.
    nsi = _nsi()
    first_use = {
        sec: nsi.index(f"${{{sec}}}")
        for sec in ("SecUI", "SecMain", "SecTerminalProfile")
    }
    for sec, pos in first_use.items():
        assert nsi.index(f" {sec}\n") < pos, f"{sec} is used before it is defined"


def test_developer_build_is_refused_not_migrated():
    nsi = _nsi()
    assert _define("DEV_SETTINGS_KEY") == r"Software\AMD\GAIA"
    on_init = re.search(
        r"^Function \.onInit\n(.*?)^FunctionEnd", nsi, re.M | re.S
    ).group(1)
    assert "${DEV_SETTINGS_KEY}" in on_init and "EXIT_CONFLICT" in on_init
    # gaia.nsi's uninstaller re-execs from %TEMP% and edits PATH; running it
    # from here would race this setup's own PATH update. The only programs this
    # setup runs are the Lemonade MSI and the embedded desktop app setup.
    launched = re.findall(r"^\s*ExecWait '([^']+)'", nsi, re.M)
    assert launched == [
        'msiexec /i "$PLUGINSDIR\\${LEMONADE_MSI_NAME}" /qn /norestart',
        '"$PLUGINSDIR\\${UI_SETUP_NAME}" /S /currentuser',
    ]


# ── fetch_agent_ui_setup ───────────────────────────────────────────────────


def _setup_bytes(size: int = fetch.MIN_BYTES) -> bytes:
    return b"MZ" + b"\0" * (size - 2)


def test_verify_accepts_a_real_looking_setup(tmp_path):
    path = tmp_path / fetch.setup_name("0.25.0")
    path.write_bytes(_setup_bytes())
    assert fetch.verify(path, "0.25.0") == path


@pytest.mark.parametrize(
    "version,head,size,match",
    [
        ("0.24.1", b"MZ", fetch.MIN_BYTES, "expected"),
        ("0.25.0", b"<h", fetch.MIN_BYTES, "not a Windows"),
        ("0.25.0", b"MZ", 1000, "floor"),
    ],
    ids=["wrong-version", "not-pe", "truncated"],
)
def test_verify_refuses_the_wrong_version_a_non_pe_or_a_truncated_file(
    tmp_path, version, head, size, match
):
    path = tmp_path / fetch.setup_name(version)
    path.write_bytes(head + b"\0" * (size - len(head)))
    with pytest.raises(SystemExit, match=match):
        fetch.verify(path, "0.25.0")


def test_release_source_refuses_a_tag_that_is_not_a_version(tmp_path):
    with pytest.raises(SystemExit, match="vX.Y.Z"):
        fetch.from_release("amd/gaia", "latest", tmp_path)


class _FakeGitHub:
    """publish.yml's runs, its gate job and its artifacts, as a sequence of polls."""

    def __init__(self, polls, archive=b""):
        self.polls = list(polls)
        self.archive = archive
        self.current = None

    def api(self, path):
        if "/workflows/" in path:
            self.current = self.polls.pop(0) if self.polls else self.current
            return {"workflow_runs": self.current.get("runs", [])}
        if path.split("?")[0].endswith("/jobs"):
            gate = self.current.get("gate")
            return {"jobs": [{"name": "Validate Release"}, *([gate] if gate else [])]}
        return {"artifacts": self.current.get("artifacts", [])}

    def gh(self, args):
        assert args[0] == "api" and args[1].endswith("/zip")
        return self.archive


def _zip(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, data)
        zf.writestr("latest.yml", b"version: x")
    return buf.getvalue()


RUN = {
    "id": 7,
    "run_number": 3,
    "head_branch": "v0.25.0",
    "status": "in_progress",
    "conclusion": None,
    "html_url": "u",
}
GATE = "Build Desktop Installers / Build complete"
PENDING = {"name": GATE, "status": "in_progress", "conclusion": None}
PASSED = {"name": GATE, "status": "completed", "conclusion": "success"}
ARTIFACT = [{"id": 9, "expired": False}]


def _install(monkeypatch, fake):
    monkeypatch.setattr(fetch, "_api", fake.api)
    monkeypatch.setattr(fetch, "_gh", fake.gh)
    monkeypatch.setattr(fetch.time, "sleep", lambda _s: None)


def _fetch(tmp_path, wait_minutes=5):
    return fetch.from_publish_run("amd/gaia", "abc", "0.25.0", tmp_path, wait_minutes)


def test_publish_run_source_waits_for_the_run_then_its_checks(tmp_path, monkeypatch):
    name = fetch.setup_name("0.25.0")
    fake = _FakeGitHub(
        [
            {"runs": []},
            # Uploaded mid-build, before validation: present but not yet trusted.
            {"runs": [RUN], "gate": PENDING, "artifacts": ARTIFACT},
            {"runs": [RUN], "gate": PASSED, "artifacts": ARTIFACT},
        ],
        archive=_zip(name, _setup_bytes()),
    )
    _install(monkeypatch, fake)
    path = _fetch(tmp_path)
    assert path == tmp_path / name and path.read_bytes()[:2] == b"MZ"
    assert fake.polls == []


def test_publish_run_source_refuses_a_setup_that_failed_its_checks(
    tmp_path, monkeypatch
):
    failed = dict(PASSED, conclusion="failure")
    _install(
        monkeypatch,
        _FakeGitHub([{"runs": [RUN], "gate": failed, "artifacts": ARTIFACT}]),
    )
    with pytest.raises(SystemExit, match="failed its own checks"):
        _fetch(tmp_path)
    assert not list(tmp_path.iterdir())


def test_publish_run_source_ignores_another_tags_run_on_the_same_commit(
    tmp_path, monkeypatch
):
    rc = dict(RUN, id=6, run_number=9, head_branch="v0.25.0-rc.1")
    _install(
        monkeypatch,
        _FakeGitHub([{"runs": [rc], "gate": PASSED, "artifacts": ARTIFACT}]),
    )
    with pytest.raises(SystemExit, match="no publish.yml push run for v0.25.0"):
        _fetch(tmp_path, wait_minutes=0)


def test_publish_run_source_fails_loudly_when_the_run_ended_before_its_checks(
    tmp_path, monkeypatch
):
    done = dict(RUN, status="completed", conclusion="cancelled")
    _install(monkeypatch, _FakeGitHub([{"runs": [done], "gate": PENDING}]))
    with pytest.raises(SystemExit, match="without its 'Build complete' job completing"):
        _fetch(tmp_path)


def test_publish_run_source_points_at_the_release_once_the_artifact_expired(
    tmp_path, monkeypatch
):
    done = dict(RUN, status="completed", conclusion="success")
    _install(
        monkeypatch, _FakeGitHub([{"runs": [done], "gate": PASSED, "artifacts": []}])
    )
    with pytest.raises(SystemExit, match="--release v0.25.0"):
        _fetch(tmp_path)


def test_publish_run_source_times_out_rather_than_waiting_forever(
    tmp_path, monkeypatch
):
    _install(monkeypatch, _FakeGitHub([{"runs": [RUN], "gate": PENDING}]))
    with pytest.raises(SystemExit, match="timed out after 0 min"):
        _fetch(tmp_path, wait_minutes=0)


def test_publish_run_source_refuses_an_artifact_for_another_version(
    tmp_path, monkeypatch
):
    fake = _FakeGitHub(
        [{"runs": [RUN], "gate": PASSED, "artifacts": ARTIFACT}],
        archive=_zip(fetch.setup_name("0.24.9"), _setup_bytes()),
    )
    _install(monkeypatch, fake)
    with pytest.raises(SystemExit, match="has no gaia-agent-ui-0.25.0-x64-setup.exe"):
        _fetch(tmp_path)
    assert not list(tmp_path.iterdir())


def test_main_requires_exactly_one_source(tmp_path):
    with pytest.raises(SystemExit):
        fetch.main(["--out", str(tmp_path)])
    with pytest.raises(SystemExit):
        fetch.main(
            ["--release", "v1.0.0", "--publish-run-sha", "abc", "--out", str(tmp_path)]
        )
    with pytest.raises(SystemExit):
        fetch.main(["--publish-run-sha", "abc", "--out", str(tmp_path)])


def test_workflow_publishes_the_windows_setup_after_the_raw_binaries():
    # The Worker fills a version's `artifact` field from the FIRST publish, and
    # hub installs download that field expecting the raw binary.
    wf = (REPO_ROOT / ".github" / "workflows" / "release_components.yml").read_text(
        encoding="utf-8"
    )
    job = re.search(r"^  publish-windows-setup:\n(.*?)(?=^  \S)", wf, re.M | re.S)
    assert job, "release_components.yml has no publish-windows-setup job"
    needs = re.search(r"needs: \[([^\]]+)\]", job.group(1)).group(1)
    assert "terminal-hub" in needs
    assert "--platform win-x64" in job.group(1)
