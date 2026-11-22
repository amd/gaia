# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The user's choice between the Python agent (gaiapy) and the native one (gaiacpp).

One key, ``flagship_agent`` in ``~/.gaia/config.json``, decides which program
the daemon starts as the GAIA agent and whether the Agent UI relays to it.
"""

import dataclasses
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gaia.config as config_mod
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.daemon.sidecars import fetch
from gaia.daemon.sidecars import manager as mgr
from gaia.daemon.sidecars.errors import (
    KIND_HARNESS_UNAVAILABLE,
    KIND_START_FAILED,
    SidecarError,
    SidecarSpawnError,
)
from gaia.daemon.sidecars.spec import builtin_specs
from gaia.harnesses import HarnessUnavailableError
from gaia.ui import _chat_helpers
from gaia.ui.models import ChatRequest
from gaia.ui.server import create_app


def _config_file() -> Path:
    return GaiaConfig.config_path()


def _choose(value) -> None:
    GaiaConfig.save_key("flagship_agent", value)


@pytest.fixture(autouse=True)
def _own_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")


@pytest.fixture
def fake_gaiacpp(tmp_path, monkeypatch):
    """A file standing where an installer would put gaiacpp."""
    binary = tmp_path / fetch.native_flagship_executable_name()
    binary.write_bytes(b"not a real program")
    if sys.platform != "win32":
        binary.chmod(0o755)
    monkeypatch.setattr(fetch, "native_flagship_candidates", lambda: [binary])
    return binary


@pytest.fixture
def no_gaiacpp(tmp_path, monkeypatch):
    missing = tmp_path / "nowhere" / fetch.native_flagship_executable_name()
    monkeypatch.setattr(fetch, "native_flagship_candidates", lambda: [missing])
    return missing


# ---------------------------------------------------------------- the setting


def test_nobody_chose_means_the_python_agent():
    assert GaiaConfig.load().flagship_agent is None
    assert fetch.chosen_flagship() == "gaiapy"


@pytest.mark.parametrize("choice", ["gaiapy", "gaiacpp"])
def test_a_choice_round_trips_through_the_config_file(choice):
    _choose(choice)
    saved = json.loads(_config_file().read_text(encoding="utf-8"))
    assert saved["flagship_agent"] == choice
    assert fetch.chosen_flagship() == choice


def test_gaia_config_set_accepts_the_two_names_and_refuses_others():
    cfg = GaiaConfig.load()
    cfg.set("flagship_agent", "GaiaCpp ")
    assert cfg.flagship_agent == "gaiacpp"
    with pytest.raises(GaiaConfigError) as e:
        cfg.set("flagship_agent", "rust")
    assert "gaiapy, gaiacpp" in str(e.value)


def _save_raw(value) -> Path:
    """Write flagship_agent straight into the file, as a newer GAIA might."""
    path = _config_file()
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc["flagship_agent"] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize("name", ["rust", "gaiacode"])
def test_a_harness_this_version_cannot_run_still_loads(name):
    # A config a newer GAIA wrote must never stop this one from starting.
    _save_raw(name)
    assert GaiaConfig.load().flagship_agent == name
    assert fetch.saved_flagship() == name


def test_starting_an_unknown_harness_says_to_choose_another_in_settings():
    _save_raw("rust")
    with pytest.raises(HarnessUnavailableError) as e:
        fetch.chosen_flagship()
    assert "'rust'" in str(e.value) and "Settings" in str(e.value)


def test_starting_gaiacode_before_this_version_can_run_it_says_so():
    _save_raw("gaiacode")
    with pytest.raises(HarnessUnavailableError) as e:
        fetch.chosen_flagship()
    assert "gaiacode" in str(e.value) and "isn't available in this version" in str(
        e.value
    )


def test_gaiacode_cannot_be_set_until_this_version_can_run_it():
    with pytest.raises(GaiaConfigError) as e:
        GaiaConfig.load().set("flagship_agent", "gaiacode")
    assert "gaiapy, gaiacpp" in str(e.value)


def test_a_malformed_value_in_the_file_is_an_error_naming_the_key():
    path = _save_raw("not a harness!")
    with pytest.raises(GaiaConfigError) as e:
        GaiaConfig.load()
    assert "flagship_agent" in str(e.value) and str(path) in str(e.value)


# ---------------------------------------------------------------- finding gaiacpp


def test_gaiacpp_is_found_where_an_installer_puts_it(fake_gaiacpp):
    assert fetch.native_flagship() == fake_gaiacpp


def test_gaiacpp_that_is_not_installed_is_none(no_gaiacpp):
    assert fetch.native_flagship() is None


def test_the_search_starts_in_gaias_bin_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    first = fetch.native_flagship_candidates()[0]
    assert first == tmp_path / "bin" / fetch.native_flagship_executable_name()


# ---------------------------------------------------------------- what the daemon starts


def _flagship_manager():
    return mgr.AgentSidecarManager(builtin_specs()["gaia"])


def test_the_daemon_starts_gaiacpp_when_it_is_chosen(fake_gaiacpp, monkeypatch):
    _choose("gaiacpp")
    monkeypatch.setattr(fetch, "native_flagship_version", lambda binary: "0.1.0")
    m = _flagship_manager()
    argv, kwargs = m.build_spawn_command(port=45123)
    assert argv == [str(fake_gaiacpp), "--host", m.host, "--port", "45123"]
    assert kwargs == {}
    assert m.installed_binary_version == "0.1.0"
    # Its token goes by file, as for any installed binary, not the dev-source way.
    assert m.resolved_mode == "user"
    assert m._resolve_secret_delivery().startswith("installed binary 0.1.0")


def test_a_chosen_gaiacpp_that_is_missing_fails_and_says_how_to_switch_back(
    no_gaiacpp,
):
    _choose("gaiacpp")
    with pytest.raises(SidecarSpawnError) as e:
        _flagship_manager().build_spawn_command(port=45123)
    text = str(e.value)
    assert "gaiacpp" in text and str(no_gaiacpp) in text
    assert "Settings › Agent & model" in text


def test_a_gaiacpp_that_cannot_report_its_version_is_not_started(fake_gaiacpp):
    _choose("gaiacpp")
    with pytest.raises(SidecarSpawnError) as e:
        _flagship_manager().build_spawn_command(port=45123)
    assert "--version" in str(e.value)


def test_the_python_agent_is_started_as_before(fake_gaiacpp, monkeypatch):
    _choose("gaiapy")
    monkeypatch.setenv("GAIA_GAIA_AGENT_MODE", "dev")
    argv, _ = _flagship_manager().build_spawn_command(port=45123)
    assert str(fake_gaiacpp) not in argv


def test_another_agent_is_not_affected_by_the_choice(fake_gaiacpp, monkeypatch):
    _choose("gaiacpp")
    other_spec = dataclasses.replace(
        builtin_specs()["gaia"], agent_id="other", mode_env_var="GAIA_OTHER_AGENT_MODE"
    )
    monkeypatch.setenv("GAIA_OTHER_AGENT_MODE", "dev")
    argv, _ = mgr.AgentSidecarManager(other_spec).build_spawn_command(port=45123)
    assert str(fake_gaiacpp) not in argv


# ---------------------------------------------------------------- chat routing


class _InProcessRegistry:
    """A wheel install: the flagship is importable and would run in-process."""

    class _Reg:
        is_sidecar = False

    def get(self, agent_type):
        return self._Reg()

    def get_load_error(self, agent_type):
        return None


def test_the_python_agent_still_runs_in_process_on_a_source_install():
    _choose("gaiapy")
    assert not _chat_helpers._should_relay_to_sidecar("gaia", _InProcessRegistry())


def test_gaiacpp_is_always_reached_through_the_daemon():
    _choose("gaiacpp")
    assert _chat_helpers._should_relay_to_sidecar("gaia", _InProcessRegistry())


def test_a_harness_this_version_cannot_run_is_not_answered_by_the_python_agent():
    # It relays, so the daemon's start reports why; gaiapy never answers in its place.
    _save_raw("gaiacode")
    assert _chat_helpers._should_relay_to_sidecar("gaia", _InProcessRegistry())


def test_the_daemon_refuses_to_start_a_harness_it_cannot_run():
    _save_raw("gaiacode")
    with pytest.raises(SidecarSpawnError) as e:
        _flagship_manager().build_spawn_command(port=45123)
    assert "gaiacode" in str(e.value) and "Settings" in str(e.value)
    assert e.value.kind == KIND_HARNESS_UNAVAILABLE


def test_an_unproven_harness_with_no_launcher_is_not_run_as_the_python_agent(
    monkeypatch,
):
    # The eval override makes gaiacode "runnable", but nothing here can start it.
    _save_raw("gaiacode")
    monkeypatch.setenv("GAIA_UNPROVEN_HARNESS", "gaiacode")
    monkeypatch.setattr(
        fetch, "fetch_binary", lambda **k: pytest.fail("ran the Python agent")
    )
    with pytest.raises(SidecarSpawnError) as e:
        _flagship_manager().build_spawn_command(port=45123)
    assert "gaiacode" in str(e.value) and "Settings" in str(e.value)
    assert e.value.kind == KIND_HARNESS_UNAVAILABLE


@pytest.mark.parametrize("saved", ["gaiacode", "gaiazzz"])
def test_an_unrunnable_harness_tells_the_chat_to_choose_another_in_settings(
    monkeypatch, saved
):
    # Known-but-not-runnable and unknown names both reach the user as "choose another".
    from gaia.ui.sidecar import daemon_client
    from gaia.ui.sidecar.profiles import GAIA_PROFILE

    _save_raw(saved)

    def _acquire(agent_id):
        try:
            _flagship_manager().build_spawn_command(port=45123)
        except SidecarSpawnError as e:
            # What the daemon route forwards in its error-kind header.
            raise SidecarError(str(e), kind=e.kind) from e
        pytest.fail("the daemon started an agent")

    monkeypatch.setattr(daemon_client, "acquire_handle", _acquire)
    events = []

    class _Handler:
        def _emit(self, event):
            events.append(event)

    request = ChatRequest(session_id="s1", message="hi", agent_type="gaia")
    _chat_helpers._dispatch_sidecar_query(_Handler(), request, [], "m", "gaia")

    [error] = [e["content"] for e in events if e["type"] == "agent_error"]
    assert "Settings" in error and "Choose another" in error
    for word in ("reinstall", "restart", "send your message again. if"):
        assert word not in error.lower(), word
    assert error != GAIA_PROFILE.start_failed_text()


def test_a_missing_gaiacpp_is_not_reported_as_an_unrunnable_harness():
    _choose("gaiacpp")
    with pytest.raises(SidecarSpawnError) as e:
        _flagship_manager().build_spawn_command(port=45123)
    assert e.value.kind == KIND_START_FAILED


# ---------------------------------------------------------------- settings API


@pytest.fixture
def client():
    return TestClient(
        create_app(db_path=":memory:"),
        client=("127.0.0.1", 50000),
        headers={"X-Gaia-UI": "1"},
    )


@pytest.fixture
def stops(monkeypatch):
    """Record every sidecar the settings route asks the daemon to stop."""
    from gaia.ui.sidecar import daemon_client

    seen = []
    monkeypatch.setattr(
        daemon_client, "stop_sidecar", lambda agent_id="email": seen.append(agent_id)
    )
    return seen


def test_settings_report_the_agent_and_whether_gaiacpp_is_here(client, fake_gaiacpp):
    body = client.get("/api/settings").json()
    assert body["flagship_agent"] == "gaiapy"
    assert body["gaiacpp_available"] is True


def test_settings_still_load_with_a_harness_this_version_cannot_run(client, no_gaiacpp):
    # Settings is where the user fixes it, so it must not fail on the value.
    _save_raw("gaiacode")
    body = client.get("/api/settings").json()
    assert body["flagship_agent"] == "gaiacode"
    assert client.get("/api/agent-programs").json()["current"] == "gaiacode"


def test_settings_recover_from_a_harness_this_version_cannot_run(client, stops):
    # Settings is the remedy every launch error names, so picking gaiapy there must work.
    _save_raw("gaiacode")
    r = client.put("/api/settings", json={"flagship_agent": "gaiapy"})
    assert r.status_code == 200, r.text
    assert GaiaConfig.load().flagship_agent == "gaiapy"
    assert fetch.chosen_flagship() == "gaiapy"


def test_settings_say_gaiacpp_is_not_installed(client, no_gaiacpp):
    assert client.get("/api/settings").json()["gaiacpp_available"] is False


def test_choosing_gaiacpp_saves_it_and_stops_the_running_agent(
    client, fake_gaiacpp, stops
):
    r = client.put("/api/settings", json={"flagship_agent": "gaiacpp"})
    assert r.status_code == 200, r.text
    assert r.json()["flagship_agent"] == "gaiacpp"
    assert fetch.chosen_flagship() == "gaiacpp"
    assert stops == ["gaia"]


def test_switching_back_stops_the_agent_again(client, fake_gaiacpp, stops):
    client.put("/api/settings", json={"flagship_agent": "gaiacpp"})
    r = client.put("/api/settings", json={"flagship_agent": "gaiapy"})
    assert r.json()["flagship_agent"] == "gaiapy"
    assert stops == ["gaia", "gaia"]


def test_choosing_the_agent_already_in_use_restarts_nothing(
    client, fake_gaiacpp, stops
):
    client.put("/api/settings", json={"flagship_agent": "gaiapy"})
    assert stops == []


def test_gaiacpp_cannot_be_chosen_where_it_is_not_installed(client, no_gaiacpp, stops):
    r = client.put("/api/settings", json={"flagship_agent": "gaiacpp"})
    assert r.status_code == 422
    assert "not installed" in r.json()["detail"]
    assert fetch.chosen_flagship() == "gaiapy"
    assert stops == []


def test_an_unknown_agent_name_is_refused(client, stops):
    r = client.put("/api/settings", json={"flagship_agent": "rust"})
    assert r.status_code == 422
    assert stops == []


def test_a_settings_write_that_leaves_the_agent_out_changes_nothing(
    client, fake_gaiacpp, stops
):
    _choose("gaiacpp")
    r = client.put("/api/settings", json={"dynamic_tools": True})
    assert r.json()["flagship_agent"] == "gaiacpp"
    assert stops == []


def test_an_agent_that_will_not_stop_is_reported_with_what_was_saved(
    client, fake_gaiacpp, monkeypatch
):
    from gaia.ui.sidecar import daemon_client

    def _refuse(agent_id="email"):
        raise SidecarError("the daemon did not answer")

    monkeypatch.setattr(daemon_client, "stop_sidecar", _refuse)
    r = client.put("/api/settings", json={"flagship_agent": "gaiacpp"})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert "saved as gaiacpp" in detail and "the daemon did not answer" in detail


# ---------------------------------------------------------------- review follow-ups


def test_choosing_gaiapy_when_nothing_was_chosen_saves_it_for_both_apps(client, stops):
    assert GaiaConfig.load().flagship_agent is None
    r = client.put("/api/settings", json={"flagship_agent": "gaiapy"})
    assert r.status_code == 200
    assert GaiaConfig.load().flagship_agent == "gaiapy"
    assert stops == [], "the Python agent was already the one running here"


def test_a_switch_is_refused_while_a_chat_is_answering(
    client, fake_gaiacpp, stops, monkeypatch
):
    from gaia.ui.run_manager import run_manager

    monkeypatch.setattr(run_manager, "active_sessions", lambda: ["chat-1"])
    r = client.put("/api/settings", json={"flagship_agent": "gaiacpp"})
    assert r.status_code == 409
    assert "answering" in r.json()["detail"]
    assert fetch.chosen_flagship() == "gaiapy"
    assert stops == []


def test_the_agent_cannot_be_switched_from_another_computer(fake_gaiacpp, stops):
    remote = TestClient(create_app(db_path=":memory:"), headers={"X-Gaia-UI": "1"})
    r = remote.put("/api/settings", json={"flagship_agent": "gaiacpp"})
    assert r.status_code == 403
    assert fetch.chosen_flagship() == "gaiapy"


def test_a_bad_saved_value_says_to_change_or_delete_the_line():
    with pytest.raises(GaiaConfigError) as e:
        GaiaConfig._check_flagship_agent("not a harness!", "GAIA config at x.json")
    assert "delete the 'flagship_agent' line" in str(e.value)
    assert "gaia config set" not in str(e.value), "that command fails the same way"


def test_a_pip_installs_path_is_not_trusted_for_gaiacpp(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    monkeypatch.delenv(fetch.NATIVE_FLAGSHIP_PATH_ENV, raising=False)
    monkeypatch.setattr(fetch.shutil, "which", lambda name: str(tmp_path / "x"))
    assert fetch.native_flagship_candidates() == [
        tmp_path / "bin" / fetch.native_flagship_executable_name()
    ]


def test_a_build_of_your_own_is_named_explicitly(monkeypatch, tmp_path):
    mine = tmp_path / "build" / fetch.native_flagship_executable_name()
    monkeypatch.setenv(fetch.NATIVE_FLAGSHIP_PATH_ENV, str(mine))
    assert fetch.native_flagship_candidates()[0] == mine


def test_a_downloaded_gaiacpp_that_was_changed_afterwards_is_not_run(
    monkeypatch, tmp_path
):
    import hashlib

    from gaia.daemon.sidecars.errors import IntegrityError

    binary = tmp_path / fetch.native_flagship_executable_name()
    binary.write_bytes(b"what was downloaded")
    binary.chmod(0o755)
    record = {
        "version": "0.26.1",
        "sha256": hashlib.sha256(b"what was downloaded").hexdigest(),
    }
    (tmp_path / fetch.NATIVE_INSTALL_RECORD_NAME).write_text(json.dumps(record))
    monkeypatch.setattr(fetch, "native_flagship_candidates", lambda: [binary])
    assert fetch.verified_native_flagship() == binary

    binary.write_bytes(b"something else")
    with pytest.raises(IntegrityError):
        fetch.verified_native_flagship()
    _choose("gaiacpp")
    with pytest.raises(SidecarSpawnError) as e:
        _flagship_manager().build_spawn_command(port=45123)
    assert "does not match" in str(e.value)


@pytest.mark.parametrize(
    "printed, stamp, version",
    [
        ("gaiacpp 0.26.1\n", "0.26.1", "0.26.1"),
        ("gaiacpp 0.26.0-rc.3\n", "0.26.0-rc.3", "0.26.0"),
    ],
)
def test_the_version_a_release_candidate_prints_is_read_whole(
    monkeypatch, tmp_path, printed, stamp, version
):
    import subprocess

    class _Done:
        returncode = 0
        stdout = printed
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done())
    binary = tmp_path / "gaiacpp"
    assert fetch.native_flagship_stamp(binary) == stamp
    assert fetch.native_flagship_version(binary) == version


def test_a_switch_is_refused_while_setup_is_running_its_tests(
    client, fake_gaiacpp, stops, monkeypatch
):
    from gaia.ui.setup_runner import runner

    monkeypatch.setattr(runner, "status", lambda: {"state": "verifying"})
    r = client.put("/api/settings", json={"flagship_agent": "gaiacpp"})
    assert r.status_code == 409
    assert "setting up" in r.json()["detail"]
    assert fetch.chosen_flagship() == "gaiapy"
    assert stops == []

    monkeypatch.setattr(runner, "status", lambda: {"state": "ready"})
    assert (
        client.put("/api/settings", json={"flagship_agent": "gaiacpp"}).status_code
        == 200
    )
