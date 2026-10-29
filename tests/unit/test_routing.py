# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA picks a turn's model from a Lemonade router (``collection.router``)."""

import pytest

from gaia import print_mode
from gaia.llm import routing

ROUTING = {
    "candidates": ["amd.Cheap", "amd.Strong"],
    "default_model": "amd.Cheap",
    "classifiers": [
        {
            "id": "work",
            "type": "llm",
            "model": "amd.Cheap",
            "labels": ["find_defects", "other"],
            "prompt": "...",
        }
    ],
    "rules": [
        {
            "id": "defects-to-strong",
            "match": {"classifier": "work", "label": "find_defects"},
            "route_to": "amd.Strong",
        }
    ],
}
ROUTER = {
    "id": "user.Hybrid",
    "recipe": "collection.router",
    "version": "1",
    "components": ["amd.Cheap", "amd.Strong"],
    "routing": ROUTING,
}


class FakeClient:
    """A Lemonade client with a catalog and a canned routing decision."""

    base_url = "http://localhost:13305/api/v1"

    def __init__(self, catalog, decision=None, fail=None):
        self.catalog, self.decision, self.fail = catalog, decision, fail
        self.posts = []

    def get_model_details(self, model_id):
        if model_id not in self.catalog:
            raise RuntimeError(f"404 {model_id}")
        return self.catalog[model_id]

    def _send_request(self, method, url, data=None, timeout=None):
        self.posts.append((method, url, data))
        if self.fail:
            raise self.fail
        return {"decision": self.decision}


def _decision(route_to, rule="defects-to-strong", label="find_defects"):
    return {
        "route_to": route_to,
        "matched_rule": rule,
        "trace": [
            {
                "condition": "classifier:work",
                "label": label,
                "result": route_to == "amd.Strong",
                "score": 1.0 if route_to == "amd.Strong" else 0.0,
                "rationale": "a review",
            }
        ],
    }


def test_a_router_turn_gets_its_chosen_candidate_and_why():
    client = FakeClient({"user.Hybrid": ROUTER}, _decision("amd.Strong"))
    route = routing.route_turn(client, "user.Hybrid", "Review this PR")
    assert route.model == "amd.Strong" and route.rule == "defects-to-strong"
    assert route.label == "find_defects" and route.rationale == "a review"
    method, url, body = client.posts[0]
    # The request Lemonade's validate endpoint accepts: the stored policy, the turn.
    assert method == "post" and url.endswith("/api/v1/routing/validate")
    assert body["policy"] == {
        "version": "1",
        "recipe": "collection.router",
        "components": ["amd.Cheap", "amd.Strong"],
        "routing": ROUTING,
    }
    assert body["prompt"] == "Review this PR" and body["has_tools"] is True


def test_the_default_route_is_reported_as_default():
    client = FakeClient(
        {"user.Hybrid": ROUTER}, {"route_to": "amd.Cheap", "matched_rule": None}
    )
    route = routing.route_turn(client, "user.Hybrid", "Write a summary")
    assert (
        route.model == "amd.Cheap" and route.rule == "default" and route.label is None
    )


@pytest.mark.parametrize(
    "catalog",
    [
        {"amd.Cheap": {"id": "amd.Cheap", "recipe": "cloud"}},
        {"Gemma-4-E4B-it-GGUF": {"id": "Gemma-4-E4B-it-GGUF", "recipe": "llamacpp"}},
    ],
)
def test_an_ordinary_model_is_not_routed_and_costs_no_routing_call(catalog):
    client = FakeClient(catalog)
    (name,) = catalog
    assert routing.route_turn(client, name, "hi") is None
    assert client.posts == []


def test_a_name_lemonade_does_not_list_is_not_routed():
    assert routing.route_turn(FakeClient({}), "amd.Unlisted", "hi") is None


def test_an_unlisted_user_model_is_an_error_not_a_silent_pass_through():
    with pytest.raises(routing.RoutingError, match="Could not read model 'user.Gone'"):
        routing.route_turn(FakeClient({}), "user.Gone", "hi")


def test_a_router_listed_without_its_policy_says_how_to_fix_it():
    broken = {**ROUTER}
    del broken["routing"]
    with pytest.raises(routing.RoutingError, match="Re-register it"):
        routing.route_turn(FakeClient({"user.Hybrid": broken}), "user.Hybrid", "hi")


def test_a_routing_failure_is_raised_with_the_remedy():
    client = FakeClient(
        {"user.Hybrid": ROUTER}, fail=RuntimeError("HTTP 400 bad policy")
    )
    with pytest.raises(routing.RoutingError, match="bad policy.*routing/validate"):
        routing.route_turn(client, "user.Hybrid", "hi")


