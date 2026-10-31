# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/check_licenses.py: GAIA must not ship AGPL/GPL/LGPL dependencies."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_licenses", REPO / "util" / "check_licenses.py"
)
check_licenses = importlib.util.module_from_spec(_spec)
sys.modules["check_licenses"] = (
    check_licenses  # dataclasses resolve types through sys.modules
)
_spec.loader.exec_module(check_licenses)
classify = check_licenses.classify


@pytest.mark.parametrize(
    "licence",
    [
        "MIT",
        "Apache-2.0",
        "BSD-3-Clause",
        "Apache-2.0 OR BSD-3-Clause",
        "Apache-2.0 AND MIT",
        "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",  # numpy
        "MIT OR Apache-2.0",
        "MPL-2.0",  # certifi: file-level copyleft, unmodified use allowed
        "MIT License",
        "BSD License",
        "Apache Software License",
        "ISC",
        "(MIT OR CC0-1.0)",
        "BSD-3-Clause OR GPL-2.0",  # zstd: the permissive alternative is taken
    ],
)
def test_permissive_licences_pass(licence):
    verdict, _ = classify(licence)
    assert verdict == "ok", licence


@pytest.mark.parametrize(
    "licence,family",
    [
        (
            "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License",
            "AGPL",
        ),  # PyMuPDF 1.28.2
        ("AGPL-3.0-only", "AGPL"),
        ("AGPL-3.0-or-later", "AGPL"),
        ("GPL-3.0", "GPL"),
        ("GPL-2.0-or-later", "GPL"),
        ("GPL-2.0-or-later WITH Bootloader-exception", "GPL"),
        ("LGPL-2.1-or-later", "LGPL"),
        ("LGPL-3.0-only", "LGPL"),
        ("GNU Lesser General Public License v2 or later (LGPLv2+)", "LGPL"),
        ("GNU General Public License v3 (GPLv3)", "GPL"),
        ("MIT AND GPL-3.0-only", "GPL"),  # one copyleft part taints an AND
        ("SSPL-1.0", "SSPL"),
        ("CC-BY-NC-4.0", "CC"),
    ],
)
def test_copyleft_licences_are_denied(licence, family):
    verdict, detail = classify(licence)
    assert verdict == "denied", (licence, detail)
    assert family in detail


@pytest.mark.parametrize(
    "licence", ["", "   ", "Proprietary", "Custom licence, see LICENSE", "UNKNOWN"]
)
def test_unclassifiable_licences_fail_rather_than_pass(licence):
    verdict, _ = classify(licence)
    assert verdict == "unknown", licence


def test_lgpl_is_not_mistaken_for_allowed_by_substring():
    # "GPL" inside "LGPL" must still deny, never fall through as unknown-but-ok.
    assert classify("LGPLv3+")[0] == "denied"


def test_reviewed_exception_is_used_and_must_itself_be_permissive():
    finding = check_licenses._classify_component(
        "python", "matplotlib", "3.11.2", "License agreement for matplotlib"
    )
    assert finding.verdict == "ok"
    assert "reviewed" in finding.detail
    for (_eco, _name), (_versions, spdx, why) in check_licenses.REVIEWED.items():
        assert (
            classify(spdx)[0] == "ok"
        ), f"REVIEWED entry {_name} maps to a non-permissive licence {spdx}"
        assert why.strip(), f"REVIEWED entry {_name} has no justification"


def test_classifier_fallback_denies_gpl_classifier():
    finding = check_licenses._classify_component(
        "python",
        "somepkg",
        "1.0",
        "see LICENSE",
        ["License :: OSI Approved :: GNU General Public License v3 (GPLv3)"],
    )
    assert finding.verdict == "denied"


def test_classifier_fallback_allows_mit_classifier():
    finding = check_licenses._classify_component(
        "python",
        "somepkg",
        "1.0",
        "see LICENSE",
        ["License :: OSI Approved :: MIT License"],
    )
    assert finding.verdict == "ok"


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy",
            "MIT",
        ),
        (
            "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/",
            "Apache-2.0",
        ),
        (
            "Copyright (c) X\nRedistribution and use in source and binary forms ... Neither the name of",
            "BSD-3-Clause",
        ),
        (
            "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 2.1, February 1999\nPermission is hereby granted",
            "LGPL",
        ),
        ("GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007", "AGPL"),
    ],
)
def test_full_licence_texts_are_identified(text, expected):
    assert check_licenses._licence_from_text(text) == expected


