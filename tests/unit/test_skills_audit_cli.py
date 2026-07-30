# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``gaia skill audit ./skill/`` (issue #2468).

Exit codes are the contract CI depends on: ALLOW is 0, REVIEW and BLOCK are
distinct non-zero codes so a workflow can hold a skill without treating it as a
rejection.
"""

from __future__ import annotations

import argparse
import json
import textwrap

import pytest

from gaia.skills import cli as skill_cli
from gaia.skills.cli import EXIT_BLOCK, EXIT_INVALID, EXIT_OK, EXIT_REVIEW


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    skill_cli.add_subparser(subparsers)
    return parser.parse_args(argv)


def _run(argv: list[str]) -> int:
    return skill_cli.handle(_parse(argv))


def _write_skill(
    tmp_path,
    *,
    name: str = "demo",
    tier: str | None = None,
    permissions: list[str] | None = None,
    body: str = "# Demo\n\nSummarize the input.\n",
    tools: str | None = None,
):
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}", "description: A demo skill.", 'version: "1.0.0"']
    gaia_block = []
    if tier:
        gaia_block.append(f"    security_tier: {tier}")
    if permissions:
        gaia_block.append("    permissions:")
        gaia_block.extend(f"      - {p}" for p in permissions)
    if gaia_block:
        lines.append("metadata:")
        lines.append("  gaia:")
        lines.extend(gaia_block)
    (directory / "SKILL.md").write_text(
        "---\n" + "\n".join(lines) + "\n---\n\n" + body, encoding="utf-8"
    )
    if tools is not None:
        (directory / "tools.py").write_text(textwrap.dedent(tools), encoding="utf-8")
    return directory


# ----------------------------------------------------------------------
# Exit codes
# ----------------------------------------------------------------------


def test_a_clean_skill_exits_zero(tmp_path, capsys):
    code = _run(["skill", "audit", str(_write_skill(tmp_path))])
    assert code == EXIT_OK
    assert "ALLOW" in capsys.readouterr().out


def test_a_blocked_skill_exits_with_the_block_code(tmp_path, capsys):
    directory = _write_skill(tmp_path, tools="def f(x):\n    return eval(x)\n")
    assert _run(["skill", "audit", str(directory)]) == EXIT_BLOCK
    assert "BLOCK" in capsys.readouterr().out


def test_a_reviewed_skill_exits_with_the_review_code(tmp_path, capsys):
    directory = _write_skill(
        tmp_path,
        tier="community",
        tools="import subprocess\ndef f():\n    subprocess.run(['ls'])\n",
    )
    assert _run(["skill", "audit", str(directory)]) == EXIT_REVIEW
    assert "REVIEW" in capsys.readouterr().out


def test_review_and_block_are_distinguishable(tmp_path):
    """CI must be able to hold a skill without treating it as rejected."""
    assert EXIT_REVIEW != EXIT_BLOCK
    assert EXIT_REVIEW != EXIT_OK


def test_a_missing_directory_exits_invalid(tmp_path, capsys):
    code = _run(["skill", "audit", str(tmp_path / "does-not-exist")])
    assert code == EXIT_INVALID
    assert "SKILL.md" in capsys.readouterr().err


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------


def test_text_output_names_the_finding_location(tmp_path, capsys):
    directory = _write_skill(
        tmp_path, tools="import requests\ndef f(u):\n    return requests.get(u).text\n"
    )
    _run(["skill", "audit", str(directory)])
    out = capsys.readouterr().out
    assert "tools.py:3" in out
    assert "permission.undeclared.network" in out


def test_json_output_is_the_wire_payload(tmp_path, capsys):
    directory = _write_skill(tmp_path)
    _run(["skill", "audit", str(directory), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "ALLOW"
    assert payload["skill"] == "demo"
    assert payload["content_digest"].startswith("sha256:")
    assert payload["cleared_tiers"]


def test_json_output_is_the_only_thing_on_stdout(tmp_path, capsys):
    """A publish script pipes stdout straight into the audit form part."""
    _run(["skill", "audit", str(_write_skill(tmp_path)), "--json"])
    json.loads(capsys.readouterr().out)  # raises if anything else was printed


def test_output_file_receives_the_report(tmp_path, capsys):
    directory = _write_skill(tmp_path)
    destination = tmp_path / "report.json"
    _run(["skill", "audit", str(directory), "--output", str(destination)])
    assert json.loads(destination.read_text())["verdict"] == "ALLOW"


def test_sarif_output_is_written(tmp_path):
    directory = _write_skill(
        tmp_path, tools="import requests\ndef f(u):\n    return requests.get(u).text\n"
    )
    destination = tmp_path / "audit.sarif"
    _run(["skill", "audit", str(directory), "--sarif", str(destination)])
    sarif = json.loads(destination.read_text())
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"]


def test_sarif_paths_are_anchored_to_the_repo_by_default(tmp_path, monkeypatch):
    """Code scanning needs a repo-relative path, not a bare 'tools.py'."""
    root = tmp_path / "repo"
    (root / "hub" / "skills").mkdir(parents=True)
    directory = _write_skill(
        root / "hub" / "skills",
        tools="import requests\ndef f(u):\n    return requests.get(u).text\n",
    )
    monkeypatch.chdir(root)
    destination = tmp_path / "audit.sarif"
    _run(["skill", "audit", "hub/skills/demo", "--sarif", str(destination)])

    uri = json.loads(destination.read_text())["runs"][0]["results"][0]["locations"][0][
        "physicalLocation"
    ]["artifactLocation"]["uri"]
    assert uri == "hub/skills/demo/tools.py"
    assert directory.exists()


def test_sarif_path_prefix_can_be_overridden(tmp_path):
    directory = _write_skill(
        tmp_path, tools="import requests\ndef f(u):\n    return requests.get(u).text\n"
    )
    destination = tmp_path / "audit.sarif"
    _run(
        [
            "skill",
            "audit",
            str(directory),
            "--sarif",
            str(destination),
            "--path-prefix",
            "vendor/skills/demo",
        ]
    )
    uri = json.loads(destination.read_text())["runs"][0]["results"][0]["locations"][0][
        "physicalLocation"
    ]["artifactLocation"]["uri"]
    assert uri == "vendor/skills/demo/tools.py"


def test_a_skill_outside_the_cwd_gets_no_prefix_rather_than_a_bad_one(tmp_path):
    """An absolute or '../' SARIF path is rejected by code scanning."""
    directory = _write_skill(
        tmp_path, tools="import requests\ndef f(u):\n    return requests.get(u).text\n"
    )
    destination = tmp_path / "audit.sarif"
    _run(["skill", "audit", str(directory), "--sarif", str(destination)])
    uri = json.loads(destination.read_text())["runs"][0]["results"][0]["locations"][0][
        "physicalLocation"
    ]["artifactLocation"]["uri"]
    assert uri == "tools.py"
    assert not uri.startswith(("/", ".."))


def test_snippets_are_withheld_unless_requested(tmp_path, capsys):
    directory = _write_skill(tmp_path, tools="KEY_FILE = '/home/u/.ssh/id_rsa'\n")
    _run(["skill", "audit", str(directory)])
    assert "id_rsa" not in capsys.readouterr().out

    _run(["skill", "audit", str(directory), "--show-snippets"])
    assert "id_rsa" in capsys.readouterr().out


# ----------------------------------------------------------------------
# --tier: check a claim before making it
# ----------------------------------------------------------------------


def test_tier_override_audits_against_a_different_claim(tmp_path, capsys):
    """An author can ask 'would this pass community?' before claiming it."""
    directory = _write_skill(
        tmp_path, tools="import subprocess\ndef f():\n    subprocess.run(['ls'])\n"
    )
    assert _run(["skill", "audit", str(directory)]) == EXIT_OK

    code = _run(["skill", "audit", str(directory), "--tier", "community"])
    assert code == EXIT_REVIEW
    assert "community" in capsys.readouterr().out


def test_tier_override_is_recorded_in_the_report(tmp_path, capsys):
    directory = _write_skill(tmp_path)
    _run(["skill", "audit", str(directory), "--tier", "community", "--json"])
    assert json.loads(capsys.readouterr().out)["security_tier"] == "community"


def test_an_unknown_tier_is_rejected(tmp_path, capsys):
    directory = _write_skill(tmp_path)
    with pytest.raises(SystemExit):
        _run(["skill", "audit", str(directory), "--tier", "platinum"])


# ----------------------------------------------------------------------
# --fail-on: let a repo tighten the gate
# ----------------------------------------------------------------------


def test_fail_on_turns_an_advisory_finding_into_a_failure(tmp_path):
    directory = _write_skill(
        tmp_path, tools="import subprocess\ndef f():\n    subprocess.run(['ls'])\n"
    )
    assert _run(["skill", "audit", str(directory)]) == EXIT_OK
    assert _run(["skill", "audit", str(directory), "--fail-on", "high"]) == EXIT_BLOCK


def test_fail_on_does_not_fire_below_the_threshold(tmp_path):
    directory = _write_skill(tmp_path, permissions=["network:read"])
    assert _run(["skill", "audit", str(directory), "--fail-on", "high"]) == EXIT_OK


# ----------------------------------------------------------------------
# The audit verb must not disturb the existing subcommands
# ----------------------------------------------------------------------


def test_existing_subcommands_still_parse(tmp_path):
    for argv in (
        ["skill", "list"],
        ["skill", "info", "demo"],
        ["skill", "create", "demo"],
        ["skill", "export", "demo"],
    ):
        assert _parse(argv).skill_action == argv[1]


def test_audit_is_registered_as_a_subcommand():
    assert _parse(["skill", "audit", "."]).skill_action == "audit"
