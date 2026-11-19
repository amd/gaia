# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""installer/headless/hub_mirror.py: what the setup journey serves as the hub."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[2]
VERSION = "0.3.6"
PLATFORM = "linux-x64"


def _module():
    path = REPO / "installer" / "headless" / "hub_mirror.py"
    spec = importlib.util.spec_from_file_location("hub_mirror", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hub_mirror = _module()


def _artifact(name: str, body: bytes, sha: str = "") -> dict:
    return {
        "filename": name,
        "path": f"agents/gaia/{VERSION}/{name}",
        "size_bytes": len(body),
        "sha256": sha or hashlib.sha256(body).hexdigest(),
    }


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """A hub laid out like hub.amd-gaia.ai, read through file:// as GAIA_HUB_URL."""
    root = tmp_path / "hub"
    folder = root / "agents" / "gaia" / VERSION
    folder.mkdir(parents=True)
    binaries = {
        "gaia-agent-linux-x64": b"linux build",
        "gaia-agent-win32-x64.exe": b"windows build",
    }
    for name, body in binaries.items():
        (folder / name).write_bytes(body)
    (folder / "gaia-agent.yaml").write_bytes(b"id: gaia\n")
    artifacts = [_artifact(n, b) for n, b in binaries.items()]
    manifest = {
        "id": "gaia",
        "language": "python",
        "latest_version": VERSION,
        "min_gaia_version": "0.1.0",
        "versions": {
            VERSION: {
                "version": VERSION,
                "min_gaia_version": "0.1.0",
                "artifact": artifacts[0],
                "artifacts": artifacts,
            }
        },
    }
    (root / "agents" / "gaia" / "manifest.json").write_text(json.dumps(manifest))
    (root / "index.json").write_text(json.dumps({"agents": [{"id": "gaia"}]}))
    monkeypatch.setenv("GAIA_HUB_URL", root.as_uri())
    return root


def test_mirrors_this_machines_build_at_the_hubs_own_paths(hub, tmp_path):
    out = tmp_path / "mirror"
    artifact = hub_mirror.mirror(out, platform_key=PLATFORM, backoff=0)

    assert artifact == out / "agents" / "gaia" / VERSION / "gaia-agent-linux-x64"
    for rel in (
        "index.json",
        "agents/gaia/manifest.json",
        f"agents/gaia/{VERSION}/gaia-agent.yaml",
        f"agents/gaia/{VERSION}/gaia-agent-linux-x64",
    ):
        assert (out / rel).read_bytes() == (hub / rel).read_bytes(), rel
    # Only the build this runner installs: the others are 150 MB each.
    assert not (out / "agents" / "gaia" / VERSION / "gaia-agent-win32-x64.exe").exists()


def test_a_build_that_does_not_match_its_checksum_fails_the_mirror(hub, tmp_path):
    binary = hub / "agents" / "gaia" / VERSION / "gaia-agent-linux-x64"
    binary.write_bytes(b"truncated")

    with pytest.raises(SystemExit, match="sha256"):
        hub_mirror.mirror(tmp_path / "mirror", platform_key=PLATFORM, backoff=0)


def test_a_missing_agent_yaml_fails_rather_than_serving_a_partial_hub(hub, tmp_path):
    (hub / "agents" / "gaia" / VERSION / "gaia-agent.yaml").unlink()

    with pytest.raises(FileNotFoundError):
        hub_mirror.mirror(tmp_path / "mirror", platform_key=PLATFORM, backoff=0)


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status}", response=response)


@pytest.mark.parametrize(
    "transient",
    [
        requests.exceptions.ChunkedEncodingError("IncompleteRead(8523 bytes read)"),
        requests.exceptions.ReadTimeout("Read timed out. (read timeout=10)"),
        _http_error(503),
    ],
)
def test_a_dropped_or_slow_download_is_retried(transient):
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) < 3:
            raise transient
        return b"ok"

    assert hub_mirror.fetch_with_retries(fetch, "x", attempts=4, backoff=0) == b"ok"
    assert len(calls) == 3


def test_a_client_error_is_not_retried():
    calls = []

    def fetch():
        calls.append(1)
        raise _http_error(404)

    with pytest.raises(requests.HTTPError):
        hub_mirror.fetch_with_retries(fetch, "x", attempts=4, backoff=0)
    assert len(calls) == 1


def test_gives_up_with_the_last_error_after_every_attempt():
    def fetch():
        raise requests.exceptions.ConnectionError("hub down")

    with pytest.raises(requests.exceptions.ConnectionError, match="hub down"):
        hub_mirror.fetch_with_retries(fetch, "x", attempts=3, backoff=0)
