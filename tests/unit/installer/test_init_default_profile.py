# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia init` with no --profile sets up the FLAGSHIP agent.

The bug these guard against: every init profile installed the ``chat`` wheel,
but the TUI spawns the flagship (``gaia-agent-gaia`` -> module ``gaia_agent``).
So `gaia init` — the one command the installers and the TUI's setup gate both
tell users to run — never installed the agent the TUI actually launches.

Two contracts are pinned here because nothing else can see them:

* the argparse default is a literal in ``cli.py`` (importing
  ``init_command`` costs ~3s and ``build_parser`` runs on every invocation),
  so it can drift from ``DEFAULT_INIT_PROFILE`` silently;
* ``tui/internal/gaiainit/gaiainit.go`` hardcodes the profile name and the
  "not ready" exit code. That is a cross-language contract no Python test
  and no Go test sees on its own.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.cli import build_parser
from gaia.installer.init_command import (
    DEFAULT_INIT_PROFILE,
    HUB_INSTALL_AGENTS,
    INIT_PROFILES,
    InitCommand,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
GAIAINIT_GO = REPO_ROOT / "tui" / "internal" / "gaiainit" / "gaiainit.go"

# The flagship's hub id. Its wheel is gaia-agent-gaia; the module it installs is
# plain `gaia_agent`, NOT `gaia_agent_gaia` (hub/agents/gaia/python).
FLAGSHIP_AGENT_ID = "gaia"
FLAGSHIP_IMPORT_NAME = "gaia_agent"


def _go_const(name: str) -> str:
    """Value of a top-level `const <name> = <literal>` in gaiainit.go."""
    source = GAIAINIT_GO.read_text(encoding="utf-8")
    match = re.search(rf"^const {name} = (.+)$", source, re.MULTILINE)
    assert match, f"const {name} not found in {GAIAINIT_GO}"
    return match.group(1).strip().strip('"')


class TestBareInitTargetsTheFlagship:
    def test_bare_init_resolves_to_the_flagship_profile(self):
        assert build_parser().parse_args(["init"]).profile == FLAGSHIP_AGENT_ID

    def test_cli_default_matches_the_installer_constant(self):
        """The literal in cli.py is a performance workaround, not a second
        source of truth."""
        assert build_parser().parse_args(["init"]).profile == DEFAULT_INIT_PROFILE

    def test_the_default_profile_exists(self):
        assert DEFAULT_INIT_PROFILE in INIT_PROFILES

    def test_flagship_profile_installs_the_flagship_agent(self):
        """Not `chat`: the chat wheel is a different agent and cannot serve
        the TUI, which spawns the flagship binary."""
        profile = INIT_PROFILES[FLAGSHIP_AGENT_ID]
        assert profile["agent"] == FLAGSHIP_AGENT_ID
        assert profile["agent"] in HUB_INSTALL_AGENTS

    def test_flagship_profile_carries_the_chat_models_and_rag_extras(self):
        """A flagship session needs the chat LLM, the RAG/memory embedder and
        the [rag] extras — dropping any of them makes documents fail at first
        index rather than at setup."""
        profile = INIT_PROFILES[FLAGSHIP_AGENT_ID]
        assert profile["models"] == [
            "Gemma-4-E4B-it-GGUF",
            "user.embeddinggemma-300m-GGUF",
        ]
        assert profile["pip_extras"] == ["rag"]
        # EmbeddingGemma only loads on 10.9.0+ (same floor as chat/rag).
        assert profile["min_lemonade_version"] == "10.9.0"

    def test_availability_probe_uses_the_flagships_real_module_name(self, monkeypatch):
        """`gaia_agent_gaia` does not exist and never will, so probing for it
        would report the flagship missing forever and reinstall it every run."""
        asked = []

        def fake_find_spec(name):
            asked.append(name)
            return None

        monkeypatch.setattr(
            "gaia.installer.init_command.importlib.util.find_spec", fake_find_spec
        )
        monkeypatch.setattr("gaia.hub.installer.read_sentinel", lambda _id: None)
        InitCommand._is_hub_agent_available(FLAGSHIP_AGENT_ID)
        assert asked == [FLAGSHIP_IMPORT_NAME]

    def test_other_agents_keep_the_gaia_agent_prefix_convention(self, monkeypatch):
        asked = []
        monkeypatch.setattr(
            "gaia.installer.init_command.importlib.util.find_spec",
            lambda name: asked.append(name),
        )
        monkeypatch.setattr("gaia.hub.installer.read_sentinel", lambda _id: None)
        InitCommand._is_hub_agent_available("chat")
        assert asked == ["gaia_agent_chat"]

    def test_a_binary_only_flagship_install_counts_as_present(self, monkeypatch):
        """The flagship publishes a native binary — `~/.gaia/agents/gaia/`
        holds `gaia-agent.exe` and no site-packages at all. An import probe
        can never see it, so without the sentinel check `gaia init` would
        re-download the flagship on every run and always print
        "initialization incomplete" after a successful setup."""
        monkeypatch.setattr(
            "gaia.installer.init_command.importlib.util.find_spec", lambda _n: None
        )
        monkeypatch.setattr("gaia.hub.installer.read_sentinel", lambda _id: object())
        assert InitCommand._is_hub_agent_available(FLAGSHIP_AGENT_ID) is True

    def test_absent_everywhere_reports_missing(self, monkeypatch):
        monkeypatch.setattr(
            "gaia.installer.init_command.importlib.util.find_spec", lambda _n: None
        )
        monkeypatch.setattr("gaia.hub.installer.read_sentinel", lambda _id: None)
        assert InitCommand._is_hub_agent_available(FLAGSHIP_AGENT_ID) is False

    def test_default_never_lands_on_a_device_specific_profile(self):
        """Device profile and agent profile are different axes. Bare `gaia init`
        resolves the agent axis only — auto-selecting `npu` would silently swap
        a Ryzen AI box from GGUF/Vulkan onto the FLM backend."""
        profile = INIT_PROFILES[DEFAULT_INIT_PROFILE]
        for device_key in ("required_device", "recipe", "backend"):
            assert device_key not in profile


class TestExplicitProfilesUnchanged:
    """Every explicit --profile still resolves exactly as before the default
    moved. A default change that quietly re-pointed an explicit flag would be
    the worse bug."""

    @pytest.mark.parametrize("profile", sorted(INIT_PROFILES.keys()) + ["mcp"])
    def test_explicit_profile_is_honoured(self, profile):
        ns = build_parser().parse_args(["init", "--profile", profile])
        assert ns.profile == profile

    def test_chat_profile_still_installs_the_chat_wheel(self):
        assert INIT_PROFILES["chat"]["agent"] == "chat"

    def test_npu_profile_still_installs_the_chat_wheel_on_flm_models(self):
        npu = INIT_PROFILES["npu"]
        assert npu["agent"] == "chat"
        assert npu["models"] == ["gemma4-it-e2b-FLM", "embed-gemma-300m-FLM"]
        assert npu["required_device"] == "amd_npu"

    def test_minimal_shortcut_still_wins_over_the_default(self):
        """`gaia init --minimal` is a documented shortcut for --profile
        minimal; the new default must not shadow it."""
        ns = build_parser().parse_args(["init", "--minimal"])
        assert ns.minimal is True
        assert ("minimal" if ns.minimal else ns.profile) == "minimal"

    def test_every_profile_still_has_the_required_keys(self):
        for name, profile in INIT_PROFILES.items():
            for key in ("description", "agent", "models", "approx_size"):
                assert key in profile, f"profile '{name}' is missing '{key}'"


class TestGoTuiContract:
    """The Go TUI is the main entry point and drives setup through this CLI.
    Nothing else checks that the two agree."""

    def test_tui_asks_for_a_profile_this_cli_accepts(self):
        profile = _go_const("Profile")
        assert profile in INIT_PROFILES, (
            f"{GAIAINIT_GO.name} runs `gaia init --profile {profile}`, "
            f"which this CLI would reject"
        )

    def test_tui_asks_for_the_flagship_profile(self):
        """The TUI spawns the flagship, so it must install the flagship."""
        assert _go_const("Profile") == DEFAULT_INIT_PROFILE

    def test_not_ready_exit_code_is_one(self):
        """gaiainit.go treats 1 — and ONLY 1 — as "not set up yet"; every other
        non-zero code means the question was not answered. Renumbering the
        Python side would make the TUI rerun a multi-minute setup on launch."""
        assert _go_const("notReadyExitCode") == "1"

    @pytest.mark.parametrize("ready", [True, False])
    def test_check_exit_codes_match_that_contract(self, ready, monkeypatch, capsys):
        from gaia.installer.init_command import SetupStatus

        monkeypatch.setattr(
            "gaia.installer.init_command.check_setup_status",
            lambda **kwargs: SetupStatus(ready=ready, reasons=[] if ready else ["x"]),
        )
        monkeypatch.setattr(
            sys, "argv", ["gaia", "init", "--check", "--profile", DEFAULT_INIT_PROFILE]
        )
        from gaia.cli import main

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == (0 if ready else 1)

    def test_the_tuis_exact_argv_is_accepted_by_the_real_cli(self):
        """End-to-end on the argv gaiainit.CheckArgs builds. An unrecognised
        flag exits 2, which the TUI reports as "could not determine" — this
        catches that before a user sees it."""
        argv = ["init", "--check", "--profile", _go_const("Profile")]
        proc = subprocess.run(
            [sys.executable, "-m", "gaia.cli", *argv],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=REPO_ROOT,
            env={**_child_env()},
        )
        assert proc.returncode in (0, 1), (
            f"`gaia {' '.join(argv)}` exited {proc.returncode}; "
            f"only 0 (ready) and 1 (not ready) are part of the contract.\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


def _child_env() -> dict:
    """Environment for the subprocess above, pinned to THIS worktree.

    `gaia` is frequently `pip install -e` linked to a different checkout, so a
    bare invocation would happily test someone else's source and pass.
    """
    import os

    env = dict(os.environ)
    src = str(REPO_ROOT / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
    return env
