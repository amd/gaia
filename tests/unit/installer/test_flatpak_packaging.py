# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The desktop app's Flatpak is built, checked and released with the others.

A Flatpak that drops out of one of these lists fails nothing on its own: the
build still passes and the release simply ships without it. So each place that
has to name it is read here.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
BUILDER_YML = REPO / "src" / "gaia" / "apps" / "webui" / "electron-builder.yml"
BUILD_WORKFLOW = REPO / ".github" / "workflows" / "build-installers.yml"
PUBLISH_WORKFLOW = REPO / ".github" / "workflows" / "publish.yml"
SMOKE = REPO / "installer" / "linux" / "flatpak-smoke.sh"


def _load_flatpak_refs():
    path = REPO / "installer" / "scripts" / "flatpak_refs.py"
    spec = importlib.util.spec_from_file_location("flatpak_refs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


flatpak_refs = _load_flatpak_refs()


def _builder() -> dict:
    return yaml.safe_load(BUILDER_YML.read_text(encoding="utf-8"))


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps_text(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


class TestBuilderConfig:
    def test_flatpak_is_a_linux_target(self):
        targets = [t["target"] for t in _builder()["linux"]["target"]]
        assert targets == ["deb", "AppImage", "flatpak"]

    def test_the_app_knows_its_own_flatpak_id(self):
        """services/flatpak.cjs compares FLATPAK_ID to this, so another app's sandbox isn't taken for GAIA's."""
        source = (BUILDER_YML.parent / "services" / "flatpak.cjs").read_text(
            encoding="utf-8"
        )
        assert f'const APP_ID = "{_builder()["appId"]}";' in source

    def test_the_bundle_records_its_runtime_repo(self):
        hook = _builder()["artifactBuildCompleted"]
        assert (BUILDER_YML.parent / hook).resolve() == (
            REPO / "installer" / "scripts" / "flatpak-runtime-repo.cjs"
        )

    def test_file_name_matches_the_other_desktop_builds(self):
        assert (
            _builder()["flatpak"]["artifactName"]
            == "gaia-agent-ui-${version}-${arch}.${ext}"
        )

    @pytest.mark.parametrize(
        "arg, why",
        [
            ("--persist=.gaia", "~/.gaia is lost on exit without it"),
            ("--persist=.cache", "downloaded models are lost on exit"),
            (
                "--talk-name=org.freedesktop.background.Monitor",
                "how the app tells GNOME 44+ from one with no Background Apps",
            ),
            ("--device=all", "the NPU is /dev/accel, which --device=dri leaves out"),
            ("--share=network", "model downloads and the local model server"),
            ("--socket=wayland", "the window"),
            ("--socket=pulseaudio", "voice"),
            ("--filesystem=xdg-documents", "document Q&A"),
            ("--filesystem=xdg-config/autostart:create", "start at sign-in"),
            ("--talk-name=org.freedesktop.secrets", "connector sign-ins"),
        ],
    )
    def test_sandbox_keeps_a_permission_gaia_needs(self, arg, why):
        assert arg in _builder()["flatpak"]["finishArgs"], why

    def test_sandbox_does_not_share_the_home_folder(self):
        """~/.gaia would then be the host's, shared with a daemon the sandbox can't see."""
        args = _builder()["flatpak"]["finishArgs"]
        assert not [a for a in args if a in ("--filesystem=home", "--filesystem=host")]


class TestFlatpakRefs:
    def test_reads_the_real_config(self):
        cfg = _builder()["flatpak"]
        assert flatpak_refs.refs(BUILDER_YML.read_text(encoding="utf-8")) == [
            f"{cfg['runtime']}//{cfg['runtimeVersion']}",
            f"{cfg['sdk']}//{cfg['runtimeVersion']}",
            f"{cfg['base']}//{cfg['baseVersion']}",
        ]

    def test_reads_quoted_and_commented_values(self):
        text = (
            "linux:\n  runtime: not-this-one\n"
            "flatpak:\n"
            "  runtime: org.example.Platform  # the runtime\n"
            '  runtimeVersion: "25.08"\n'
            "  sdk: org.example.Sdk\n"
            "  base: org.example.BaseApp\n"
            "  baseVersion: '24.08'\n"
            "  finishArgs:\n    - --share=ipc\n"
            "appImage:\n  runtime: nor-this\n"
        )
        assert flatpak_refs.refs(text) == [
            "org.example.Platform//25.08",
            "org.example.Sdk//25.08",
            "org.example.BaseApp//24.08",
        ]

    def test_a_missing_setting_fails_naming_it(self):
        text = "flatpak:\n  runtime: org.example.Platform\n  sdk: org.example.Sdk\n"
        with pytest.raises(ValueError) as err:
            flatpak_refs.refs(text, "some/electron-builder.yml")
        message = str(err.value)
        assert "some/electron-builder.yml" in message
        assert "flatpak.runtimeVersion" in message
        assert "flatpak.baseVersion" in message

    def test_no_flatpak_block_fails(self):
        with pytest.raises(ValueError, match="flatpak.runtime"):
            flatpak_refs.refs("linux:\n  target: []\n")


class TestBuildWorkflow:
    def test_linux_build_installs_the_tools_and_uploads_the_flatpak(self):
        build = _workflow(BUILD_WORKFLOW)["jobs"]["build"]
        linux = next(
            m
            for m in build["strategy"]["matrix"]["include"]
            if m["platform"] == "linux"
        )
        assert "src/gaia/apps/webui/dist-app/*.flatpak" in linux["artifacts"].split()
        tools = next(
            s
            for s in build["steps"]
            if s.get("name") == "Install the Flatpak build tools"
        )
        assert tools["if"] == "matrix.platform == 'linux'"
        assert "flatpak-builder" in tools["run"]
        assert " ostree " in tools["run"], "flatpak-runtime-repo.cjs runs `ostree init`"
        assert "installer/scripts/flatpak_refs.py" in tools["run"]
        names = [s.get("name") for s in build["steps"]]
        assert names.index("Install the Flatpak build tools") < names.index(
            "Build installer"
        )

    def test_a_missing_flatpak_fails_artifact_validation(self):
        validate = _workflow(BUILD_WORKFLOW)["jobs"]["validate-artifacts"]
        assert "Linux: no .flatpak found" in _steps_text(validate)

    def test_the_install_smoke_gates_the_build(self):
        jobs = _workflow(BUILD_WORKFLOW)["jobs"]
        smoke = jobs["flatpak-install-smoke"]
        text = _steps_text(smoke)
        assert "flatpak install --user -y --noninteractive" in text
        assert "remote-add" not in text, (
            "the smoke installs from a PC that never added Flathub; "
            "the bundle's own runtime repo must be enough"
        )
        assert "installer/linux/flatpak-smoke.sh" in text
        gate = jobs["build-complete"]
        assert "flatpak-install-smoke" in gate["needs"]
        gate_text = _steps_text(gate)
        assert "needs.flatpak-install-smoke.result" in gate_text
        assert '"$flatpak_result"' in gate_text


class TestReleaseWorkflow:
    def test_a_release_without_the_flatpak_is_refused(self):
        release = _workflow(PUBLISH_WORKFLOW)["jobs"]["github-release"]
        check = next(
            s
            for s in release["steps"]
            if s.get("name") == "Check every desktop installer is attached"
        )
        assert '"gaia-agent-ui-${v}-x86_64.flatpak"' in check["run"]

    def test_candidates_and_finals_attach_the_same_files(self):
        """One upload step for both: the only RC difference is the prerelease flag."""
        release = _workflow(PUBLISH_WORKFLOW)["jobs"]["github-release"]
        upload = next(
            s for s in release["steps"] if "github_release_retry.py" in s.get("run", "")
        )
        assert "find release-assets dist -type f" in upload["run"]
        rc_only = re.findall(
            r'if \[ "\$\{IS_RC\}" = "true" \]; then\n(.*?)\n\s*fi', upload["run"], re.S
        )
        assert [block.strip() for block in rc_only] == ["args+=(--prerelease)"]

    def test_release_notes_name_the_flatpak(self):
        text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        assert "`gaia-agent-ui-@NPM_VERSION@-x86_64.flatpak`" in text
        assert "`gaia-agent-ui-*-x86_64.flatpak`" in text


class TestSmokeScript:
    def test_checks_the_permissions_the_config_grants(self):
        text = SMOKE.read_text(encoding="utf-8")
        assert not text.startswith("#!/bin/sh"), "uses bash here-strings"
        assert '".gaia;"' in text
        assert '".cache;"' in text
        assert '"devices=all;"' in text
        assert "/core/gaia" in text
        assert "lemonade-embeddable-" in text
