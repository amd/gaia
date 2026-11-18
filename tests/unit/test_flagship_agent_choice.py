# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The user's choice between the Python agent (gaiapy) and the native one (gaiacpp).

One key, ``flagship_agent`` in ``~/.gaia/config.json``, decides which program
the daemon starts as the GAIA agent and whether the Agent UI relays to it.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gaia.config as config_mod
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.daemon.sidecars import fetch
from gaia.daemon.sidecars import manager as mgr
from gaia.daemon.sidecars.errors import SidecarError, SidecarSpawnError
from gaia.daemon.sidecars.spec import builtin_specs
from gaia.ui import _chat_helpers
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


def test_an_unknown_value_in_the_file_is_an_error_naming_the_key():
    path = _config_file()
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc["flagship_agent"] = "rust"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
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
    assert m._resolve_secret_delivery()[0] == "file"


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
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    email = mgr.AgentSidecarManager(builtin_specs()["email"])
    argv, _ = email.build_spawn_command(port=45123)
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
    from gaia.ui.email_sidecar import daemon_client

    seen = []
    monkeypatch.setattr(
        daemon_client, "stop_sidecar", lambda agent_id="email": seen.append(agent_id)
    )
    return seen


def test_settings_report_the_agent_and_whether_gaiacpp_is_here(client, fake_gaiacpp):
    body = client.get("/api/settings").json()
    assert body["flagship_agent"] == "gaiapy"
    assert body["gaiacpp_available"] is True


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
    from gaia.ui.email_sidecar import daemon_client

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
        GaiaConfig._check_flagship_agent("rust", "GAIA config at x.json")
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
