# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/npm_publish_once.py: re-running a release never double-publishes npm.

Same bytes already on the registry: skip. Different bytes: fail. Only a 404
means "not published"; any other registry answer is an error, never a guess.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import npm_publish_once as npo  # noqa: E402

LOCAL = "sha512-local"


def test_decide():
    assert npo.decide(LOCAL, None) == "publish"
    assert npo.decide(LOCAL, LOCAL) == "skip"
    assert npo.decide(LOCAL, "sha512-other") == "conflict"


def test_registry_lookup_hits_the_version_document():
    seen = []

    def fetch(url):
        seen.append(url)
        return 200, json.dumps({"dist": {"integrity": LOCAL}}).encode()

    assert npo.registry_integrity("@amd-gaia/agent-ui", "1.0.0-rc.1", fetch) == LOCAL
    assert seen == ["https://registry.npmjs.org/@amd-gaia%2fagent-ui/1.0.0-rc.1"]


def test_only_a_404_means_not_published():
    assert npo.registry_integrity("x", "1.0.0", lambda u: (404, b"")) is None
    for status in (401, 500, 503):
        with pytest.raises(RuntimeError, match=str(status)):
            npo.registry_integrity("x", "1.0.0", lambda u, s=status: (s, b"oops"))


def test_a_version_document_without_integrity_is_an_error():
    with pytest.raises(RuntimeError, match="no dist.integrity"):
        npo.registry_integrity("x", "1.0.0", lambda u: (200, b'{"dist": {}}'))


@pytest.fixture
def fake_npm(monkeypatch):
    """Stub `npm pack` and record any `npm publish`."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "pack":
            out = json.dumps([{"name": "pkg", "version": "1.0.0", "integrity": LOCAL}])
            return subprocess.CompletedProcess(cmd, 0, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(npo.subprocess, "run", fake_run)
    return calls


def _publishes(calls):
    return [c for c in calls if c[1] == "publish"]


def test_publishes_with_the_dist_tag_and_extra_args(fake_npm, monkeypatch, tmp_path):
    monkeypatch.setattr(npo, "http_get", lambda url: (404, b""))
    rc = npo.main(
        ["--dir", str(tmp_path), "--dist-tag", "next", "--", "--access", "public"]
    )
    assert rc == 0
    (pub,) = _publishes(fake_npm)
    assert pub[1:] == ["publish", "--tag", "next", "--access", "public"]


def test_skips_the_same_bytes(fake_npm, monkeypatch, tmp_path, capsys):
    body = json.dumps({"dist": {"integrity": LOCAL}}).encode()
    monkeypatch.setattr(npo, "http_get", lambda url: (200, body))
    assert npo.main(["--dir", str(tmp_path), "--dist-tag", "latest"]) == 0
    assert _publishes(fake_npm) == []
    assert "skipping" in capsys.readouterr().out


def test_fails_on_different_bytes(fake_npm, monkeypatch, tmp_path, capsys):
    body = json.dumps({"dist": {"integrity": "sha512-other"}}).encode()
    monkeypatch.setattr(npo, "http_get", lambda url: (200, body))
    assert npo.main(["--dir", str(tmp_path), "--dist-tag", "latest"]) == 1
    assert _publishes(fake_npm) == []
    assert "different bytes" in capsys.readouterr().out


def test_a_registry_error_never_publishes(fake_npm, monkeypatch, tmp_path):
    monkeypatch.setattr(npo, "http_get", lambda url: (503, b"down"))
    assert npo.main(["--dir", str(tmp_path), "--dist-tag", "latest"]) == 1
    assert _publishes(fake_npm) == []
