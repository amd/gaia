# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Automations the Discord bridge runs and delivers, and its GitHub watches."""

from __future__ import annotations

import io

import pytest

from gaia.automations import store
from gaia.messaging.discord import cli, github_watch, state

from .test_discord_adapter import DM, make, message


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_HOME", raising=False)


# ----------------------------------------------------------------------
# Automations, run and delivered by the bridge
# ----------------------------------------------------------------------


def _summary(clock, name="Digest", prompt="summarize my PRs"):
    return store.add(
        store.Automation(
            "",
            name,
            store.KIND_SUMMARY,
            prompt=prompt,
            every_seconds=600,
            next_at=clock.now - 1,
        )
    )


def test_a_due_summary_runs_on_tick_and_answers_in_the_dm():
    ad, clock = make()
    ad.on_ready()
    _summary(clock)
    ad.tick()
    turns = ad._agent().turns
    assert len(turns) == 1 and turns[0].text.startswith("summarize my PRs")
    assert "since " in turns[0].text and "UTC" in turns[0].text, "an exact window"
    assert turns[0].context.channel_id == DM
    assert ad.transport.sent[-1]["text"].startswith("🗓️ **Digest**")
    ad._on_agent_event({"type": "final", "answer": "3 merged."}, turns[0])
    answer = ad.transport.sent[-1]["text"]
    assert answer == "🗓️ **Digest**\n3 merged.", answer


def test_an_alert_shows_nothing_until_it_has_news():
    ad, clock = make()
    ad.on_ready()
    store.add(
        store.Automation(
            "",
            "Rainier window",
            store.KIND_ALERT,
            prompt="find a weekend",
            every_seconds=6 * 3600,
            next_at=clock.now - 1,
        )
    )
    sent = len(ad.transport.sent)
    ad.tick()
    turn = ad._agent().turns[-1]
    ad._on_agent_event({"type": "tool_call", "tool": "search_web"}, turn)
    assert len(ad.transport.sent) == sent, "no placeholder or progress for an alert"
    ad._on_agent_event({"type": "final", "answer": "NOTHING_TO_REPORT"}, turn)
    assert len(ad.transport.sent) == sent, "nothing to report means silence"

    store.update("Rainier window", next_at=clock.now - 1)
    ad.tick()
    turn = ad._agent().turns[-1]
    ad._on_agent_event(
        {"type": "final", "answer": "Sat 14 looks climbable\n[forecast](https://x)"},
        turn,
    )
    assert ad.transport.sent[-1]["text"].startswith(
        "🔔 **Rainier window**\nSat 14 looks climbable"
    )


def test_a_bridge_that_lost_the_lease_runs_no_automations():
    ad, clock = make()
    ad.on_ready()
    _summary(clock)
    ad._set_state(state.STATE_TAKEN_OVER)
    ad.tick()
    assert ad._agent().turns == []


def _zero_fill(path):
    # What NTFS leaves when a crash keeps the rename but not the bytes.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * 971)


def test_a_damaged_automations_file_is_reported_once_and_watches_still_run(
    monkeypatch,
):
    ad, clock = make()
    ad.on_ready()
    _zero_fill(store.path())
    polled = []
    monkeypatch.setattr(
        ad, "_watcher", lambda: type("W", (), {"poll": lambda s: polled.append(1)})()
    )
    ad.tick()
    warnings = [m["text"] for m in ad.transport.sent if "damaged" in m["text"]]
    assert len(warnings) == 1 and "automations again" in warnings[0]
    assert polled == [1], "a damaged automations file must not stop GitHub alerts"
    ad.tick()
    assert len([m for m in ad.transport.sent if "damaged" in m["text"]]) == 1
    assert store.load() == []


def test_a_damaged_watches_file_is_reported_and_automations_still_run():
    ad, clock = make()
    ad.on_ready()
    _summary(clock)
    _zero_fill(github_watch.path())
    ad.tick()
    assert len(ad._agent().turns) == 1
    assert any("watch your repos again" in m["text"] for m in ad.transport.sent)


def test_a_bang_message_is_just_a_question_now():
    ad, clock = make()
    ad.on_ready()
    ad.handle_message(message(clock, "!every 1h do something"))
    assert store.load() == [], "automations come from the skill, not DM syntax"
    assert len(ad._agent().turns) == 1


# ----------------------------------------------------------------------
# GitHub watch
# ----------------------------------------------------------------------


def _issue(id_, number, login, created, pr=False, user_type="User"):
    item = {
        "id": id_,
        "number": number,
        "title": f"title {number}",
        "user": {"login": login, "type": user_type},
        "created_at": created,
        "html_url": f"https://github.com/amd/gaia/issues/{number}",
    }
    if pr:
        item["pull_request"] = {}
    return item


