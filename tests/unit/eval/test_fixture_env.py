# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A local eval run's own fixture root and port, and the fingerprint of what it measured.

The scenarios name ``~/gaia-eval`` and ``127.0.0.1:8765``; a run staged under
its ``--out`` folder on a free port must show the agent and the judge the same
rewritten place, or a correct answer is scored against the wrong ground truth.
"""

import json
from pathlib import Path

import pytest
import yaml

from gaia.eval import fixture_env
from gaia.eval.runner import (
    SCENARIOS_DIR,
    ScorecardMismatchError,
    compare_scorecards,
    measurement_warnings,
    validate_scenario,
)

ROOT = Path("/tmp/out/eval-root/gaia-eval")


def test_root_and_port_are_rewritten_in_messages_and_ground_truth():
    scenario = {
        "turns": [
            {
                "user_message": "Load ~/gaia-eval/csv/sales.csv and fetch "
                "http://127.0.0.1:8765/atlas.html",
                "ground_truth": {"expected_answer": "Host 127.0.0.1, port 8765"},
            },
            {"user_message": "ok what about http://2130706433:8765/atlas.html"},
        ],
        "tags": ["live"],
        "severity": 3,
    }

    out = fixture_env.localize(scenario, ROOT, 51234)

    turn1, turn2 = out["turns"]
    assert turn1["user_message"] == (
        f"Load {ROOT.as_posix()}/csv/sales.csv and fetch "
        "http://127.0.0.1:51234/atlas.html"
    )
    assert turn1["ground_truth"]["expected_answer"] == "Host 127.0.0.1, port 51234"
    assert turn2["user_message"].endswith("2130706433:51234/atlas.html")
    assert out["severity"] == 3
    assert scenario["turns"][0]["user_message"].startswith("Load ~/gaia-eval")


def test_numbers_that_merely_contain_8765_are_left_alone():
    text = "AWS account ID: 98765, expected 98765"

    assert fixture_env.localize_text(text, ROOT, 51234) == text


def test_without_a_root_or_port_the_scenario_is_untouched():
    scenario = {"turns": [{"user_message": "~/gaia-eval at 127.0.0.1:8765"}]}

    assert fixture_env.localize(scenario, None, None) is scenario


def test_every_builtin_scenario_still_validates_and_leaves_nothing_behind(tmp_path):
    root = tmp_path / "gaia-eval"
    checked = 0
    for path in sorted(SCENARIOS_DIR.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        out = fixture_env.localize(data, root, 51234)
        validate_scenario(path, out)
        text = json.dumps(out)
        assert "~/gaia-eval" not in text, path
        assert "127.0.0.1:8765" not in text, path
        checked += "gaia-eval" in json.dumps(data) or ":8765" in json.dumps(data)
    assert checked > 100


def test_env_names_the_root_and_port(tmp_path):
    root, port = fixture_env.from_env(
        {fixture_env.ROOT_ENV: str(tmp_path), fixture_env.PORT_ENV: "51234"}
    )

    assert (root, port) == (tmp_path, 51234)
    assert fixture_env.from_env({}) == (None, None)


@pytest.mark.parametrize(
    "environ, match",
    [
        ({fixture_env.ROOT_ENV: "relative/gaia-eval"}, "absolute path"),
        ({fixture_env.ROOT_ENV: "/no/such/gaia-eval"}, "absolute path"),
        ({fixture_env.PORT_ENV: "eighty"}, "not a port"),
        ({fixture_env.PORT_ENV: "70000"}, "out of the port range"),
    ],
)
def test_a_malformed_env_is_refused(environ, match):
    with pytest.raises(ValueError, match=match):
        fixture_env.from_env(environ)


def _repo(tmp_path):
    (tmp_path / "tests" / "fixtures" / "gaia" / "csv").mkdir(parents=True)
    (tmp_path / "eval" / "scenarios").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "gaia" / "csv" / "sales.csv").write_bytes(
        b"a,b\r\n1,2\r\n"
    )
    (tmp_path / "eval" / "scenarios" / "s.yaml").write_text("id: s\n")
    return tmp_path


def test_the_fingerprint_follows_content_not_line_endings_or_build_output(tmp_path):
    repo = _repo(tmp_path)
    first = fixture_env.fixture_fingerprint(repo)

    csv = repo / "tests" / "fixtures" / "gaia" / "csv" / "sales.csv"
    csv.write_bytes(b"a,b\n1,2\n")
    hub = repo / "tests" / "fixtures" / "gaia" / "fixture_hub" / "_prepared"
    hub.mkdir(parents=True)
    (hub / "index.json").write_text("{}")
    # The hub's lock and swap folders that prepare_fixture_hub leaves behind.
    (hub.parent / "_prepared.lock").write_text("")
    (hub.parent / "_prepared.lock.d").mkdir()
    (hub.parent / "_prepared.lock.d" / "owner").write_text("1234")
    (hub.parent / "_prepared.old-x").mkdir()
    (hub.parent / "_prepared.old-x" / "index.json").write_text("{}")
    assert fixture_env.fixture_fingerprint(repo) == first

    csv.write_bytes(b"a,b\n1,3\n")
    changed = fixture_env.fixture_fingerprint(repo)
    assert changed["fixtures"] != first["fixtures"]
    assert changed["scenarios"] == first["scenarios"]
    assert changed["combined"] != first["combined"]


def test_a_missing_tree_cannot_be_fingerprinted(tmp_path):
    with pytest.raises(FileNotFoundError, match="cannot be fingerprinted"):
        fixture_env.fixture_fingerprint(tmp_path)


def _card(path, fixtures="f1", scenarios="s1", lemonade="10.9.0"):
    path.write_text(
        json.dumps(
            {
                "config": {
                    "model": "claude-sonnet-4-6",
                    "agent_model": "Gemma-4-E4B-it-GGUF",
                    "fixtures": {
                        "fingerprint": {
                            "fixtures": fixtures,
                            "scenarios": scenarios,
                            "combined": fixtures + scenarios,
                        }
                    },
                    "lemonade": {"version": lemonade},
                },
                "summary": {},
                "scenarios": [
                    {"scenario_id": "a", "status": "PASS", "overall_score": 9.0}
                ],
            }
        )
    )
    return path


def test_same_fixtures_and_build_compare_without_a_warning(tmp_path):
    base = _card(tmp_path / "base.json")
    cur = _card(tmp_path / "cur.json")

    assert compare_scorecards(base, cur)["measurement_warnings"] == []


def test_different_fixtures_or_lemonade_are_flagged(tmp_path, capsys):
    base = _card(tmp_path / "base.json")
    cur = _card(tmp_path / "cur.json", fixtures="f2", lemonade="10.9.1")

    warnings = compare_scorecards(base, cur)["measurement_warnings"]

    assert any("tests/fixtures/gaia differs" in w for w in warnings)
    assert any("10.9.0 vs 10.9.1" in w for w in warnings)
    assert not any("eval/scenarios" in w for w in warnings)
    assert "[WARN] Different setup" in capsys.readouterr().err


def test_require_same_setup_refuses_a_fixture_mismatch(tmp_path):
    base = _card(tmp_path / "base.json")
    cur = _card(tmp_path / "cur.json", scenarios="s2")

    with pytest.raises(ScorecardMismatchError, match="eval/scenarios differs"):
        compare_scorecards(base, cur, require_same_setup=True)


def test_a_baseline_that_predates_fingerprints_is_not_flagged():
    old = {"config": {"model": "m"}}
    new = {"config": {"fixtures": {"fingerprint": {"combined": "x"}}}}

    assert measurement_warnings(old, new) == []


def test_the_scenario_a_run_drives_is_the_rewritten_one(tmp_path, monkeypatch):
    from gaia.eval import runner

    seen = {}
    monkeypatch.setenv(fixture_env.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(fixture_env.PORT_ENV, "51234")
    monkeypatch.setattr(
        runner,
        "run_scenario_subprocess",
        lambda _p, data, *_a, **_k: seen.setdefault("data", data),
    )
    scenario = {"id": "s", "turns": [{"user_message": "read ~/gaia-eval/x.txt"}]}

    runner.run_scripted_scenario(
        None, scenario, tmp_path, "http://127.0.0.1:1", scenario_backend=object()
    )

    message = seen["data"]["turns"][0]["user_message"]
    assert message == f"read {tmp_path.as_posix()}/x.txt"


def test_the_scorecard_records_where_its_fixtures_were_served(tmp_path, monkeypatch):
    from gaia.eval.runner import fixture_provenance

    monkeypatch.setenv(fixture_env.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(fixture_env.PORT_ENV, "51234")

    recorded = fixture_provenance()

    assert recorded["root"] == str(tmp_path)
    assert recorded["port"] == 51234
    assert len(recorded["fingerprint"]["combined"]) == 64
