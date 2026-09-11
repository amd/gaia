"""When the Slack setup offer appears, and what it remembers.

The rule under test: offer once, then again only when something changed. A
prompt on every launch trains the user to dismiss it; a prompt that never
returns strands the user who installed Slack after saying no.
"""

import json

import pytest

from gaia.messaging.slack import onboarding as ob

#: One record captured from a REAL ``scan_installed_apps()`` run on macOS.
#: Verbatim on purpose: the scanner returns discovery *facts*, not rows with a
#: ``name`` field, and an invented fixture shaped like the latter is exactly how
#: a detector passes its tests while reporting that nobody has Slack.
REAL_SLACK_RECORD = {
    "content": "Installed app: Slack [Communication]",
    "category": "fact",
    "context": "unclassified",
    "entity": "app:slack",
    "sensitive": False,
    "confidence": 0.4,
    "source": "discovery",
    "approved": None,
}

REAL_OTHER_RECORD = {
    "content": "Installed app: Google Chrome [Browser]",
    "category": "fact",
    "entity": "app:google_chrome",
    "source": "discovery",
}


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------


def test_detects_slack_in_a_real_discovery_record():
    assert ob.slack_is_installed([REAL_OTHER_RECORD, REAL_SLACK_RECORD]) is True


def test_reports_absent_when_no_record_is_slack():
    assert ob.slack_is_installed([REAL_OTHER_RECORD]) is False


def test_empty_inventory_is_not_slack():
    assert ob.slack_is_installed([]) is False


def test_a_similarly_named_app_is_not_slack():
    """'Slackware Tools' must not light up the offer."""
    assert (
        ob.slack_is_installed(
            [
                {
                    "entity": "app:slackware_tools",
                    "content": "Installed app: Slackware Tools [Other]",
                }
            ]
        )
        is False
    )


def test_falls_back_to_the_content_line_when_no_entity_slug():
    assert (
        ob.slack_is_installed([{"content": "Installed app: Slack [Communication]"}])
        is True
    )


def test_content_fallback_still_rejects_a_prefix_match():
    assert (
        ob.slack_is_installed([{"content": "Installed app: Slackware [Other]"}])
        is False
    )


# ----------------------------------------------------------------------
# The offer rule
# ----------------------------------------------------------------------


def test_first_boot_with_slack_installed_offers_setup():
    assert ob.should_offer(ob.OnboardingState(), detected=True) is True


def test_first_boot_without_slack_stays_quiet():
    assert ob.should_offer(ob.OnboardingState(), detected=False) is False


def test_skipped_without_slack_then_installing_it_offers_again():
    """The user answered a different question: they had no Slack at the time."""
    state = ob.OnboardingState(state=ob.STATE_SKIPPED, detected_when_decided=False)
    assert ob.should_offer(state, detected=True) is True


def test_skipped_with_slack_installed_never_offers_again():
    """They saw the offer with Slack present and said no. Honour it."""
    state = ob.OnboardingState(state=ob.STATE_SKIPPED, detected_when_decided=True)
    assert ob.should_offer(state, detected=True) is False


def test_never_is_honoured_forever():
    for detected_then in (True, False):
        state = ob.OnboardingState(
            state=ob.STATE_NEVER, detected_when_decided=detected_then
        )
        assert ob.should_offer(state, detected=True) is False


def test_connected_does_not_re_offer():
    state = ob.OnboardingState(state=ob.STATE_CONNECTED, detected_when_decided=True)
    assert ob.should_offer(state, detected=True) is False


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


def test_missing_state_file_means_never_asked(tmp_path):
    """A fresh install is not an error — it is the starting point."""
    state = ob.load_state(tmp_path / "nope.json")
    assert state.state == ob.STATE_UNSET


def test_a_decision_round_trips(tmp_path):
    path = tmp_path / "onboarding.json"
    ob.record_decision(ob.STATE_SKIPPED, detected=False, team_name="acme", path=path)
    reloaded = ob.load_state(path)
    assert reloaded.state == ob.STATE_SKIPPED
    assert reloaded.detected_when_decided is False
    assert reloaded.team_name == "acme"
    assert reloaded.updated_at > 0


def test_saving_creates_the_parent_directory(tmp_path):
    path = tmp_path / "slack" / "onboarding.json"
    ob.save_state(ob.OnboardingState(state=ob.STATE_CONNECTED), path)
    assert path.is_file()


def test_corrupt_state_is_refused_rather_than_reset(tmp_path):
    """Silently resetting would re-prompt a user who explicitly said never."""
    path = tmp_path / "onboarding.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ob.OnboardingStateError) as excinfo:
        ob.load_state(path)
    assert "Delete the file" in str(excinfo.value), "an error must name the remedy"


def test_a_non_object_state_file_is_refused(tmp_path):
    path = tmp_path / "onboarding.json"
    path.write_text('["not", "an", "object"]', encoding="utf-8")
    with pytest.raises(ob.OnboardingStateError):
        ob.load_state(path)


def test_an_unknown_state_name_is_refused(tmp_path):
    path = tmp_path / "onboarding.json"
    path.write_text(json.dumps({"state": "sideways"}), encoding="utf-8")
    with pytest.raises(ob.OnboardingStateError):
        ob.load_state(path)


def test_unknown_keys_in_the_file_are_ignored(tmp_path):
    """Forward compatibility: a newer GAIA's extra key must not break an older one."""
    path = tmp_path / "onboarding.json"
    path.write_text(
        json.dumps({"state": ob.STATE_NEVER, "future_field": 1}), encoding="utf-8"
    )
    assert ob.load_state(path).state == ob.STATE_NEVER


def test_constructing_an_invalid_state_is_refused():
    with pytest.raises(ValueError):
        ob.OnboardingState(state="banana")


def test_state_path_honours_a_relocated_gaia_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path))
    assert ob._state_path() == tmp_path / "slack" / "onboarding.json"
