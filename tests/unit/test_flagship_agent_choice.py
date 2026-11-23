# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The user's choice between the Python agent (gaiapy) and the native one (gaiacpp).

One key, ``flagship_agent`` in ``~/.gaia/config.json``, decides which program
the daemon starts as the GAIA agent and whether the Agent UI relays to it.
"""

import dataclasses
import json
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gaia.config as config_mod
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.daemon.sidecars import fetch
from gaia.daemon.sidecars import manager as mgr
from gaia.daemon.sidecars.errors import (
    KIND_HARNESS_MISSING,
    KIND_HARNESS_UNAVAILABLE,
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
    monkeypatch.setattr(
        fetch, "native_flagship_candidates", lambda name="gaiacpp": [binary]
    )
    return binary


@pytest.fixture
def no_gaiacpp(tmp_path, monkeypatch):
    missing = tmp_path / "nowhere" / fetch.native_flagship_executable_name()
    monkeypatch.setattr(
        fetch, "native_flagship_candidates", lambda name="gaiacpp": [missing]
    )
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
    monkeypatch.setattr(
        fetch, "native_flagship_version", lambda binary, name="gaiacpp": "0.1.0"
    )
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
    # The Agent UI says what to do, not that "GAIA couldn't start its agent".
    from gaia.ui.sidecar.profiles import HARNESS_MISSING_MESSAGE, profile_for

    assert e.value.kind == KIND_HARNESS_MISSING
    assert (
        profile_for("gaia").start_failed_text(e.value.kind) == HARNESS_MISSING_MESSAGE
    )


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


def test_the_daemon_starts_gaiacode_under_the_eval_override(monkeypatch, tmp_path):
    # Not offered yet: only the eval lanes' override lets it run, from a build of its own.
    binary = tmp_path / "build" / fetch.native_flagship_executable_name("gaiacode")
    binary.parent.mkdir()
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("GAIA_GAIACODE_PATH", str(binary))
    monkeypatch.setenv("GAIA_UNPROVEN_HARNESS", "gaiacode")
    monkeypatch.setattr(
        fetch, "fetch_binary", lambda **k: pytest.fail("ran the Python agent")
    )
    seen = []
    monkeypatch.setattr(
        fetch,
        "native_flagship_version",
        lambda b, name="gaiacpp": seen.append(name) or "0.1.0",
    )
    _save_raw("gaiacode")
    m = _flagship_manager()
    argv, kwargs = m.build_spawn_command(port=45123)
    assert argv == [str(binary), "--host", m.host, "--port", "45123"]
    assert kwargs == {} and seen == ["gaiacode"]
    assert m.resolved_mode == "user"


def test_a_missing_gaiacode_names_its_variable_and_never_runs_python(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    monkeypatch.delenv("GAIA_GAIACODE_PATH", raising=False)
    monkeypatch.setenv("GAIA_UNPROVEN_HARNESS", "gaiacode")
    monkeypatch.setattr(
        fetch, "fetch_binary", lambda **k: pytest.fail("ran the Python agent")
    )
    _save_raw("gaiacode")
    with pytest.raises(SidecarSpawnError) as e:
        _flagship_manager().build_spawn_command(port=45123)
    text = str(e.value)
    assert "gaiacode" in text and "GAIA_GAIACODE_PATH" in text and "Settings" in text
    from gaia.ui.routers.agent_programs import HARNESS_UNAVAILABLE, chosen_agent_problem

    problem = chosen_agent_problem()
    assert problem is not None and problem["code"] == HARNESS_UNAVAILABLE, problem
    assert "GAIA_GAIACODE_PATH" in problem["detail"], "Settings says what is missing"


def test_each_native_harness_looks_for_its_own_program(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    monkeypatch.delenv("GAIA_GAIACPP_PATH", raising=False)
    monkeypatch.delenv("GAIA_GAIACODE_PATH", raising=False)
    assert fetch.native_flagship_candidates("gaiacode")[0] == (
        tmp_path / "bin" / fetch.native_flagship_executable_name("gaiacode")
    )
    with pytest.raises(ValueError):
        fetch.native_flagship_candidates("gaiapy")


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
    # Missing, so the fix is to download it: never "this version can't run it".
    assert e.value.kind == KIND_HARNESS_MISSING


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
    monkeypatch.setattr(
        fetch, "native_flagship_candidates", lambda name="gaiacpp": [binary]
    )
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


# ---------------------------------------------------------------- gaiacode's model


def test_gaiacode_takes_only_a_model_the_user_chose(monkeypatch):
    from gaia.harnesses import chosen_model

    monkeypatch.delenv("GAIA_MODEL_ID", raising=False)
    GaiaConfig.save_key("default_model", "Gemma-4-E4B-it-GGUF")
    assert chosen_model() is None, "setup's hardware pick is not the user's choice"
    GaiaConfig.save_key("last_model", "fireworks.glm-5p3")
    assert chosen_model() == "fireworks.glm-5p3"
    monkeypatch.setenv("GAIA_MODEL_ID", "Qwen3.6-35B-A3B-GGUF")
    assert chosen_model() == "Qwen3.6-35B-A3B-GGUF"


def _relay_once(monkeypatch, model):
    """Run one Agent UI turn for gaia; return (events, the model relay_query was given)."""
    from gaia.ui.sidecar import daemon_client, relay

    handle = daemon_client.SidecarHandle(
        base_url="http://127.0.0.1:1",
        token="t",
        api_version="99.0",
        agent_version="0.1.0",
        mode="user",
        pid=1,
        agent_id="gaia",
    )
    monkeypatch.setattr(daemon_client, "acquire_handle", lambda agent_id: handle)
    sent = {}

    def _relay(*args, **kwargs):
        sent["model"] = kwargs.get("model_id", args[3] if len(args) > 3 else None)

    monkeypatch.setattr(relay, "relay_query", _relay)
    events = []

    class _Handler:
        cancelled = threading.Event()

        def _emit(self, event):
            events.append(event)

    request = ChatRequest(session_id="s1", message="hi", agent_type="gaia")
    _chat_helpers._dispatch_sidecar_query(_Handler(), request, [], model, "gaia")
    return events, sent


def test_an_agent_ui_turn_never_sends_gaiacode_a_filled_in_model(monkeypatch):
    monkeypatch.delenv("GAIA_MODEL_ID", raising=False)
    _save_raw("gaiacode")
    events, sent = _relay_once(monkeypatch, "Gemma-4-E4B-it-GGUF")
    errors = [e["content"] for e in events if e["type"] == "agent_error"]
    assert errors and "No model is chosen for gaiacode" in errors[0], events
    assert "model" not in sent, "the turn reached gaiacode"


def test_a_chat_on_its_own_model_runs_gaiacode_on_it(monkeypatch):
    # Two chats on different models: each runs on its own, not on the last one picked.
    from gaia.ui.database import resolved_default_model

    monkeypatch.delenv("GAIA_MODEL_ID", raising=False)
    GaiaConfig.save_key("last_model", "fireworks.glm-5p3")
    _save_raw("gaiacode")
    picked = "Qwen3.5-4B-GGUF"
    assert picked != resolved_default_model()
    from gaia.ui.sidecar import daemon_client

    class _Ready:
        base_url = "http://127.0.0.1:1"

        def init(self):
            return 200, {"model": {"present": True}}

    monkeypatch.setattr(daemon_client.SidecarHandle, "proxy", lambda self: _Ready())
    events, sent = _relay_once(monkeypatch, picked)
    assert not [e for e in events if e["type"] == "agent_error"], events
    assert sent["model"] == picked


# ---------------------------------------------------------------- a switch made elsewhere


def test_a_flagship_started_as_another_harness_is_stale_after_a_switch(
    fake_gaiacpp, monkeypatch
):
    monkeypatch.setattr(
        fetch, "native_flagship_version", lambda b, name="gaiacpp": "0.1.0"
    )
    _choose("gaiacpp")
    m = _flagship_manager()
    m.build_spawn_command(port=45123)
    assert not m.uses_stale_harness
    _choose("gaiapy")  # the TUI's /agent writes only config.json
    assert m.uses_stale_harness


def test_a_non_flagship_agent_is_never_stale():
    other = [s for s in builtin_specs().values() if s.agent_id != "gaia"]
    if not other:
        pytest.skip("no other built-in sidecar spec")
    assert not mgr.AgentSidecarManager(other[0]).uses_stale_harness


# ---------------------------------------------------------------- start screen


def test_the_start_screen_explains_a_saved_harness_it_cannot_run(monkeypatch):
    from gaia.ui.routers import agent_programs

    monkeypatch.delenv("GAIA_UNPROVEN_HARNESS", raising=False)
    _save_raw("gaiacode")
    problem = agent_programs.chosen_agent_problem()
    assert problem["code"] == "harness_unavailable"
    assert "gaiacode" in problem["detail"]


# ---------------------------------------------------------------- a stale agent restarts only when idle


def _stale_dispatch(monkeypatch, busy, elsewhere=0):
    """One Agent UI turn whose sidecar runs a harness other than the saved one.

    *elsewhere* is what the sidecar says other apps are running, or an
    exception it raises instead.
    """
    from gaia.ui import run_manager as rm
    from gaia.ui.sidecar import daemon_client, relay

    def handle(stale):
        return daemon_client.SidecarHandle(
            base_url="http://127.0.0.1:1",
            token="t",
            api_version="99.0",
            agent_version="0.1.0",
            mode="user",
            pid=1,
            agent_id="gaia",
            stale_harness=stale,
        )

    acquired, stopped = [], []

    def _acquire(agent_id):
        acquired.append(agent_id)
        return handle(len(acquired) == 1)

    monkeypatch.setattr(daemon_client, "acquire_handle", _acquire)
    monkeypatch.setattr(
        daemon_client, "stop_sidecar", lambda agent_id: stopped.append(agent_id)
    )
    monkeypatch.setattr(
        rm.run_manager, "active_sessions", lambda: ["s1"] + (["other"] if busy else [])
    )

    def _active_runs(h):
        if isinstance(elsewhere, Exception):
            raise elsewhere
        return elsewhere

    monkeypatch.setattr(daemon_client, "active_runs", _active_runs)
    monkeypatch.setattr(relay, "relay_query", lambda *a, **k: None)
    events = []

    class _Handler:
        def _emit(self, event):
            events.append(event)

    request = ChatRequest(session_id="s1", message="hi", agent_type="gaia")
    _chat_helpers._dispatch_sidecar_query(
        _Handler(), request, [], "m", "gaia", session_id="s1"
    )
    return events, acquired, stopped


def test_a_stale_agent_restarts_when_no_other_chat_is_answering(monkeypatch):
    _choose("gaiapy")
    events, acquired, stopped = _stale_dispatch(monkeypatch, busy=False)
    assert stopped == ["gaia"] and len(acquired) == 2, events


def test_a_stale_agent_is_left_running_while_another_chat_answers(monkeypatch):
    _choose("gaiapy")
    events, acquired, stopped = _stale_dispatch(monkeypatch, busy=True)
    assert stopped == [] and len(acquired) == 1
    assert any("takes over once" in e.get("message", "") for e in events), events


def test_a_stale_agent_is_left_running_while_a_background_relay_runs(monkeypatch):
    # A scheduled prompt or agent-loop tick isn't a chat session, but it is mid-answer.
    _choose("gaiapy")
    monkeypatch.setattr(_chat_helpers, "_relays_in_flight", 1)
    events, acquired, stopped = _stale_dispatch(monkeypatch, busy=False)
    assert stopped == [] and len(acquired) == 1
    assert _chat_helpers._relays_in_flight == 1, "the turn's own count was released"


def test_a_stale_agent_is_left_running_while_another_app_is_answering(monkeypatch):
    # The TUI drives the same sidecar; its turns are invisible to this process.
    _choose("gaiapy")
    events, acquired, stopped = _stale_dispatch(monkeypatch, busy=False, elsewhere=1)
    assert stopped == [] and len(acquired) == 1
    assert any("takes over once" in e.get("message", "") for e in events), events


def test_a_sidecar_that_cant_say_is_never_restarted_on_a_guess(monkeypatch):
    from gaia.daemon.sidecars.errors import SidecarError

    _choose("gaiapy")
    events, acquired, stopped = _stale_dispatch(
        monkeypatch, busy=False, elsewhere=SidecarError("HTTP 404")
    )
    assert stopped == [] and len(acquired) == 1
    assert any("takes over once" in e.get("message", "") for e in events), events


def test_a_sidecar_from_before_activity_restarts_on_this_apps_idle_check(monkeypatch):
    # An old binary has no /activity; waiting on it would leave the switch pending forever.
    from gaia.ui.sidecar import daemon_client

    _choose("gaiapy")
    real = daemon_client.SidecarHandle
    monkeypatch.setattr(
        daemon_client,
        "SidecarHandle",
        lambda **kw: real(**{**kw, "api_version": "2.31", "harness": "gaiapy"}),
    )
    # It would report a busy agent if asked: the restart shows it never was.
    events, acquired, stopped = _stale_dispatch(monkeypatch, busy=False, elsewhere=1)
    assert stopped == ["gaia"] and len(acquired) == 2, events


def test_activity_is_read_only_from_a_sidecar_that_serves_it():
    from gaia.ui.sidecar import daemon_client

    def handle(version, harness):
        return daemon_client.SidecarHandle(
            base_url="http://127.0.0.1:1",
            token="t",
            api_version=version,
            agent_version="0.1.0",
            mode="user",
            pid=1,
            agent_id="gaia",
            harness=harness,
        )

    assert _chat_helpers._reports_activity(handle("2.32", "gaiapy"))
    assert not _chat_helpers._reports_activity(handle("2.31", "gaiapy"))
    assert _chat_helpers._reports_activity(handle("2.28", "gaiacpp"))
    assert not _chat_helpers._reports_activity(handle("2.27", "gaiacode"))
    assert _chat_helpers._reports_activity(
        handle("3.0", None)
    ), "an unnamed harness is the Python one"
    assert not _chat_helpers._reports_activity(handle("garbage", "gaiapy"))


def test_active_runs_reads_the_sidecar_and_refuses_an_older_one(monkeypatch):
    from gaia.daemon.sidecars.errors import SidecarError
    from gaia.ui.sidecar import daemon_client

    handle = daemon_client.SidecarHandle(
        base_url="http://127.0.0.1:1",
        token="tok",
        api_version="2.32",
        agent_version="0.1.0",
        mode="user",
        pid=1,
        agent_id="gaia",
    )
    seen = {}

    class _Reply:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def json(self):
            return self._body

    def _get(url, headers, timeout):
        seen.update(url=url, auth=headers.get("Authorization"))
        return reply

    monkeypatch.setattr(daemon_client.requests, "get", _get)
    reply = _Reply(200, {"active_runs": 2})
    assert daemon_client.active_runs(handle) == 2
    assert seen == {"url": "http://127.0.0.1:1/v1/gaia/activity", "auth": "Bearer tok"}
    reply = _Reply(404, {"detail": "Not Found"})
    with pytest.raises(SidecarError, match="can't say"):
        daemon_client.active_runs(handle)


def test_one_agents_restart_never_holds_up_anothers_relay(monkeypatch):
    # The per-agent lock: a held "gaia" lock doesn't block a relay to another agent.
    import threading

    held = _chat_helpers._agent_lock("gaia")
    other = _chat_helpers._agent_lock("custom")
    assert held is not other
    assert held is _chat_helpers._agent_lock("gaia")
    with held:
        got = []
        t = threading.Thread(target=lambda: got.append(other.acquire(timeout=2)))
        t.start()
        t.join(5)
        assert got == [True]
        other.release()


def test_a_relay_is_counted_until_its_dispatch_returns(monkeypatch):
    _choose("gaiapy")
    seen = []
    from gaia.ui.sidecar import relay

    monkeypatch.setattr(
        relay,
        "relay_query",
        lambda *a, **k: seen.append(_chat_helpers._relays_in_flight),
    )
    _relay_once(monkeypatch, "m")
    assert seen in ([1], []), seen
    assert _chat_helpers._relays_in_flight == 0


def test_a_kept_stale_agent_keeps_its_own_model_rules(monkeypatch):
    # Saved gaiacode, but the running gaiapy answers: no gaiacode model refusal.
    from gaia.ui import run_manager as rm
    from gaia.ui.sidecar import daemon_client, relay

    monkeypatch.delenv("GAIA_MODEL_ID", raising=False)
    monkeypatch.setenv("GAIA_UNPROVEN_HARNESS", "gaiacode")
    _save_raw("gaiacode")
    handle = daemon_client.SidecarHandle(
        base_url="http://127.0.0.1:1",
        token="t",
        api_version="99.0",
        agent_version="0.1.0",
        mode="user",
        pid=1,
        agent_id="gaia",
        stale_harness=True,
        harness="gaiapy",
    )
    monkeypatch.setattr(daemon_client, "acquire_handle", lambda agent_id: handle)
    monkeypatch.setattr(rm.run_manager, "active_sessions", lambda: ["s1", "other"])
    sent = {}
    monkeypatch.setattr(
        relay,
        "relay_query",
        lambda *a, **k: sent.setdefault("model", k.get("model_id")),
    )
    events = []

    class _Handler:
        def _emit(self, event):
            events.append(event)

    request = ChatRequest(session_id="s1", message="hi", agent_type="gaia")
    _chat_helpers._dispatch_sidecar_query(
        _Handler(), request, [], "Gemma-4-E4B-it-GGUF", "gaia", session_id="s1"
    )
    # Past the model rules (the relay itself isn't faked here): none of gaiacode's refusals.
    for e in events:
        assert "gaiacode" not in str(e.get("content", "")), events


def test_gaiacode_on_hybrid_starts_on_its_cheap_model(monkeypatch):
    from gaia.harnesses import chosen_model
    from gaia.llm import hybrid

    monkeypatch.delenv("GAIA_MODEL_ID", raising=False)
    GaiaConfig.save_key("last_model", "hybrid")
    assert chosen_model() == hybrid.start_model("hybrid")
    assert chosen_model() != "hybrid"


def test_gaiacode_refuses_a_claude_model_in_the_agent_ui(monkeypatch):
    monkeypatch.delenv("GAIA_MODEL_ID", raising=False)
    _save_raw("gaiacode")
    GaiaConfig.save_key("last_model", "claude-sonnet-5")
    events, sent = _relay_once(monkeypatch, "Gemma-4-E4B-it-GGUF")
    errors = [e["content"] for e in events if e["type"] == "agent_error"]
    assert errors and "Claude models are not available" in errors[0], events
    assert "model" not in sent
