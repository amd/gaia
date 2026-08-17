# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The Python half of the cross-language digest contract (issue #2468).

``workers/agent-hub/test/audit-digest-vectors.test.ts`` asserts against the same
``vectors.json``. Two implementations of one hash is precisely the case where
each side gets verified only against its own mock — which proves the function was
called, never that the two interoperate. Shared vectors are what make the
contract real.

See ``tests/fixtures/skill_audit_digest/README.md`` before changing a digest.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gaia.skills.audit import AUDIT_ENGINE, content_digest, manifest_digest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "skill_audit_digest"


@pytest.fixture(scope="module")
def vectors() -> dict:
    return json.loads((FIXTURES / "vectors.json").read_text(encoding="utf-8"))


def test_the_vector_file_names_the_engine_that_produced_it(vectors):
    """A digest change is a wire-format change and must move the engine version."""
    assert vectors["engine"] == AUDIT_ENGINE


def test_manifest_digest_matches_the_vector(vectors):
    text = (FIXTURES / "tree" / "SKILL.md").read_text(encoding="utf-8")
    assert manifest_digest(text) == vectors["manifest_digest"]["skill_md_lf"]


def test_manifest_digest_normalizes_crlf_to_lf(vectors):
    """A Windows checkout must not produce a different digest."""
    crlf = (FIXTURES / "skill_md_crlf.txt").read_bytes().decode("utf-8")
    assert "\r\n" in crlf, "the CRLF fixture lost its line endings"
    assert (
        manifest_digest(crlf)
        == vectors["manifest_digest"]["skill_md_crlf_normalizes_to_the_same"]
    )
    assert manifest_digest(crlf) == vectors["manifest_digest"]["skill_md_lf"]


def test_manifest_digest_of_the_empty_string_is_plain_sha256(vectors):
    """Pins the algorithm itself: this is the well-known sha256 of ''."""
    assert vectors["manifest_digest"]["empty_string"] == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert manifest_digest("") == vectors["manifest_digest"]["empty_string"]


def test_content_digest_of_the_fixture_tree_matches_the_vector(vectors):
    assert content_digest(FIXTURES / "tree") == vectors["content_digest"]["tree"]


def test_content_digest_ignores_a_build_cache(tmp_path, vectors):
    """A __pycache__ appearing must not invalidate a valid audit report.

    The cache is created here rather than committed: ``**/__pycache__/`` is in
    .gitignore, so a committed one would be absent on a fresh clone and this
    assertion would silently test nothing.
    """
    tree = tmp_path / "tree"
    shutil.copytree(FIXTURES / "tree", tree)
    cache = tree / "__pycache__"
    cache.mkdir()
    (cache / "tools.cpython-312.pyc").write_bytes(b"\x00\x01\x02")

    assert content_digest(tree) == vectors["content_digest"]["tree"]


def test_content_digest_is_sensitive_to_a_nested_file(tmp_path, vectors):
    """Guards against the vector passing for the wrong reason."""
    tree = tmp_path / "tree"
    shutil.copytree(FIXTURES / "tree", tree)
    (tree / "scripts" / "run.py").write_text("print('changed')\n", encoding="utf-8")
    assert content_digest(tree) != vectors["content_digest"]["tree"]
