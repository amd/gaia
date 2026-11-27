# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Downloading gaiacpp from a release: size, byte progress, checksum, version."""

import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

import gaia.config as config_mod
from gaia.daemon.sidecars import fetch, gaiacpp_fetch
from gaia.daemon.sidecars.errors import IntegrityError, PlatformError
from gaia.ui.routers import agent_programs
from gaia.ui.server import create_app

PAYLOAD = b"gaiacpp-binary " * 20000  # ~300 KB: several chunks


@pytest.fixture(autouse=True)
def _own_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        gaiacpp_fetch.plat, "current_platform_key", lambda *a, **k: "win32-x64"
    )
    agent_programs._sizes.update(at=None, values={}, reasons={})
    agent_programs._job.update(state="idle", bytes_done=0, bytes_total=None, error=None)


@pytest.fixture
def release(tmp_path, monkeypatch):
    """A folder laid out like a release's gaiacpp assets."""
    folder = tmp_path / "release"
    folder.mkdir()
    name = gaiacpp_fetch.ASSETS["win32-x64"]
    (folder / name).write_bytes(PAYLOAD)
    sha = hashlib.sha256(PAYLOAD).hexdigest()
    (folder / gaiacpp_fetch.SUMS_NAME).write_text(
        f"{'0' * 64}  gaiacpp-linux-x64\n{sha}  {name}\n", encoding="utf-8"
    )
    monkeypatch.setenv(gaiacpp_fetch.ASSETS_URL_ENV, folder.as_uri())
    monkeypatch.setattr(fetch, "native_flagship_stamp", lambda path: "0.26.1")
    return folder


def _target(tmp_path):
    return tmp_path / "home" / "bin" / fetch.native_flagship_executable_name()


def _place(target, data):
    """Put a gaiacpp where it is looked for, executable as an install leaves it."""
    target.write_bytes(data)
    target.chmod(0o755)


# ---------------------------------------------------------------- which release


@pytest.mark.parametrize(
    "core, tag, stamp",
    [
        ("0.26.1", "v0.26.1", "0.26.1"),
        ("0.26.0rc3", "v0.26.0-rc3", "0.26.0-rc.3"),
    ],
)
def test_the_release_and_the_version_its_binary_prints(core, tag, stamp):
    assert gaiacpp_fetch.release_tag(core) == tag
    assert gaiacpp_fetch.stamped_version(tag) == stamp


def test_a_development_build_has_no_release_and_says_what_to_do():
    with pytest.raises(PlatformError) as e:
        gaiacpp_fetch.release_tag("0.26.1.dev3")
    assert "development build" in str(e.value)
    assert gaiacpp_fetch.ASSETS_URL_ENV in str(e.value)


def test_a_pc_gaiacpp_is_not_built_for_is_told_to_use_the_python_agent():
    with pytest.raises(PlatformError) as e:
        gaiacpp_fetch.asset_name("win32-arm64")
    assert "Python agent" in str(e.value)


def test_a_plain_http_asset_folder_is_refused(monkeypatch):
    monkeypatch.setenv(gaiacpp_fetch.ASSETS_URL_ENV, "http://example.com/x")
    with pytest.raises(PlatformError) as e:
        gaiacpp_fetch.assets_base_url("v0.26.1")
    assert "https://" in str(e.value)


# ---------------------------------------------------------------- size and checksum


def test_the_size_and_checksum_are_known_before_downloading(release):
    info = gaiacpp_fetch.download_info(core_version="0.26.1")
    assert info.size == len(PAYLOAD)
    assert info.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert info.url.endswith("gaiacpp-windows-x64.exe")
    assert info.version == "0.26.1"


def test_a_checksum_list_without_this_binary_is_refused(release):
    (release / gaiacpp_fetch.SUMS_NAME).write_text("", encoding="utf-8")
    with pytest.raises(IntegrityError) as e:
        gaiacpp_fetch.download_info(core_version="0.26.1")
    assert "0 checksums" in str(e.value)


def test_a_checksum_list_naming_it_twice_is_refused(release):
    name = gaiacpp_fetch.ASSETS["win32-x64"]
    (release / gaiacpp_fetch.SUMS_NAME).write_text(
        f"{'a' * 64}  {name}\n{'b' * 64}  {name}\n", encoding="utf-8"
    )
    with pytest.raises(IntegrityError):
        gaiacpp_fetch.download_info(core_version="0.26.1")