def test_npm_scan_flags_copyleft_and_unknown(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "packages": {
                    "": {"name": "root"},
                    "node_modules/react": {"version": "19.3.1", "license": "MIT"},
                    "node_modules/bad-gpl": {
                        "version": "1.0.0",
                        "license": "GPL-3.0-only",
                    },
                    "node_modules/no-licence": {"version": "1.0.0"},
                    "node_modules/@scope/old-style": {
                        "version": "1.0.0",
                        "license": {"type": "BSD-3-Clause"},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    findings = {f.name: f.verdict for f in check_licenses.scan_npm(lock)}
    assert findings == {
        "react": "ok",
        "bad-gpl": "denied",
        "no-licence": "unknown",
        "@scope/old-style": "ok",
    }
    assert check_licenses.report(check_licenses.scan_npm(lock)) == 1


def test_report_passes_when_everything_is_permissive(capsys):
    ok = [check_licenses.Finding("npm", "react", "19.3.1", "MIT", "ok")]
    assert check_licenses.report(ok) == 0
    assert "1 ok" in capsys.readouterr().out


def test_python_scan_of_this_environment_runs():
    # Smoke: the scanner reads real installed metadata without crashing; verdicts depend on the env.
    findings = check_licenses.scan_python()
    assert findings, "no distributions found"
    assert all(f.verdict in {"ok", "denied", "unknown"} for f in findings)


def test_setup_py_declares_no_known_copyleft_packages():
    setup = (REPO / "setup.py").read_text(encoding="utf-8").lower()
    for pkg in ("pymupdf", "fitz", "pyqt5", "pyqt6", "mysql-connector", "pygobject"):
        assert f'"{pkg}' not in setup, f"setup.py requires copyleft package {pkg}"


def test_empty_scan_fails_instead_of_passing(capsys):
    # A failed install leaves nothing to scan; that must not read as "all clean".
    assert check_licenses.report([]) == 2
    assert "proves nothing" in capsys.readouterr().out


# --- SPDX parsing: parentheses, precedence, WITH --------------------------------


@pytest.mark.parametrize(
    "licence",
    [
        # Code review: the old split-based parser let these through.
        "GPL-3.0 AND (MIT OR Apache-2.0)",
        "LGPL-3.0-or-later AND (MIT OR Apache-2.0)",
        "(GPL-3.0 OR LGPL-2.1) AND MIT",
        "MIT AND (Apache-2.0 AND AGPL-3.0-only)",
        "((MIT OR Apache-2.0) AND GPL-2.0-only)",
        "GPL-2.0-or-later WITH Classpath-exception-2.0",
        "GPL-3.0 WITH GCC-exception-3.1",
    ],
)
def test_copyleft_cannot_hide_behind_parentheses_or_exceptions(licence):
    assert classify(licence)[0] == "denied", licence


@pytest.mark.parametrize(
    "licence",
    [
        "(MIT OR Apache-2.0) AND BSD-3-Clause",
        "MIT OR (GPL-3.0 AND LGPL-2.1)",  # a permissive alternative exists
        "Apache-2.0 WITH LLVM-exception",
        "GPL-3.0 WITH GCC-exception-3.1 OR MIT",
    ],
)
def test_parenthesised_permissive_expressions_pass(licence):
    assert classify(licence)[0] == "ok", licence


@pytest.mark.parametrize(
    "licence", ["(MIT", "MIT AND", "OR MIT", "MIT ) (", "LicenseRef-Proprietary"]
)
def test_malformed_or_proprietary_expressions_are_not_ok(licence):
    assert classify(licence)[0] == "unknown", licence


def test_free_text_mentioning_copyleft_is_denied_even_with_an_alternative():
    # Free text has no reliable OR; a copyleft mention needs a human review entry.
    assert (
        classify("Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License")[0]
        == "denied"
    )
    assert classify("MIT or LGPL, at your option")[0] == "denied"


# --- REVIEWED version ranges and classifier precedence --------------------------


def test_reviewed_entry_outside_its_version_range_needs_review_again():
    inside = check_licenses._classify_component(
        "python", "matplotlib", "3.11.2", "free text"
    )
    outside = check_licenses._classify_component(
        "python", "matplotlib", "4.0.0", "free text"
    )
    assert inside.verdict == "ok"
    assert outside.verdict == "unknown"
    assert "re-check the licence" in outside.detail


def test_copyleft_classifier_overrides_a_permissive_licence_field():
    finding = check_licenses._classify_component(
        "python",
        "sneaky",
        "1.0",
        "MIT",
        ["License :: OSI Approved :: GNU General Public License v3 (GPLv3)"],
    )
    assert finding.verdict == "denied"


def test_dual_licence_listing_gpl_classifier_passes_when_expression_offers_it():
    finding = check_licenses._classify_component(
        "python",
        "dual",
        "1.0",
        "BSD-3-Clause OR GPL-2.0",
        ["License :: OSI Approved :: GNU General Public License v2 (GPLv2)"],
    )
    assert finding.verdict == "ok"


# --- npm production-only scanning -----------------------------------------------


def test_npm_production_only_skips_dev_entries(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "packages": {
                    "": {"name": "ext"},
                    "node_modules/runtime": {"version": "1.0.0", "license": "MIT"},
                    "node_modules/packager": {
                        "version": "1.0.0",
                        "license": "SEE LICENSE IN LICENSE.txt",
                        "dev": True,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    full = {f.name: f.verdict for f in check_licenses.scan_npm(lock)}
    prod = {
        f.name: f.verdict for f in check_licenses.scan_npm(lock, production_only=True)
    }
    assert full == {"runtime": "ok", "packager": "unknown"}
    assert prod == {"runtime": "ok"}
