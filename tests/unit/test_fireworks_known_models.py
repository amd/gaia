# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Fireworks' model list failing, and the entries that keep chat working."""

import json

import pytest
import requests
import responses

from gaia.llm import cloud_keys
from gaia.llm import fireworks_known_models as known
from gaia.llm.providers.fireworks import (
    FIREWORKS_BASE_URL,
    ModelListCheck,
    ModelListState,
    check_model_list,
)

URL = FIREWORKS_BASE_URL + "/models"
KEY = "fw_SECRET_key_do_not_leak"


@pytest.mark.parametrize(
    "status,state",
    [
        (200, ModelListState.OK),
        (401, ModelListState.REJECTED),
        (403, ModelListState.REJECTED),
        (500, ModelListState.FAILING),
        (503, ModelListState.FAILING),
        (429, ModelListState.FAILING),
    ],
)
@responses.activate
def test_the_list_answer_separates_a_bad_key_from_a_failure(status, state):
    responses.add(responses.GET, URL, json={"data": []}, status=status)
    assert check_model_list(KEY).state is state
    assert responses.calls[0].request.headers["Authorization"] == f"Bearer {KEY}"


@responses.activate
def test_an_echoed_key_never_reaches_the_failure_detail():
    responses.add(responses.GET, URL, body=f"boom {KEY}", status=500)
    check = check_model_list(KEY)
    assert "HTTP 500" in check.detail and KEY not in check.detail


@pytest.mark.parametrize("offset", [4, 10, 20])
@responses.activate
def test_a_key_echoed_across_the_detail_cut_leaks_no_part_of_itself(offset):
    pad = "x" * (200 - offset)
    responses.add(responses.GET, URL, body=f"{pad}{KEY} trailing", status=500)
    detail = check_model_list(KEY).detail
    assert KEY[:offset] not in detail


@responses.activate
def test_the_check_only_ever_calls_fireworks():
    responses.add(responses.GET, URL, json={"data": []}, status=200)
    check_model_list(KEY)
    assert [c.request.url for c in responses.calls] == [URL]


@responses.activate
def test_an_unreachable_fireworks_is_a_failure_and_hides_the_exception_text():
    responses.add(responses.GET, URL, body=requests.ConnectionError(f"bad {KEY}"))
    check = check_model_list(KEY)
    assert check.state is ModelListState.FAILING and KEY not in check.detail


def test_entries_have_what_the_server_needs_to_route_them(tmp_path):
    known.add_entries(tmp_path)
    entry = json.loads((tmp_path / "user_models.json").read_text())[
        "fireworks.glm-5p3-flash"
    ]
    assert entry["recipe"] == "cloud"
    assert entry["cloud_provider"] == "fireworks"
    assert entry["checkpoint"] == "accounts/fireworks/models/glm-5p3-flash"
    assert "tool-calling" in entry["labels"]


def test_a_dated_snapshot_is_not_offered():
    assert "glm-5p3-flash" in known.offered_models()
    assert "deepseek-v4-flash-0731" not in known.offered_models()


def test_the_users_own_models_survive_adding_and_removing(tmp_path):
    path = tmp_path / "user_models.json"
    path.write_text(json.dumps({"mine": {"recipe": "llamacpp"}}))
    known.add_entries(tmp_path)
    assert known.is_active(tmp_path)
    known.remove_entries(tmp_path)
    assert not known.is_active(tmp_path)
    assert json.loads(path.read_text()) == {"mine": {"recipe": "llamacpp"}}


def test_an_unreadable_model_file_is_never_overwritten(tmp_path):
    path = tmp_path / "user_models.json"
    path.write_text("{ torn")
    with pytest.raises(known.KnownModelsError):
        known.add_entries(tmp_path)
    assert path.read_text() == "{ torn"


@pytest.fixture
def active(tmp_path, monkeypatch):
    known.add_entries(tmp_path)
    monkeypatch.setattr(cloud_keys, "recall_key", lambda provider: KEY)
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    return tmp_path


def _list_answers(monkeypatch, state):
    monkeypatch.setattr(
        known, "check_model_list", lambda key: ModelListCheck(state, "HTTP 500")
    )


def test_a_start_during_the_outage_carries_the_key(active, monkeypatch):
    _list_answers(monkeypatch, ModelListState.FAILING)
    assert known.start_env(active) == {"LEMONADE_FIREWORKS_API_KEY": KEY}
    assert known.is_active(active)


@pytest.mark.parametrize("state", [ModelListState.OK, ModelListState.REJECTED])
def test_a_start_after_the_outage_goes_back_to_the_live_list(
    active, monkeypatch, state
):
    _list_answers(monkeypatch, state)
    assert known.start_env(active) == {}
    assert not known.is_active(active)


def test_local_only_keeps_the_key_out_of_the_server(active, monkeypatch):
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: True)
    assert known.start_env(active) == {}


def test_a_normal_start_asks_fireworks_nothing(tmp_path, monkeypatch):
    def fail(_):
        raise AssertionError("no list check without known models")

    monkeypatch.setattr(known, "check_model_list", fail)
    assert known.start_env(tmp_path) == {}


def test_restart_changes_the_file_only_while_the_server_is_stopped(tmp_path):
    order = []

    class Embedded:
        config_dir = tmp_path

        def status(self):
            return type("S", (), {"port": 1234, "owner_pid": 7, "owner_started": 8.0})()

        def stop(self):
            order.append(("stop", known.is_active(tmp_path)))

        def start(self, **kwargs):
            order.append(("start", known.is_active(tmp_path), kwargs["port"]))

    known.use_known_models(Embedded())
    assert order == [("stop", False), ("start", True, 1234)]