# ---------------------------------------------------------------- the download


def test_the_download_reports_bytes_and_installs_the_verified_binary(release, tmp_path):
    seen = []
    path = gaiacpp_fetch.download(
        lambda done, total: seen.append((done, total)), core_version="0.26.1"
    )
    assert path == _target(tmp_path)
    assert path.read_bytes() == PAYLOAD
    assert seen[0] == (0, len(PAYLOAD))
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert len(seen) > 3, "progress arrives as bytes do, not once at the end"
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    record = json.loads(
        (path.parent / gaiacpp_fetch.INSTALL_RECORD_NAME).read_text(encoding="utf-8")
    )
    assert record == {
        "version": "0.26.1",
        "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        "tag": "v0.26.1",
    }
    assert fetch.native_flagship() == path


def test_a_binary_that_does_not_match_its_checksum_is_not_installed(release, tmp_path):
    (release / gaiacpp_fetch.ASSETS["win32-x64"]).write_bytes(PAYLOAD + b"tampered")
    with pytest.raises(IntegrityError) as e:
        gaiacpp_fetch.download(core_version="0.26.1")
    assert "SHA-256 mismatch" in str(e.value)
    assert not _target(tmp_path).exists()
    assert list(_target(tmp_path).parent.glob("gaiacpp-part-*")) == []


def test_a_binary_from_another_version_is_not_installed(release, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "native_flagship_stamp", lambda path: "0.1.0")
    with pytest.raises(PlatformError) as e:
        gaiacpp_fetch.download(core_version="0.26.1")
    assert "reports version 0.1.0, not 0.26.1" in str(e.value)
    assert not _target(tmp_path).exists()


def test_a_cancelled_download_leaves_nothing_behind(release, tmp_path):
    with pytest.raises(RuntimeError) as e:
        gaiacpp_fetch.download(core_version="0.26.1", cancelled=lambda: True)
    assert "cancelled" in str(e.value)
    assert not _target(tmp_path).exists()
    assert list(_target(tmp_path).parent.glob("gaiacpp-part-*")) == []


# ---------------------------------------------------------------- the routes


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(agent_programs, "_gaiapy_size", lambda: 163381945)
    monkeypatch.setattr(gaiacpp_fetch, "release_tag", lambda core=None: "v0.26.1")
    return TestClient(
        create_app(db_path=":memory:"),
        client=("127.0.0.1", 50000),
        headers={"X-Gaia-UI": "1"},
    )


def _program(body, program_id):
    return next(p for p in body["programs"] if p["id"] == program_id)


def test_the_listing_gives_each_agents_size_and_whether_it_is_here(client, release):
    body = client.get("/api/agent-programs").json()
    assert body["current"] == "gaiapy"
    assert _program(body, "gaiapy")["size_bytes"] == 163381945
    gaiacpp = _program(body, "gaiacpp")
    assert gaiacpp["installed"] is False
    assert gaiacpp["size_bytes"] == len(PAYLOAD)
    assert gaiacpp["can_download"] is True
    assert gaiacpp["unavailable_reason"] is None


def test_a_release_without_gaiacpp_says_why_it_cannot_be_downloaded(
    client, tmp_path, monkeypatch
):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(gaiacpp_fetch.ASSETS_URL_ENV, empty.as_uri())
    gaiacpp = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    assert gaiacpp["can_download"] is False
    assert gaiacpp["size_bytes"] is None
    assert "has no gaiacpp to download" in gaiacpp["unavailable_reason"]
    assert gaiacpp["unavailable_code"] == "no_release"


class _Answers:
    """A stand-in for requests: every GET answers *status*, or raises."""

    def __init__(self, status=None, raises=None):
        self.status_code = status
        self.text = ""
        self._raises = raises

    def get(self, url, timeout=None):
        if self._raises:
            raise self._raises
        return self


