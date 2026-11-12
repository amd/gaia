# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The audit covers everything a skill ships, and reads Python as Python does.

Compiled modules, archives, ``__pycache__`` and ``node_modules`` are copied on
install and importable from the skill directory, so the audit names them
instead of skipping them. Source is parsed from bytes so a BOM or a PEP 263
coding declaration is read the way the interpreter reads it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.skills.audit import audit_skill, content_digest
from gaia.skills.audit.code import analyze_code
from gaia.skills.audit.findings import python_sources, uninspectable_content

_SKILL_MD = (
    "---\n"
    "name: demo\n"
    "description: A demo skill for audit coverage tests.\n"
    'version: "1.0.0"\n'
    "---\n\n"
    "# Demo\n\nSummarize the input and return it.\n"
)


def _skill(root: Path) -> Path:
    directory = root / "demo"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (directory / "tools.py").write_text("x = 1\n", encoding="utf-8")
    return directory


def _unreadable(analysis) -> dict[str, object]:
    return {f.file: f for f in analysis.findings if f.rule_id == "code.unreadable"}


# --- content the analyzer cannot inspect -------------------------------------


@pytest.mark.parametrize(
    ("relative", "reported"),
    [
        ("__pycache__/tools.cpython-312.pyc", "__pycache__/"),
        ("node_modules/pkg/index.js", "node_modules/"),
        ("scripts/node_modules/pkg/index.js", "scripts/node_modules/"),
        ("helper.pyc", "helper.pyc"),
        ("helper.pyo", "helper.pyo"),
        (
            "native.cpython-312-x86_64-linux-gnu.so",
            "native.cpython-312-x86_64-linux-gnu.so",
        ),
        ("native.pyd", "native.pyd"),
        ("vendor.zip", "vendor.zip"),
    ],
)
def test_shipped_content_the_audit_cannot_read_is_reported(
    tmp_path, relative, reported
):
    directory = _skill(tmp_path)
    target = directory / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00\x01\x02")

    assert uninspectable_content(directory) == [reported]
    finding = _unreadable(analyze_code(directory))[reported]
    assert finding.severity == "high"
    assert reported in finding.remediation


def test_a_directory_is_reported_once_however_many_files_it_holds(tmp_path):
    directory = _skill(tmp_path)
    for index in range(5):
        path = directory / "node_modules" / f"pkg{index}" / "index.js"
        path.parent.mkdir(parents=True)
        path.write_text("module.exports = 1;\n", encoding="utf-8")

    assert uninspectable_content(directory) == ["node_modules/"]


def test_uninspectable_content_holds_the_verdict_for_review(tmp_path):
    directory = _skill(tmp_path)
    (directory / "helper.pyc").write_bytes(b"\x00")

    report = audit_skill(directory)

    assert report.verdict != "ALLOW"
    assert "code.unreadable" in {f.rule_id for f in report.findings}


def test_a_clean_skill_has_nothing_uninspectable(tmp_path):
    directory = _skill(tmp_path)
    (directory / "scripts").mkdir()
    (directory / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    (directory / "README.txt").write_text("notes\n", encoding="utf-8")

    assert uninspectable_content(directory) == []
    assert _unreadable(analyze_code(directory)) == {}
    assert audit_skill(directory).verdict == "ALLOW"


def test_tool_caches_stay_out_of_the_audit(tmp_path):
    directory = _skill(tmp_path)
    for cache in (".git", ".pytest_cache", ".mypy_cache", ".ruff_cache"):
        (directory / cache).mkdir()
        (directory / cache / "blob.pyc").write_bytes(b"\x00")

    assert uninspectable_content(directory) == []


def test_shipped_binaries_and_node_modules_are_in_the_digest(tmp_path):
    directory = _skill(tmp_path)
    before = content_digest(directory)
    (directory / "vendor.zip").write_bytes(b"PK\x03\x04")
    with_zip = content_digest(directory)
    (directory / "node_modules").mkdir()
    (directory / "node_modules" / "index.js").write_text("1\n", encoding="utf-8")

    assert with_zip != before
    assert content_digest(directory) != with_zip


def test_a_bytecode_cache_still_leaves_the_digest_alone(tmp_path):
    """Loading a skill writes __pycache__; that must not read as drift."""
    directory = _skill(tmp_path)
    before = content_digest(directory)
    (directory / "__pycache__").mkdir()
    (directory / "__pycache__" / "tools.cpython-312.pyc").write_bytes(b"\x00")

    assert content_digest(directory) == before


# --- .pyw is Python ----------------------------------------------------------


def test_pyw_files_are_analyzed_as_python(tmp_path):
    directory = _skill(tmp_path)
    (directory / "helper.pyw").write_text(
        "import os\n\ndef go(cmd):\n    os.system(cmd)\n", encoding="utf-8"
    )

    assert directory / "helper.pyw" in python_sources(directory)
    assert any(
        f.file == "helper.pyw" and f.rule_id == "code.shell.os_system"
        for f in analyze_code(directory).findings
    )


def test_importing_a_local_pyw_module_is_not_an_undeclared_dependency(tmp_path):
    directory = _skill(tmp_path)
    (directory / "helper.pyw").write_text("VALUE = 1\n", encoding="utf-8")
    (directory / "tools.py").write_text("import helper\n", encoding="utf-8")

    report = audit_skill(directory)

    assert not [f for f in report.findings if "helper" in f.message]


# --- source encoding ---------------------------------------------------------


def test_a_utf8_bom_is_valid_python(tmp_path):
    directory = _skill(tmp_path)
    (directory / "tools.py").write_bytes(b"\xef\xbb\xbfx = 1\n")

    rules = {f.rule_id for f in analyze_code(directory).findings}

    assert "code.unparseable" not in rules
    assert "code.unreadable" not in rules


def test_a_coding_declaration_is_honoured(tmp_path):
    """Read as UTF-8 this is a comment; Python decodes it and runs the call."""
    directory = _skill(tmp_path)
    source = (
        "# -*- coding: utf-7 -*-\n"
        "import os\n"
        "def go(cmd):\n"
        "    pass  #+AAo-    os.system(cmd)\n"
    )
    (directory / "tools.py").write_bytes(source.encode("ascii"))

    findings = analyze_code(directory).findings

    assert any(
        f.file == "tools.py" and f.rule_id == "code.shell.os_system" for f in findings
    )


def test_plain_utf8_source_is_analyzed_as_before(tmp_path):
    directory = _skill(tmp_path)
    (directory / "tools.py").write_text(
        "# naïve café\nimport os\n\ndef go(cmd):\n    os.system(cmd)\n",
        encoding="utf-8",
    )

    findings = analyze_code(directory).findings

    assert "code.shell.os_system" in {f.rule_id for f in findings}
    assert "code.unparseable" not in {f.rule_id for f in findings}


def test_undecodable_source_is_still_refused(tmp_path):
    directory = _skill(tmp_path)
    (directory / "tools.py").write_bytes(b"x = '\xff\xfe'\n")

    rules = {f.rule_id for f in analyze_code(directory).findings}

    assert "code.unparseable" in rules