def _comment(id_, number, login, created, body="looks good"):
    return {
        "id": id_,
        "user": {"login": login, "type": "User"},
        "created_at": created,
        "body": body,
        "issue_url": f"https://api.github.com/repos/amd/gaia/issues/{number}",
        "html_url": f"https://github.com/amd/gaia/issues/{number}#c{id_}",
    }


SINCE = "2026-11-05T20:00:00Z"
AFTER = "2026-11-05T20:05:00Z"
BEFORE = "2026-11-05T19:00:00Z"


def test_only_third_party_activity_is_reported():
    watch = github_watch.Watch(
        "amd/gaia", ["kovtcharov", "kovtcharov-amd"], since=SINCE
    )
    items = github_watch.third_party_items(
        watch,
        issues=[
            _issue(1, 10, "alice", AFTER, pr=True),
            _issue(2, 11, "kovtcharov", AFTER),
            _issue(3, 12, "Kovtcharov-AMD", AFTER, pr=True),
            _issue(4, 13, "dependabot[bot]", AFTER, pr=True, user_type="Bot"),
            _issue(5, 14, "bob", BEFORE),
        ],
        comments=[
            _comment(7, 10, "carol", AFTER),
            _comment(8, 11, "kovtcharov", AFTER),
        ],
        review_comments=[],
    )
    assert [(i.author, i.kind, i.number) for i in items] == [
        ("alice", "PR", 10),
        ("carol", "comment", 10),
    ]
    assert items[1].title == "title 10"


def test_an_item_is_reported_once():
    watch = github_watch.Watch("amd/gaia", [], since=SINCE, seen=["issue:1"])
    items = github_watch.third_party_items(
        watch, [_issue(1, 10, "alice", AFTER)], [], []
    )
    assert items == []


def test_the_first_check_records_a_start_instead_of_flooding():
    github_watch.add("amd/gaia", ["kovtcharov"])
    posted, fetched = [], []
    poller = github_watch.Poller(
        posted.append,
        fetch=lambda *a: fetched.append(a) or [],
        token=lambda: "t",
        clock=lambda: 1_800_000_000.0,
    )
    poller.poll()
    assert posted == [] and fetched == []
    assert github_watch.load()[0].since


def test_the_poller_posts_news_and_remembers_it(monkeypatch):
    github_watch.add("amd/gaia", ["kovtcharov"])
    watches = github_watch.load()
    watches[0].since = SINCE
    github_watch.save(watches)
    now = [github_watch._parse_iso(AFTER) + 300]

    def fetch(url, params, token):
        assert token == "t"
        if url.endswith("/issues"):
            return [_issue(1, 10, "alice", AFTER, pr=True)]
        return []

    posted = []
    poller = github_watch.Poller(
        posted.append, fetch=fetch, token=lambda: "t", clock=lambda: now[0]
    )
    poller.poll()
    assert len(posted) == 1 and "alice" in posted[0] and "#10" in posted[0]
    now[0] += github_watch.POLL_SECONDS_AUTHENTICATED + 1
    poller.poll()
    assert len(posted) == 1, "the same PR must not be reported twice"


def test_a_failed_check_is_logged_and_retried_later():
    github_watch.add("amd/gaia", [])
    watches = github_watch.load()
    watches[0].since = SINCE
    github_watch.save(watches)

    def fetch(*_):
        raise RuntimeError("rate limited")

    posted = []
    github_watch.Poller(
        posted.append, fetch=fetch, token=lambda: None, clock=lambda: 2e9
    ).poll()
    assert posted == []
    assert github_watch.load()[0].since == SINCE, "a failed check must not skip ahead"


@pytest.mark.parametrize(
    "repo,expected",
    [("amd/gaia", "amd/gaia"), ("https://github.com/AMD/gaia/", "amd/gaia")],
)
def test_repo_names_are_normalized(repo, expected):
    assert github_watch.add(repo, []).repo == expected


def test_a_bad_repo_name_is_refused():
    with pytest.raises(github_watch.WatchError, match="owner/name"):
        github_watch.add("gaia", [])


def test_cli_watch_round_trip(monkeypatch):
    monkeypatch.setattr(github_watch, "github_token", lambda: "t")
    monkeypatch.setattr(github_watch, "check_repo", lambda repo, token: None)
    out = []
    assert (
        cli.run_watch(
            repo="amd/gaia", mine="kovtcharov, @kovtcharov-amd", emit=out.append
        )
        == 0
    )
    assert "every 2 minutes" in out[-1]
    assert github_watch.load()[0].own_accounts == ["kovtcharov", "kovtcharov-amd"]
    assert cli.run_unwatch(repo="amd/gaia", emit=out.append) == 0
    assert github_watch.load() == []