def test_the_reason_shown_for_a_release_without_gaiacpp_has_no_address(
    client, monkeypatch
):
    monkeypatch.delenv(gaiacpp_fetch.ASSETS_URL_ENV, raising=False)
    info = gaiacpp_fetch.download_info
    monkeypatch.setattr(
        gaiacpp_fetch, "download_info", lambda: info(_Answers(status=404))
    )
    gaiacpp = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    assert gaiacpp["can_download"] is False
    assert gaiacpp["unavailable_reason"] == (
        "this GAIA release has no gaiacpp to download. Use the Python agent, "
        "or update GAIA."
    )
    assert gaiacpp["unavailable_code"] == "no_release"


def test_the_reason_shown_when_the_release_cannot_be_reached_is_plain(
    client, monkeypatch
):
    import requests

    monkeypatch.delenv(gaiacpp_fetch.ASSETS_URL_ENV, raising=False)
    info = gaiacpp_fetch.download_info
    down = _Answers(raises=requests.ConnectionError("getaddrinfo failed"))
    monkeypatch.setattr(gaiacpp_fetch, "download_info", lambda: info(down))
    gaiacpp = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    reason = gaiacpp["unavailable_reason"]
    assert "internet connection" in reason
    assert "http" not in reason and "getaddrinfo" not in reason
    assert gaiacpp["unavailable_code"] == "unreachable"


def test_a_server_that_answers_with_an_error_is_not_blamed_on_the_connection_alone(
    client, monkeypatch
):
    monkeypatch.delenv(gaiacpp_fetch.ASSETS_URL_ENV, raising=False)
    info = gaiacpp_fetch.download_info
    monkeypatch.setattr(
        gaiacpp_fetch, "download_info", lambda: info(_Answers(status=503))
    )
    gaiacpp = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    assert "try again later" in gaiacpp["unavailable_reason"]
    assert "503" not in gaiacpp["unavailable_reason"]
    assert gaiacpp["unavailable_code"] == "unreachable"


def test_the_reason_shown_for_an_unverifiable_release_has_no_address(client, release):
    (release / gaiacpp_fetch.SUMS_NAME).write_text("", encoding="utf-8")
    gaiacpp = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    reason = gaiacpp["unavailable_reason"]
    assert "can't be verified" in reason
    assert "file://" not in reason
    assert gaiacpp["unavailable_code"] == "unverifiable"


def test_a_hub_that_cannot_be_reached_leaves_the_size_unknown(client, monkeypatch):
    def _down():
        raise RuntimeError("hub unreachable")

    monkeypatch.setattr(agent_programs, "_gaiapy_size", _down)
    body = client.get("/api/agent-programs").json()
    assert _program(body, "gaiapy")["size_bytes"] is None


def _wait(client, states, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/api/agent-programs/gaiacpp/download").json()
        if status["state"] in states:
            return status
        time.sleep(0.05)
    raise AssertionError(f"download never reached {states}: {status}")


def test_downloading_through_the_api_installs_it_and_then_it_can_be_chosen(
    client, release, tmp_path, monkeypatch
):
    from gaia.ui.sidecar import daemon_client

    monkeypatch.setattr(daemon_client, "stop_sidecar", lambda agent_id="email": None)
    refused = client.put("/api/settings", json={"flagship_agent": "gaiacpp"})
    assert refused.status_code == 422

    started = client.post("/api/agent-programs/gaiacpp/download")
    assert started.status_code == 202
    done = _wait(client, {"done", "failed"})
    assert done["state"] == "done", done
    assert done["bytes_done"] == done["bytes_total"] == len(PAYLOAD)
    assert _target(tmp_path).read_bytes() == PAYLOAD

    listing = client.get("/api/agent-programs").json()
    assert _program(listing, "gaiacpp")["installed"] is True
    chosen = client.put("/api/settings", json={"flagship_agent": "gaiacpp"})
    assert chosen.status_code == 200 and chosen.json()["flagship_agent"] == "gaiacpp"


def test_a_failed_download_reports_the_reason(client, release):
    (release / gaiacpp_fetch.ASSETS["win32-x64"]).write_bytes(b"corrupt")
    client.post("/api/agent-programs/gaiacpp/download")
    failed = _wait(client, {"done", "failed"})
    assert failed["state"] == "failed"
    assert "SHA-256 mismatch" in failed["error"]


def test_gaiacpp_already_here_is_not_downloaded_again(client, release, tmp_path):
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    _place(target, PAYLOAD)
    r = client.post("/api/agent-programs/gaiacpp/download")
    assert r.status_code == 409


def test_a_gaiacpp_that_fails_its_check_can_be_downloaded_again(
    client, release, tmp_path
):
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    _place(target, b"changed after download")
    (target.parent / gaiacpp_fetch.INSTALL_RECORD_NAME).write_text(
        json.dumps(
            {
                "version": "0.26.1",
                "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                "tag": "v0.26.1",
            }
        )
    )
    gaiacpp = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    assert gaiacpp["installed"] is True and gaiacpp["can_download"] is True
    assert gaiacpp["update_available"] is False
    assert client.post("/api/agent-programs/gaiacpp/download").status_code == 202
    assert _wait(client, {"done", "failed"})["state"] == "done"
    assert fetch.verified_native_flagship() == target


def test_only_gaiacpp_is_downloaded_here(client):
    r = client.post("/api/agent-programs/gaiapy/download")
    assert r.status_code == 404
    assert "setup" in r.json()["detail"]


# ---------------------------------------------------------------- review follow-ups


def test_a_release_candidates_binary_passes_its_version_check(
    release, tmp_path, monkeypatch
):
    monkeypatch.setattr(fetch, "native_flagship_stamp", lambda path: "0.26.0-rc.3")
    path = gaiacpp_fetch.download(core_version="0.26.0rc3")
    assert path == _target(tmp_path)


def test_parts_left_by_an_interrupted_download_are_cleared(release, tmp_path):
    stale = _target(tmp_path).parent / "gaiacpp-part-old.exe"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"half a download")
    gaiacpp_fetch.download(core_version="0.26.1")
    assert not stale.exists()


