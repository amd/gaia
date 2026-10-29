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


ENTRY = {
    "id": "@amd-gaia/gaia@1.2.3",
    "name": "@amd-gaia/gaia",
    "version": "1.2.3",
    "integrity": LOCAL,
    "files": [{"path": "package.json", "size": 10, "mode": 420}],
}
PARSED = ("@amd-gaia/gaia", "1.2.3", LOCAL)


def test_reads_npm11_list_shape():
    assert npo.parse_pack_output(json.dumps([ENTRY])) == PARSED


def test_reads_npm12_object_shape():
    assert npo.parse_pack_output(json.dumps({ENTRY["name"]: ENTRY})) == PARSED


def test_reads_pretty_printed_output_with_trailing_newline():
    assert npo.parse_pack_output(json.dumps({"x": ENTRY}, indent=2) + "\n") == PARSED


@pytest.mark.parametrize(
    "out",
    [
        [ENTRY, dict(ENTRY, name="other")],
        {"a": ENTRY, "b": dict(ENTRY, name="other")},
    ],
    ids=["list", "object"],
)
def test_more_than_one_package_raises(out):
    with pytest.raises(ValueError, match="exactly one package, got 2"):
        npo.parse_pack_output(json.dumps(out))


@pytest.mark.parametrize("out", ["[]", "{}"])
def test_no_package_raises(out):
    with pytest.raises(ValueError, match="exactly one package, got 0"):
        npo.parse_pack_output(out)


@pytest.mark.parametrize(
    "out", ["", "   \n", "npm WARN something\n[]", "<html>", "[{]", "null x"]
)
def test_non_json_raises(out):
    with pytest.raises(ValueError, match="not JSON"):
        npo.parse_pack_output(out)


@pytest.mark.parametrize("out", ["null", "42", '"pkg"', "true"])
def test_json_that_is_not_a_list_or_object_raises(out):
    with pytest.raises(ValueError, match="list or object"):
        npo.parse_pack_output(out)


@pytest.mark.parametrize(
    "err",
    [
        {"error": {"code": "ENOENT", "summary": "no package.json", "detail": ""}},
        # An error payload must not pass as a package called "error", even
        # when its body happens to carry package-looking fields.
        {"error": dict(ENTRY)},
    ],
)
def test_npm_error_payload_raises(err):
    with pytest.raises(ValueError, match="npm reported an error"):
        npo.parse_pack_output(json.dumps(err))


@pytest.mark.parametrize(
    "entry",
    [None, "pkg", 1, [ENTRY]],
    ids=["null", "string", "number", "nested-list"],
)
def test_entry_that_is_not_an_object_raises(entry):
    for out in ([entry], {"pkg": entry}):
        with pytest.raises(ValueError, match="not an object"):
            npo.parse_pack_output(json.dumps(out))


@pytest.mark.parametrize("field", ["name", "version", "integrity"])
@pytest.mark.parametrize(
    "bad", [None, "", 0, ["x"]], ids=["null", "empty", "0", "list"]
)
def test_entry_with_a_missing_or_bad_field_raises(field, bad):
    broken = dict(ENTRY, **{field: bad})
    gone = {k: v for k, v in ENTRY.items() if k != field}
    for entry in (broken, gone):
        for out in ([entry], {"pkg": entry}):
            with pytest.raises(ValueError, match=f"lacks {field}"):
                npo.parse_pack_output(json.dumps(out))


def _stub_npm(monkeypatch, pack_out, version_rc=0):
    def fake_run(cmd, **kwargs):
        if cmd[1] == "--version":
            return subprocess.CompletedProcess(cmd, version_rc, "12.2.1\n", "")
        return subprocess.CompletedProcess(cmd, 0, pack_out, "")

    monkeypatch.setattr(npo.subprocess, "run", fake_run)


def test_local_integrity_reads_npm12(monkeypatch, tmp_path):
    _stub_npm(monkeypatch, json.dumps({ENTRY["name"]: ENTRY}))
    assert npo.local_integrity(tmp_path) == PARSED


def test_unreadable_pack_output_names_the_npm_version_and_output(monkeypatch, tmp_path):
    out = json.dumps({"a": ENTRY, "b": ENTRY}) + "x" * 1000
    _stub_npm(monkeypatch, json.dumps({"a": ENTRY, "b": ENTRY}))
    with pytest.raises(RuntimeError) as exc:
        npo.local_integrity(tmp_path)
    msg = str(exc.value)
    assert "npm 12.2.1" in msg
    assert "exactly one package, got 2" in msg
    assert '"a"' in msg

    _stub_npm(monkeypatch, out)
    with pytest.raises(RuntimeError) as exc:
        npo.local_integrity(tmp_path)
    assert repr(out[:300]) in str(exc.value)
    assert "x" * 301 not in str(exc.value)


def test_unreadable_pack_output_when_npm_version_also_fails(monkeypatch, tmp_path):
    _stub_npm(monkeypatch, "garbage", version_rc=1)
    with pytest.raises(RuntimeError, match=r"npm unknown \(npm --version exited 1\)"):
        npo.local_integrity(tmp_path)


def test_unreadable_pack_output_fails_main_without_publishing(
    monkeypatch, tmp_path, capsys
):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, "12.2.1\n", "")
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(npo.subprocess, "run", fake_run)
    monkeypatch.setattr(npo, "http_get", lambda url: (404, b""))
    assert npo.main(["--dir", str(tmp_path), "--dist-tag", "latest"]) == 1
    assert _publishes(calls) == []
    assert "::error::Could not read" in capsys.readouterr().out


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
