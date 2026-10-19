# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The SWE-bench model preflight skips a PR only for an upstream outage.

A model Fireworks itself will not serve is not something a PR can fix, so the
PR smoke run must not go red on it. Anything GAIA or the repo owns (the key,
Lemonade's routing) must still fail, on every event.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import swebench_model_preflight as pf  # noqa: E402

MODEL = "fireworks.deepseek-v4p1-flash"


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
def test_model_answering_through_lemonade_is_ready(event):
    d = pf.decide(event, MODEL, 200, None)
    assert d.ready and not d.fail


@pytest.mark.parametrize("direct", [0, 404, 429, 500, 502, 503])
def test_upstream_outage_skips_a_pr_with_a_warning_not_a_red_run(direct):
    d = pf.decide("pull_request", MODEL, 404, direct)
    assert not d.ready and not d.fail
    assert d.level == "warning"
    assert "nothing was measured" in d.message


@pytest.mark.parametrize("direct", [0, 404, 429, 503])
def test_upstream_outage_fails_a_dispatch(direct):
    d = pf.decide("workflow_dispatch", MODEL, 404, direct)
    assert not d.ready and d.fail and d.level == "error"


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
@pytest.mark.parametrize("direct", [401, 403])
def test_a_refused_key_fails_even_on_a_pr(event, direct):
    d = pf.decide(event, MODEL, 401, direct)
    assert d.fail and "FIREWORKS_API_KEY" in d.message


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
@pytest.mark.parametrize("via_lemonade", [0, 404, 500])
def test_fireworks_serving_but_lemonade_not_is_gaia_breakage(event, via_lemonade):
    # The 404 a PR would skip on must not hide a Lemonade routing regression.
    d = pf.decide(event, MODEL, via_lemonade, 200)
    assert d.fail and "routing is broken" in d.message


@pytest.mark.parametrize("direct", [400, 422])
def test_an_unexpected_status_fails_on_a_pr(direct):
    assert pf.decide("pull_request", MODEL, 400, direct).fail


def test_direct_probe_result_is_required_when_lemonade_did_not_answer():
    with pytest.raises(ValueError):
        pf.decide("pull_request", MODEL, 404, None)


@pytest.mark.parametrize(
    "model, expected",
    [
        (MODEL, "accounts/fireworks/models/deepseek-v4p1-flash"),
        (
            "fireworks.accounts/fireworks/routers/glm-5p3-fast",
            "accounts/fireworks/routers/glm-5p3-fast",
        ),
        ("deepseek-v4p1-flash", "accounts/fireworks/models/deepseek-v4p1-flash"),
    ],
)
def test_fireworks_model_id(model, expected):
    assert pf.fireworks_model_id(model) == expected


def test_main_writes_ready_false_and_exits_zero_on_a_pr_outage(tmp_path, monkeypatch):
    out, summary = tmp_path / "out", tmp_path / "summary"
    codes = iter([404, 404])
    monkeypatch.setattr(pf, "probe", lambda *a, **k: next(codes))
    for k, v in {
        "SWE_MODEL": MODEL,
        "LEMONADE_BASE_URL": "http://127.0.0.1:1/api/v1",
        "LEMONADE_API_KEY": "x",
        "LEMONADE_FIREWORKS_API_KEY": "y",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
    }.items():
        monkeypatch.setenv(k, v)
    assert pf.main() == 0
    assert out.read_text() == "ready=false\n"
    assert "skipped" in summary.read_text()


def test_main_exits_one_on_a_dispatch_outage(tmp_path, monkeypatch):
    codes = iter([404, 404])
    monkeypatch.setattr(pf, "probe", lambda *a, **k: next(codes))
    for k, v in {
        "SWE_MODEL": MODEL,
        "LEMONADE_BASE_URL": "http://127.0.0.1:1/api/v1",
        "LEMONADE_API_KEY": "x",
        "LEMONADE_FIREWORKS_API_KEY": "y",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_OUTPUT": str(tmp_path / "out"),
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert pf.main() == 1
    assert (tmp_path / "out").read_text() == "ready=false\n"


def test_probe_reports_zero_when_nothing_answers():
    assert pf.probe("http://127.0.0.1:1/chat/completions", "k", MODEL, timeout=2) == 0