def test_a_network_failure_is_asked_again_and_a_missing_build_is_remembered(
    client, monkeypatch
):
    calls = []

    def _unreachable():
        calls.append("x")
        raise RuntimeError("could not reach the release")

    # A PC booted moments ago: its monotonic clock is still small.
    monkeypatch.setattr(agent_programs.time, "monotonic", lambda: 5.0)
    monkeypatch.setattr(gaiacpp_fetch, "download_info", _unreachable)
    client.get("/api/agent-programs")
    client.get("/api/agent-programs")
    assert len(calls) == 2, "a network failure is not cached"

    calls.clear()

    def _no_build():
        calls.append("x")
        raise PlatformError("gaiacpp is not built for this kind of PC")

    monkeypatch.setattr(gaiacpp_fetch, "download_info", _no_build)
    client.get("/api/agent-programs")
    client.get("/api/agent-programs")
    assert len(calls) == 1, "a release that has no build is remembered"


def test_the_python_agent_setup_installed_from_the_hub_counts_as_installed(
    monkeypatch,
):
    monkeypatch.setattr(agent_programs.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(fetch, "installer_placed_flagship", lambda: None)
    monkeypatch.setattr(fetch, "_hub_installed_binary", lambda cache, key: object())
    assert agent_programs._gaiapy_installed() is True
    monkeypatch.setattr(fetch, "_hub_installed_binary", lambda cache, key: None)
    assert agent_programs._gaiapy_installed() is False


def test_a_gaiacpp_from_an_older_release_can_be_downloaded_again(
    client, release, tmp_path
):
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    _place(target, b"last release")
    (target.parent / gaiacpp_fetch.INSTALL_RECORD_NAME).write_text(
        json.dumps({"version": "0.25.9", "sha256": "0" * 64, "tag": "v0.25.9"})
    )
    listing = client.get("/api/agent-programs").json()
    gaiacpp = _program(listing, "gaiacpp")
    assert gaiacpp["installed"] is True and gaiacpp["update_available"] is True
    assert client.post("/api/agent-programs/gaiacpp/download").status_code == 202
    assert _wait(client, {"done", "failed"})["state"] == "done"
    assert target.read_bytes() == PAYLOAD


def test_a_download_cannot_be_started_from_another_computer(release):
    remote = TestClient(create_app(db_path=":memory:"), headers={"X-Gaia-UI": "1"})
    assert remote.post("/api/agent-programs/gaiacpp/download").status_code == 403


def test_a_failed_update_leaves_the_working_copy_and_its_record_alone(
    release, tmp_path, monkeypatch
):
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    _place(target, b"the copy in use")
    old = {
        "version": "0.25.9",
        "sha256": hashlib.sha256(b"the copy in use").hexdigest(),
        "tag": "v0.25.9",
    }
    record = target.parent / gaiacpp_fetch.INSTALL_RECORD_NAME
    record.write_text(json.dumps(old), encoding="utf-8")

    real_replace = gaiacpp_fetch.os.replace

    def _locked(src, dst):
        if str(dst) == str(target):
            raise PermissionError("the file is in use")
        return real_replace(src, dst)

    monkeypatch.setattr(gaiacpp_fetch.os, "replace", _locked)
    with pytest.raises(RuntimeError) as e:
        gaiacpp_fetch.download(core_version="0.26.1")
    assert "in use" in str(e.value)
    assert target.read_bytes() == b"the copy in use"
    assert json.loads(record.read_text(encoding="utf-8")) == old
    monkeypatch.setattr(fetch, "native_flagship_candidates", lambda: [target])
    assert (
        fetch.verified_native_flagship() == target
    ), "the old copy still passes its check"
    assert list(target.parent.glob("gaiacpp-part-*")) == []


def _older_gaiacpp(tmp_path):
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    _place(target, b"last release")
    (target.parent / gaiacpp_fetch.INSTALL_RECORD_NAME).write_text(
        json.dumps({"version": "0.25.9", "sha256": "0" * 64, "tag": "v0.25.9"})
    )
    return target


def test_updating_the_agent_in_use_is_refused_while_a_chat_is_answering(
    client, release, tmp_path, monkeypatch
):
    from gaia.config import GaiaConfig
    from gaia.ui.run_manager import run_manager

    target = _older_gaiacpp(tmp_path)
    GaiaConfig.save_key("flagship_agent", "gaiacpp")
    monkeypatch.setattr(run_manager, "active_sessions", lambda: ["chat-1"])
    r = client.post("/api/agent-programs/gaiacpp/download")
    assert r.status_code == 409
    assert "answering" in r.json()["detail"]
    assert target.read_bytes() == b"last release"


def test_updating_the_agent_in_use_is_refused_while_setup_is_testing(
    client, release, tmp_path, monkeypatch
):
    from gaia.config import GaiaConfig
    from gaia.ui.setup_runner import runner

    target = _older_gaiacpp(tmp_path)
    GaiaConfig.save_key("flagship_agent", "gaiacpp")
    monkeypatch.setattr(runner, "status", lambda: {"state": "verifying"})
    r = client.post("/api/agent-programs/gaiacpp/download")
    assert r.status_code == 409
    assert "setting up" in r.json()["detail"]
    assert target.read_bytes() == b"last release"


def test_an_update_of_an_agent_not_in_use_goes_ahead_during_a_chat(
    client, release, tmp_path, monkeypatch
):
    from gaia.ui.run_manager import run_manager

    target = _older_gaiacpp(tmp_path)
    monkeypatch.setattr(run_manager, "active_sessions", lambda: ["chat-1"])
    assert client.post("/api/agent-programs/gaiacpp/download").status_code == 202
    assert _wait(client, {"done", "failed"})["state"] == "done"
    assert target.read_bytes() == PAYLOAD


# ------------------------------------------------- recovery: adversarial cases


def _broken_copy(tmp_path):
    """A gaiacpp whose bytes no longer match the record its download wrote."""
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    _place(target, b"changed after download")
    record = {
        "version": "0.26.1",
        "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        "tag": "v0.26.1",
    }
    record_path = target.parent / gaiacpp_fetch.INSTALL_RECORD_NAME
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return target, record_path


def test_a_tampered_release_never_blesses_a_broken_copy(client, release, tmp_path):
    target, record_path = _broken_copy(tmp_path)
    record_before = record_path.read_text(encoding="utf-8")
    (release / gaiacpp_fetch.ASSETS["win32-x64"]).write_bytes(b"also tampered")
    assert client.post("/api/agent-programs/gaiacpp/download").status_code == 202
    failed = _wait(client, {"done", "failed"})
    assert failed["state"] == "failed" and "SHA-256 mismatch" in failed["error"]
    assert target.read_bytes() == b"changed after download"
    assert record_path.read_text(encoding="utf-8") == record_before
    with pytest.raises(IntegrityError):
        fetch.verified_native_flagship()


def test_a_broken_copy_on_a_release_without_gaiacpp_says_why_with_its_code(
    client, tmp_path, monkeypatch
):
    _broken_copy(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(gaiacpp_fetch.ASSETS_URL_ENV, empty.as_uri())
    gaiacpp = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    assert gaiacpp["installed"] is True and gaiacpp["can_download"] is False
    assert "has no gaiacpp to download" in gaiacpp["unavailable_reason"]
    assert gaiacpp["unavailable_code"] == "no_release"


def test_a_working_copy_carries_no_unavailable_reason_or_code(
    client, tmp_path, monkeypatch
):
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    _place(target, PAYLOAD)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(gaiacpp_fetch.ASSETS_URL_ENV, empty.as_uri())
    gaiacpp = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    assert gaiacpp["unavailable_reason"] is None
    assert gaiacpp["unavailable_code"] is None


def test_a_part_left_by_a_killed_download_is_never_run_as_gaiacpp(release, tmp_path):
    from gaia.config import GaiaConfig
    from gaia.ui.routers.agent_programs import GAIACPP_MISSING, chosen_agent_problem

    bin_dir = _target(tmp_path).parent
    bin_dir.mkdir(parents=True)
    _place(bin_dir / f"gaiacpp-part-x1{_target(tmp_path).suffix}", PAYLOAD)
    (bin_dir / "gaiacpp-part-record.json").write_text("{}", encoding="utf-8")
    GaiaConfig.save_key("flagship_agent", "gaiacpp")
    assert fetch.native_flagship() is None
    assert chosen_agent_problem()["code"] == GAIACPP_MISSING
    gaiacpp_fetch.download(core_version="0.26.1")
    assert list(bin_dir.glob("gaiacpp-part-*")) == []
    assert fetch.verified_native_flagship() == _target(tmp_path)


def test_a_download_cut_off_mid_stream_installs_nothing(release, tmp_path, monkeypatch):
    import requests

    def cut_off(url, session):
        def body():
            yield PAYLOAD[:1000]
            raise requests.ConnectionError("connection reset")

        return len(PAYLOAD), body()

    monkeypatch.setattr(gaiacpp_fetch, "_stream", cut_off)
    with pytest.raises(RuntimeError, match="stopped after 1000 bytes"):
        gaiacpp_fetch.download(core_version="0.26.1")
    bin_dir = _target(tmp_path).parent
    assert not _target(tmp_path).exists()
    assert list(bin_dir.glob("gaiacpp*")) == []
    assert not (bin_dir / gaiacpp_fetch.INSTALL_RECORD_NAME).exists()


def test_the_binary_is_not_run_for_its_version_until_its_checksum_matches(
    release, tmp_path, monkeypatch
):
    ran = []
    monkeypatch.setattr(fetch, "native_flagship_stamp", lambda p: ran.append(p))
    (release / gaiacpp_fetch.ASSETS["win32-x64"]).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        gaiacpp_fetch.download(core_version="0.26.1")
    assert ran == []


@pytest.mark.parametrize(
    "header", ["X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto"]
)
def test_a_download_over_mobile_access_is_refused(client, release, tmp_path, header):
    r = client.post(
        "/api/agent-programs/gaiacpp/download", headers={header: "phone.example"}
    )
    assert r.status_code == 403
    assert not _target(tmp_path).exists()
    assert agent_programs._job["state"] == "idle"


# ---------------------------------------------------------------- a copy from another release


def _older_copy_on_path(tmp_path, monkeypatch, printed="0.26.0-rc.8"):
    """A gaiacpp an older installer left on PATH: no download record beside it."""
    old = tmp_path / "usr-local-bin" / fetch.native_flagship_executable_name()
    old.parent.mkdir()
    _place(old, b"an older release's gaiacpp")
    monkeypatch.setattr(
        fetch,
        "native_flagship_candidates",
        lambda: [_target(tmp_path), old],
    )
    monkeypatch.setattr(
        fetch,
        "native_flagship_stamp",
        lambda path: printed if path == old else "0.26.1",
    )
    return old


def test_a_gaiacpp_from_an_older_install_with_no_record_is_out_of_date(
    client, release, tmp_path, monkeypatch
):
    """The rc8 .pkg's /usr/local/bin/gaiacpp under an rc10 app ran with no notice."""
    old = _older_copy_on_path(tmp_path, monkeypatch)
    gaiacpp = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    assert gaiacpp["installed"] is True
    assert gaiacpp["update_available"] is True and gaiacpp["can_download"] is True
    assert gaiacpp["version"] == "0.26.0-rc.8"
    assert gaiacpp["release_version"] == "0.26.1"

    assert client.post("/api/agent-programs/gaiacpp/download").status_code == 202
    assert _wait(client, {"done", "failed"})["state"] == "done"
    # This release's copy lands in GAIA's bin dir, which is looked in first.
    assert fetch.native_flagship() == _target(tmp_path)
    assert old.read_bytes() == b"an older release's gaiacpp"
    after = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    assert after["update_available"] is False and after["version"] == "0.26.1"


def test_a_recordless_gaiacpp_of_this_release_is_not_flagged(
    client, release, tmp_path, monkeypatch
):
    _older_copy_on_path(tmp_path, monkeypatch, printed="0.26.1")
    gaiacpp = _program(client.get("/api/agent-programs").json(), "gaiacpp")
    assert gaiacpp["update_available"] is False and gaiacpp["can_download"] is False
    assert client.post("/api/agent-programs/gaiacpp/download").status_code == 409


def test_a_development_build_of_gaia_never_calls_gaiacpp_out_of_date(
    tmp_path, monkeypatch
):
    """No release to compare with: the copy runs as it is, unflagged."""
    old = _older_copy_on_path(tmp_path, monkeypatch)

    def _dev(core=None):
        raise PlatformError("GAIA 0.27.0.dev1 is a development build")

    monkeypatch.setattr(gaiacpp_fetch, "release_tag", _dev)
    assert gaiacpp_fetch.release_version() is None
    assert gaiacpp_fetch.update_available(old) is False


def test_a_build_named_by_the_environment_is_never_out_of_date(tmp_path, monkeypatch):
    old = _older_copy_on_path(tmp_path, monkeypatch)
    monkeypatch.setattr(gaiacpp_fetch, "release_tag", lambda core=None: "v0.26.1")
    assert gaiacpp_fetch.update_available(old) is True
    monkeypatch.setenv(fetch.NATIVE_FLAGSHIP_PATH_ENV, str(old))
    assert gaiacpp_fetch.update_available(old) is False


def test_the_version_a_copy_prints_is_asked_once_per_file(tmp_path, monkeypatch):
    old = _older_copy_on_path(tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(
        fetch,
        "native_flagship_stamp",
        lambda path: asked.append(path) or "0.26.0-rc.8",
    )
    monkeypatch.setattr(gaiacpp_fetch, "release_tag", lambda core=None: "v0.26.1")
    assert gaiacpp_fetch.update_available(old) is True
    assert gaiacpp_fetch.update_available(old) is True
    assert asked == [old]


def test_a_copy_that_cannot_say_its_version_is_not_flagged(tmp_path, monkeypatch):
    old = _older_copy_on_path(tmp_path, monkeypatch)

    def _silent(path):
        raise PlatformError(f"{path} --version did not print a version")

    monkeypatch.setattr(fetch, "native_flagship_stamp", _silent)
    monkeypatch.setattr(gaiacpp_fetch, "release_tag", lambda core=None: "v0.26.1")
    assert gaiacpp_fetch.update_available(old) is False
    assert agent_programs._installed_version(old) is None


def test_a_copy_whose_version_fails_is_run_once_per_file(tmp_path, monkeypatch):
    """A hung --version costs up to 20 s; a listing must not pay it twice."""
    old = _older_copy_on_path(tmp_path, monkeypatch)
    asked = []

    def _silent(path):
        asked.append(path)
        raise PlatformError(f"{path} --version did not print a version")

    monkeypatch.setattr(fetch, "native_flagship_stamp", _silent)
    monkeypatch.setattr(gaiacpp_fetch, "release_tag", lambda core=None: "v0.26.1")
    assert gaiacpp_fetch.update_available(old) is False
    assert agent_programs._installed_version(old) is None
    with pytest.raises(PlatformError, match="did not print a version"):
        gaiacpp_fetch.installed_version(old)
    assert asked == [old]
