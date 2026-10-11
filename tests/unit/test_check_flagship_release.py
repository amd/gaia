# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A final core release fails unless its flagship agent was built from it.

v0.25.0 was prepared with the hub's flagship still at 0.2.0, frozen hundreds of
commits earlier; nothing in the core release noticed. util/check_flagship_release.py
is that check, and publish.yml / release_components.yml run it on a final.
"""

import importlib.util
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

_spec = importlib.util.spec_from_file_location(
    "check_flagship_release", REPO_ROOT / "util" / "check_flagship_release.py"
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

SHA = {p: f"{i}" * 64 for i, p in enumerate(gate.REQUIRED_PLATFORMS, start=1)}


def _lock(version="0.3.0", sha=SHA):
    return {
        "components": {
            "sidecar": {
                "componentVersion": version,
                "baseUrl": f"https://hub.amd-gaia.ai/agents/gaia/{version}",
                "platforms": {
                    p: {"filename": f"gaia-agent-{p}", "sha256": sha[p]}
                    for p in gate.REQUIRED_PLATFORMS
                },
            }
        }
    }


def _hub(version="0.3.0", latest="0.3.0", sha=SHA):
    return {
        "latest_version": latest,
        "versions": {
            "0.2.0": {"artifacts": []},
            version: {
                "artifacts": [
                    {"filename": f"gaia-agent-{p}", "sha256": sha[p]}
                    for p in gate.REQUIRED_PLATFORMS
                ]
            },
        },
    }


def test_a_consistent_release_passes():
    assert gate.check_lock(_lock(), "0.3.0") == []
    assert gate.check_hub(_hub(), "0.3.0", _lock()) == []


def test_a_lock_still_pinning_the_old_flagship_fails():
    problems = gate.check_lock(_lock(version="0.2.0"), "0.3.0")
    assert any("pins sidecar '0.2.0'" in p for p in problems)


def test_a_placeholder_hash_fails():
    sha = dict(SHA, **{"win32-x64": "PENDING-replace-with-real-sha256"})
    assert any("win32-x64" in p for p in gate.check_lock(_lock(sha=sha), "0.3.0"))


def test_an_unpublished_flagship_fails():
    problems = gate.check_hub(_hub(version="0.2.0", latest="0.2.0"), "0.3.0", _lock())
    assert problems and "no gaia 0.3.0" in problems[0]


def test_a_flagship_that_is_not_the_hubs_latest_fails():
    problems = gate.check_hub(_hub(latest="0.2.0"), "0.3.0", _lock())
    assert any("latest gaia is 0.2.0" in p for p in problems)


def test_hub_bytes_that_differ_from_the_lock_fail():
    hub = _hub(sha=dict(SHA, **{"linux-x64": "f" * 64}))
    assert any("linux-x64" in p for p in gate.check_hub(hub, "0.3.0", _lock()))


def test_a_missing_flagship_tag_fails(monkeypatch):
    def git(*args):
        raise subprocess.CalledProcessError(1, ["git", *args])

    monkeypatch.setattr(gate, "_git", git)
    assert "does not exist" in gate.check_built_from("HEAD", "0.3.0")[0]


def test_frozen_code_changed_since_the_flagship_tag_fails(monkeypatch):
    calls = []

    def git(*args):
        calls.append(args)
        return "" if args[0] == "rev-parse" else "src/gaia/agents/base/agent.py\n"

    monkeypatch.setattr(gate, "_git", git)
    problems = gate.check_built_from("v0.25.0", "0.3.0")
    assert "src/gaia/agents/base/agent.py" in problems[0]
    diff = next(c for c in calls if c[0] == "diff")
    assert diff[2:4] == ("agent-pkg-gaia-v0.3.0", "v0.25.0")


def test_no_frozen_change_passes(monkeypatch):
    monkeypatch.setattr(gate, "_git", lambda *a: "")
    assert gate.check_built_from("v0.25.0", "0.3.0") == []


def test_the_frozen_inputs_cover_the_agent_code():
    for path in ("src/gaia", "hub/agents/gaia/python", "hub/agents/chat/python"):
        assert path in gate.FROZEN_INPUTS


def test_the_committed_manifest_version_is_readable():
    text = (REPO_ROOT / gate.MANIFEST).read_text(encoding="utf-8")
    assert gate.flagship_version(text) == yaml.safe_load(text)["version"]


def _steps(workflow, job):
    data = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    return data["jobs"][job]["steps"]


def test_a_final_core_release_runs_the_gate():
    step = next(
        s
        for s in _steps("publish.yml", "validate")
        if "check_flagship_release.py" in s.get("run", "")
    )
    assert step["if"] == "steps.tag.outputs.IS_RC != 'true'"


def test_the_hub_component_publish_runs_the_gate_with_tags():
    steps = _steps("release_components.yml", "version")
    assert steps[0]["with"]["fetch-depth"] == 0
    assert any("check_flagship_release.py" in s.get("run", "") for s in steps)
