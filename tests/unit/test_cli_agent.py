# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the ``gaia agent`` developer workflow (gaia.cli_agent).

Covers init scaffolding, version bumping, and the ``--lint`` / ``--live``
quality gates. The LLM is never required: ``--live`` is exercised with a fake
agent so the suite runs without Lemonade.
"""

from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from gaia import cli_agent
from gaia.hub import manifest as hub_manifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_args(name, output, language="python", force=False):
    return Namespace(
        agent_action="init",
        name=name,
        language=language,
        output=str(output),
        force=force,
    )


def _version_args(bump, path):
    return Namespace(agent_action="version", bump=bump, path=str(path))


def _test_args(path, lint=True, live=False, timeout=60):
    return Namespace(
        agent_action="test",
        path=str(path),
        lint=lint,
        live=live,
        timeout=timeout,
    )


@pytest.fixture
def scaffolded_python(tmp_path):
    """Init a python agent under tmp_path and return its package dir."""
    cli_agent.cmd_init(_init_args("demo-agent", tmp_path))
    return tmp_path / "demo-agent"


# ---------------------------------------------------------------------------
# Name derivation
# ---------------------------------------------------------------------------


def test_name_derivation():
    names = cli_agent._Names("My Cool Agent")
    assert names.id == "my-cool-agent"
    assert names.package == "gaia_agent_my_cool_agent"
    assert names.dist_name == "gaia-agent-my-cool-agent"
    assert names.class_name == "MyCoolAgent"
    assert names.display_name == "My Cool Agent"


def test_name_derivation_appends_agent_suffix():
    assert cli_agent._Names("demo").class_name == "DemoAgent"
    # Already ends with Agent -> no double suffix.
    assert cli_agent._Names("demo-agent").class_name == "DemoAgent"


def test_invalid_name_raises():
    with pytest.raises(cli_agent.AgentWorkflowError):
        cli_agent._Names("")
    with pytest.raises(cli_agent.AgentWorkflowError):
        cli_agent._Names("Bad/Name!")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_scaffolds_expected_files(scaffolded_python):
    pkg = scaffolded_python
    assert (pkg / "gaia-agent.yaml").exists()
    assert (pkg / "pyproject.toml").exists()
    assert (pkg / "README.md").exists()
    assert (pkg / "gaia_agent_demo_agent" / "__init__.py").exists()
    assert (pkg / "gaia_agent_demo_agent" / "agent.py").exists()
    assert (pkg / "tests" / "test_agent.py").exists()


def test_init_manifest_parses_and_is_complete(scaffolded_python):
    parsed = hub_manifest.parse(scaffolded_python)
    assert parsed.id == "demo-agent"
    assert parsed.language == "python"
    assert parsed.version == "0.1.0"
    assert parsed.python is not None
    assert parsed.python.entry_module == "gaia_agent_demo_agent"
    assert parsed.python.entry_class == "DemoAgent"


def test_init_pyproject_has_entry_point(scaffolded_python):
    text = (scaffolded_python / "pyproject.toml").read_text(encoding="utf-8")
    assert 'entry-points."gaia.agent"' in text
    assert "demo-agent = " in text
    assert "gaia_agent_demo_agent:build_registration" in text


def test_init_existing_dir_without_force_fails(tmp_path):
    cli_agent.cmd_init(_init_args("demo-agent", tmp_path))
    with pytest.raises(cli_agent.AgentWorkflowError, match="already exists"):
        cli_agent.cmd_init(_init_args("demo-agent", tmp_path, force=False))


def test_init_force_overwrites(tmp_path):
    cli_agent.cmd_init(_init_args("demo-agent", tmp_path))
    # Should not raise with force=True.
    cli_agent.cmd_init(_init_args("demo-agent", tmp_path, force=True))


def test_init_cpp_scaffold(tmp_path):
    cli_agent.cmd_init(_init_args("native-demo", tmp_path, language="cpp"))
    pkg = tmp_path / "native-demo"
    assert (pkg / "CMakeLists.txt").exists()
    assert (pkg / "src" / "agent.cpp").exists()
    assert (pkg / "tests" / "test_agent.cpp").exists()
    parsed = hub_manifest.parse(pkg)
    assert parsed.language == "cpp"
    assert parsed.cpp is not None
    assert parsed.cpp.binaries  # at least one platform binary declared


# ---------------------------------------------------------------------------
# test --lint
# ---------------------------------------------------------------------------


def test_lint_passes_on_fresh_python_scaffold(scaffolded_python):
    # A freshly scaffolded package must pass every static gate as-is.
    cli_agent.cmd_test(_test_args(scaffolded_python, lint=True))


def test_lint_passes_on_fresh_cpp_scaffold(tmp_path):
    cli_agent.cmd_init(_init_args("native-demo", tmp_path, language="cpp"))
    cli_agent.cmd_test(_test_args(tmp_path / "native-demo", lint=True))


def test_lint_catches_broken_manifest(scaffolded_python):
    # Corrupt the manifest by removing a required field.
    manifest_path = scaffolded_python / "gaia-agent.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    del data["version"]
    manifest_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(cli_agent.AgentWorkflowError, match="lint failed"):
        cli_agent.cmd_test(_test_args(scaffolded_python, lint=True))


def test_lint_catches_missing_entry_point(scaffolded_python):
    # Strip the gaia.agent entry point from pyproject.toml.
    pyproject = scaffolded_python / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    cut = text.split('[project.entry-points."gaia.agent"]')[0]
    pyproject.write_text(cut, encoding="utf-8")

    with pytest.raises(cli_agent.AgentWorkflowError, match="gaia.agent"):
        cli_agent.cmd_test(_test_args(scaffolded_python, lint=True))


def test_lint_catches_syntax_error(scaffolded_python):
    agent_py = scaffolded_python / "gaia_agent_demo_agent" / "agent.py"
    agent_py.write_text("def broken(:\n    pass\n", encoding="utf-8")

    with pytest.raises(cli_agent.AgentWorkflowError, match="lint failed"):
        cli_agent.cmd_test(_test_args(scaffolded_python, lint=True))


def test_lint_missing_package_dir_fails(tmp_path):
    with pytest.raises(cli_agent.AgentWorkflowError, match="package directory"):
        cli_agent.cmd_test(_test_args(tmp_path / "does-not-exist", lint=True))


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def _manifest_version(pkg):
    return yaml.safe_load((pkg / "gaia-agent.yaml").read_text(encoding="utf-8"))[
        "version"
    ]


def test_version_patch(scaffolded_python):
    cli_agent.cmd_version(_version_args("patch", scaffolded_python))
    assert _manifest_version(scaffolded_python) == "0.1.1"


def test_version_minor_resets_patch(scaffolded_python):
    cli_agent.cmd_version(_version_args("minor", scaffolded_python))
    assert _manifest_version(scaffolded_python) == "0.2.0"


def test_version_major_resets_minor_and_patch(scaffolded_python):
    cli_agent.cmd_version(_version_args("major", scaffolded_python))
    assert _manifest_version(scaffolded_python) == "1.0.0"


def test_version_syncs_pyproject_and_init(scaffolded_python):
    cli_agent.cmd_version(_version_args("patch", scaffolded_python))
    pyproject = (scaffolded_python / "pyproject.toml").read_text(encoding="utf-8")
    init_py = (scaffolded_python / "gaia_agent_demo_agent" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert 'version = "0.1.1"' in pyproject
    assert '__version__ = "0.1.1"' in init_py


def test_version_leaves_min_gaia_version_untouched(scaffolded_python):
    before = hub_manifest.parse(scaffolded_python).min_gaia_version
    cli_agent.cmd_version(_version_args("major", scaffolded_python))
    after = hub_manifest.parse(scaffolded_python).min_gaia_version
    assert before == after


def test_version_missing_manifest_fails(tmp_path):
    with pytest.raises(cli_agent.AgentWorkflowError, match="not found"):
        cli_agent.cmd_version(_version_args("patch", tmp_path))


def test_bump_semver_rejects_non_semver():
    with pytest.raises(cli_agent.AgentWorkflowError, match="SemVer"):
        cli_agent._bump_semver("not-a-version", "patch")


# ---------------------------------------------------------------------------
# test --live (LLM mocked)
# ---------------------------------------------------------------------------


class _FakeAgent:
    def __init__(self):
        self.calls = []

    def process_query(self, prompt, **kwargs):
        self.calls.append(prompt)
        return {"result": f"answer to {prompt}"}


class _CrashingAgent:
    def process_query(self, prompt, **kwargs):
        raise RuntimeError("boom")


class _EmptyAgent:
    def process_query(self, prompt, **kwargs):
        return {"result": ""}


def test_live_passes_with_fake_agent(scaffolded_python, monkeypatch):
    fake = _FakeAgent()
    monkeypatch.setattr(cli_agent, "_instantiate_agent", lambda pkg, parsed: fake)
    cli_agent.cmd_test(_test_args(scaffolded_python, lint=False, live=True))
    # The three scaffolded conversation_starters were each exercised.
    assert len(fake.calls) == 3


def test_live_detects_crash(scaffolded_python, monkeypatch):
    monkeypatch.setattr(
        cli_agent, "_instantiate_agent", lambda pkg, parsed: _CrashingAgent()
    )
    with pytest.raises(cli_agent.AgentWorkflowError, match="crashed"):
        cli_agent.cmd_test(_test_args(scaffolded_python, lint=False, live=True))


def test_live_detects_empty_response(scaffolded_python, monkeypatch):
    monkeypatch.setattr(
        cli_agent, "_instantiate_agent", lambda pkg, parsed: _EmptyAgent()
    )
    with pytest.raises(cli_agent.AgentWorkflowError, match="empty response"):
        cli_agent.cmd_test(_test_args(scaffolded_python, lint=False, live=True))


# ---------------------------------------------------------------------------
# handle() dispatch
# ---------------------------------------------------------------------------


def test_handle_returns_false_for_export():
    # export/import belong to the legacy handler in cli.py.
    assert cli_agent.handle(Namespace(agent_action="export")) is False


def test_handle_returns_true_for_init(tmp_path):
    assert cli_agent.handle(_init_args("demo-agent", tmp_path)) is True
