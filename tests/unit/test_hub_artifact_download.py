# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A dropped or stalled artifact download is retried, resumed when it can be,
and bounded by an overall deadline."""

import hashlib

import pytest
import requests

from gaia.hub import catalog, installer

URL = "https://hub.example/agents/gaia/gaia-linux-x64.bin"
PAYLOAD = bytes(range(256)) * 64


class _Resp:
    def __init__(self, status, chunks, headers=None, error=None):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, _size):
        yield from self._chunks
        if self._error is not None:
            raise self._error


def _full(data=PAYLOAD):
    return _Resp(200, [data], {"Content-Length": str(len(data))})


def _dropped(after):
    return _Resp(
        200,
        [PAYLOAD[:after]],
        {"Content-Length": str(len(PAYLOAD))},
        error=requests.exceptions.ChunkedEncodingError("IncompleteRead"),
    )


@pytest.fixture
def fake_get(monkeypatch):
    calls = []
    script = []

    def get(url, headers=None, stream=False, timeout=None):
        calls.append(dict(headers or {}))
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(requests, "get", get)
    return script, calls


def _download(**kw):
    kw.setdefault("sleep", lambda _s: None)
    return catalog.download_artifact(URL, **kw)


def test_resumes_from_offset_when_server_supports_range(fake_get):
    script, calls = fake_get
    script += [
        _dropped(1000),
        _Resp(
            206,
            [PAYLOAD[1000:]],
            {"Content-Range": f"bytes 1000-{len(PAYLOAD) - 1}/{len(PAYLOAD)}"},
        ),
    ]
    assert _download() == PAYLOAD
    assert "Range" not in calls[0]
    assert calls[1]["Range"] == "bytes=1000-"
    assert calls[1]["Accept-Encoding"] == "identity"


def test_restarts_cleanly_when_server_ignores_range(fake_get):
    script, calls = fake_get
    script += [_dropped(1000), _full()]
    assert _download() == PAYLOAD
    assert calls[1]["Range"] == "bytes=1000-"


def test_short_body_without_error_is_retried(fake_get):
    script, _ = fake_get
    script += [
        _Resp(200, [PAYLOAD[:10]], {"Content-Length": str(len(PAYLOAD))}),
        _full(),
    ]
    assert _download() == PAYLOAD


def test_timeouts_retry_with_backoff_then_fail_loudly(fake_get):
    script, calls = fake_get
    script += [requests.Timeout("read timed out")] * 3
    slept = []
    with pytest.raises(catalog.ArtifactDownloadError, match="3 time"):
        _download(attempts=3, sleep=slept.append, backoff_seconds=2.0)
    assert len(calls) == 3
    assert slept == [2.0, 4.0]


def test_server_errors_are_retried(fake_get):
    script, _ = fake_get
    script += [_Resp(503, []), _full()]
    assert _download() == PAYLOAD


def test_not_found_is_not_retried(fake_get):
    script, calls = fake_get
    script += [_Resp(404, [])]
    with pytest.raises(requests.HTTPError):
        _download()
    assert len(calls) == 1


def test_overall_deadline_stops_a_slow_download(fake_get):
    script, _ = fake_get
    now = [0.0]

    def clock():
        now[0] += 100.0
        return now[0]

    script += [_Resp(200, [b"x"] * 50, {"Content-Length": "50"})]
    with pytest.raises(catalog.ArtifactDownloadError, match="did not finish"):
        _download(deadline_seconds=300.0, clock=clock)


def test_installer_verifies_checksum_of_retried_download(fake_get):
    script, _ = fake_get
    script += [_dropped(500), _full()]
    sha = hashlib.sha256(PAYLOAD).hexdigest()
    assert installer._download_and_verify(URL, sha, catalog.fetch_bytes) == PAYLOAD


def test_installer_reports_exhausted_download_as_install_error(fake_get, monkeypatch):
    real = catalog.download_artifact
    monkeypatch.setattr(
        catalog, "download_artifact", lambda url: real(url, sleep=lambda _s: None)
    )
    script, calls = fake_get
    script += [requests.ConnectionError("reset")] * catalog.ARTIFACT_DOWNLOAD_ATTEMPTS
    with pytest.raises(installer.InstallError, match="internet connection"):
        installer._download_and_verify(URL, "0" * 64, catalog.fetch_bytes)
    assert len(calls) == catalog.ARTIFACT_DOWNLOAD_ATTEMPTS