def test_the_token_is_looked_up_once_a_watch_exists():
    lookups = []
    poller = github_watch.Poller(
        lambda _: None, fetch=lambda *a: [], token=lambda: lookups.append(1) or "t"
    )
    poller.poll()
    assert lookups == [], "no watch yet, no lookup"
    github_watch.add("amd/gaia", [])
    poller.poll()
    poller.poll()
    assert lookups == [1]
    assert poller.interval(1) == github_watch.POLL_SECONDS_AUTHENTICATED


def test_format_items_caps_the_post():
    items = [
        github_watch.Item(f"k{i}", "PR", i, "t", "alice", "https://x")
        for i in range(15)
    ]
    text = github_watch.format_items("amd/gaia", items)
    assert "and 5 more" in text
    assert io.StringIO(text).read().count("alice") == github_watch.MAX_ITEMS_PER_POST


def test_cli_watch_refuses_a_repo_github_cannot_see(monkeypatch):
    def missing(repo, token):
        raise github_watch.WatchError("GitHub shows no repository amd/gaai.")

    monkeypatch.setattr(github_watch, "github_token", lambda: None)
    monkeypatch.setattr(github_watch, "check_repo", missing)
    out = []
    assert cli.run_watch(repo="amd/gaai", mine="", emit=out.append) == 2
    assert "no repository" in out[-1]
    assert github_watch.load() == []


def test_anonymous_checks_slow_down_with_more_repos():
    poller = github_watch.Poller(lambda _: None, token=lambda: None)
    poller.poll()
    assert poller.interval(4) == 4 * github_watch.POLL_SECONDS_ANONYMOUS_PER_REPO


def test_a_watch_that_keeps_failing_says_so_once_and_again_when_it_recovers():
    github_watch.add("amd/gaia", [])
    watches = github_watch.load()
    watches[0].since = SINCE
    github_watch.save(watches)
    clock = [2e9]
    failing = [True]

    def fetch(*_):
        if failing[0]:
            raise RuntimeError("HTTP 404")
        return []

    posted = []
    poller = github_watch.Poller(
        posted.append, fetch=fetch, token=lambda: "t", clock=lambda: clock[0]
    )
    for _ in range(github_watch.FAILURES_BEFORE_ALERT + 2):
        poller.poll()
        clock[0] += github_watch.POLL_SECONDS_AUTHENTICATED + 1
    assert len(posted) == 1 and "couldn't check" in posted[0]
    failing[0] = False
    poller.poll()
    assert "again" in posted[-1]


def test_a_watch_added_during_a_check_survives_it():
    github_watch.add("amd/gaia", [])
    watches = github_watch.load()
    watches[0].since = SINCE
    github_watch.save(watches)

    def fetch(*_):
        # The agent adds a watch while this check is on the network.
        if not any(w.repo == "amd/other" for w in github_watch.load()):
            github_watch.add("amd/other", [])
        return []

    github_watch.Poller(
        lambda _: None, fetch=fetch, token=lambda: "t", clock=lambda: 2e9
    ).poll()
    assert {w.repo for w in github_watch.load()} == {"amd/gaia", "amd/other"}


def test_github_text_cannot_become_a_masked_link():
    item = github_watch.Item(
        "k",
        "PR",
        1,
        "[safe](https://evil.example) **x**",
        "eve",
        "https://github.com/x",
    )
    text = github_watch.format_items("amd/gaia", [item])
    assert "[safe](https://evil.example)" not in text
    assert "\\[safe\\]" in text


def test_an_approval_from_an_automation_says_which_one_asks():
    ad, clock = make()
    ad.on_ready()
    store.add(
        store.Automation(
            "",
            "Rainier window",
            store.KIND_ALERT,
            prompt="find a weekend",
            every_seconds=6 * 3600,
            next_at=clock.now - 1,
        )
    )
    ad.tick()
    turn = ad._agent().turns[-1]
    ad._on_agent_event(
        {
            "type": "needs_confirmation",
            "action": "write_file",
            "confirm_id": "c-1",
            "summary": "notes.md",
        },
        turn,
    )
    assert (
        "Asked by the automation 🔔 **Rainier window**" in ad.transport.sent[-1]["text"]
    )


def test_an_agent_failure_in_an_alert_is_shown_but_not_kept_as_news():
    ad, clock = make()
    ad.on_ready()
    store.add(
        store.Automation(
            "",
            "CI",
            store.KIND_ALERT,
            prompt="is main red?",
            every_seconds=3600,
            next_at=clock.now - 1,
            last_alert="main is red",
        )
    )
    ad.tick()
    ad._on_agent_event(
        {"type": "error", "detail": "model unloaded"}, ad._agent().turns[-1]
    )
    assert "model unloaded" in ad.transport.sent[-1]["text"]
    assert store.load()[0].last_alert == "main is red"