@pytest.mark.parametrize("chosen", ["amd.Elsewhere", None, ""])
def test_a_choice_outside_the_candidates_is_refused(chosen):
    client = FakeClient({"user.Hybrid": ROUTER}, _decision(chosen))
    with pytest.raises(routing.RoutingError, match="not one of its candidates"):
        routing.route_turn(client, "user.Hybrid", "hi")


def test_no_model_means_no_routing():
    assert routing.route_turn(FakeClient({}), None, "hi") is None


# --- gaia -p -----------------------------------------------------------------


@pytest.fixture
def routed(monkeypatch):
    """gaia -p with a router whose choice is amd.Strong; records what ran."""
    seen = {"started": []}
    monkeypatch.setattr(
        print_mode,
        "_start_server",
        lambda model, base_url: seen["started"].append(model) or "http://x/api/v1",
    )
    monkeypatch.setattr(print_mode, "_resolve_model", lambda m: m)
    client = FakeClient({"user.Hybrid": ROUTER}, _decision("amd.Strong"))
    monkeypatch.setattr("gaia.llm.lemonade_client.LemonadeClient", lambda **kw: client)

    def fake_turn(text, **kw):
        seen.update(text=text, model=kw["model"])
        counts = dict.fromkeys(print_mode._SUMMED, 0)
        return {
            "type": "result",
            "is_error": False,
            "status": "success",
            "result": "ok",
            **counts,
        }

    monkeypatch.setattr(print_mode, "_run_turn", fake_turn)
    return seen, client


def test_gaia_p_runs_the_turn_on_the_routed_model_and_reports_the_route(routed, capsys):
    seen, client = routed
    assert print_mode.run("Review it", model="user.Hybrid", output_format="json") == 0
    assert seen["model"] == "amd.Strong" and seen["text"] == "Review it"
    import json

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["route"]["model"] == "amd.Strong"
    assert record["route"]["router"] == "user.Hybrid"
    assert record["route"]["label"] == "find_defects"
    assert len(client.posts) == 1  # one decision for the turn


def test_attempts_inherit_the_routed_model_and_route_only_once(
    routed, monkeypatch, tmp_path
):
    seen, client = routed
    from gaia import attempts

    scratch = tmp_path / "s"
    scratch.mkdir()
    flags_seen = {}

    def fake_attempts(text, count, flags):
        flags_seen["flags"] = flags
        return text, [], scratch

    monkeypatch.setattr(attempts, "run_attempts", fake_attempts)
    assert print_mode.run("Review it", model="user.Hybrid", attempts=2) == 0
    assert flags_seen["flags"][:2] == ["--model", "amd.Strong"]
    assert seen["model"] == "amd.Strong"
    assert len(client.posts) == 1


def test_a_plain_model_skips_routing(routed, monkeypatch):
    seen, client = routed
    client.catalog = {"amd.Cheap": {"id": "amd.Cheap", "recipe": "cloud"}}
    assert print_mode.run("hi", model="amd.Cheap") == 0
    assert seen["model"] == "amd.Cheap" and client.posts == []


def test_a_routing_failure_fails_the_run_loudly(routed, capsys):
    seen, client = routed
    client.fail = RuntimeError("HTTP 500")
    assert print_mode.run("hi", model="user.Hybrid", output_format="json") == 1
    assert "could not route this turn" in capsys.readouterr().out
    assert "model" not in seen  # no turn ran on a guessed model


def test_an_unmatched_rule_label_is_not_reported_as_the_choice():
    """Lemonade traces the label a rule tested; false means it was not chosen."""
    decision = {
        "route_to": "amd.Cheap",
        "matched_rule": "",
        "trace": [
            {
                "condition": "classifier:work",
                "label": "find_defects",
                "result": False,
                "score": 0.0,
            }
        ],
    }
    route = routing.route_turn(
        FakeClient({"user.Hybrid": ROUTER}, decision), "user.Hybrid", "hi"
    )
    assert (
        route.model == "amd.Cheap" and route.label is None and route.rule == "default"
    )
    assert route.classified is True


def test_a_classifier_that_gave_no_answer_is_flagged(caplog):
    decision = {
        "route_to": "amd.Cheap",
        "matched_rule": "",
        "trace": [
            {"condition": "classifier:work", "label": "find_defects", "result": False}
        ],
    }
    route = routing.route_turn(
        FakeClient({"user.Hybrid": ROUTER}, decision), "user.Hybrid", "hi"
    )
    assert route.classified is False
    assert "gave no answer" in caplog.text
