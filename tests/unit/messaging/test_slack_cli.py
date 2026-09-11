"""``gaia slack`` — argument handling, credentials, and the status report."""

import json

import pytest

from gaia.messaging.slack import cli, credentials, onboarding

# ----------------------------------------------------------------------
# Allowlist parsing
# ----------------------------------------------------------------------


def test_ids_are_split_and_trimmed():
    assert cli.parse_allowed_users(" U024BE7LH , U0G9QF9C6 ") == {
        "U024BE7LH",
        "U0G9QF9C6",
    }


def test_an_omitted_allowlist_is_empty_not_an_error():
    """The adapter's own refusal explains why one is mandatory; argparse's
    'the following arguments are required' does not."""
    assert cli.parse_allowed_users(None) == set()
    assert cli.parse_allowed_users("") == set()


@pytest.mark.parametrize("wrong", ["@kalin", "kalin@example.com", "12345"])
def test_a_handle_or_email_is_refused_with_the_real_instruction(wrong):
    """Pasting a display name is the obvious mistake, and it would silently
    deny the person who made it."""
    with pytest.raises(ValueError) as excinfo:
        cli.parse_allowed_users(wrong)
    assert "Copy member ID" in str(excinfo.value)


def test_one_bad_entry_fails_the_whole_list():
    with pytest.raises(ValueError):
        cli.parse_allowed_users("U024BE7LH,@kalin")


# ----------------------------------------------------------------------
# Credentials
# ----------------------------------------------------------------------


def test_the_environment_supplies_tokens_without_a_keyring(monkeypatch):
    monkeypatch.setenv(credentials.BOT_TOKEN_ENV_VAR, "xoxb-env")
    monkeypatch.setenv(credentials.APP_TOKEN_ENV_VAR, "xapp-env")
    creds = credentials.load()
    assert creds.bot_token == "xoxb-env"
    assert creds.app_token == "xapp-env"


def test_a_half_configured_environment_names_what_is_missing(monkeypatch):
    monkeypatch.setenv(credentials.BOT_TOKEN_ENV_VAR, "xoxb-env")
    monkeypatch.delenv(credentials.APP_TOKEN_ENV_VAR, raising=False)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "null")
    with pytest.raises(credentials.SlackCredentialsError) as excinfo:
        credentials.load()
    message = str(excinfo.value)
    assert credentials.APP_TOKEN_ENV_VAR in message
    assert "gaia slack setup" in message


def test_load_optional_answers_none_rather_than_raising(monkeypatch):
    monkeypatch.delenv(credentials.BOT_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(credentials.APP_TOKEN_ENV_VAR, raising=False)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "null")
    assert credentials.load_optional() is None


# ----------------------------------------------------------------------
# start
# ----------------------------------------------------------------------


def test_start_without_credentials_exits_with_the_setup_remedy(monkeypatch, capsys):
    monkeypatch.delenv(credentials.BOT_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(credentials.APP_TOKEN_ENV_VAR, raising=False)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "null")
    lines = []
    assert cli.run_start(allowed_users="U024BE7LH", emit=lines.append) == 2
    assert any("gaia slack setup" in line for line in lines)


def test_start_with_a_malformed_allowlist_fails_before_touching_the_keyring():
    lines = []
    assert cli.run_start(allowed_users="@kalin", emit=lines.append) == 2
    assert any("member ID" in line for line in lines)


def test_start_without_an_allowlist_is_refused_with_the_reason(monkeypatch):
    """Credentials present, allowlist absent — the case the adapter guards."""
    monkeypatch.setenv(credentials.BOT_TOKEN_ENV_VAR, "xoxb-env")
    monkeypatch.setenv(credentials.APP_TOKEN_ENV_VAR, "xapp-env")
    lines = []
    assert cli.run_start(allowed_users=None, emit=lines.append) == 2
    assert any("--allowed-users" in line for line in lines)


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------


def test_status_reports_an_unconfigured_machine(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "null")
    monkeypatch.delenv(credentials.BOT_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(credentials.APP_TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(onboarding, "detect_slack", lambda: False)
    info = cli.status()
    assert info["configured"] is False
    assert info["slack_installed"] is False
    assert info["should_offer_setup"] is False


def test_status_offers_setup_when_slack_appears(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "null")
    monkeypatch.delenv(credentials.BOT_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(credentials.APP_TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(onboarding, "detect_slack", lambda: True)
    assert cli.status()["should_offer_setup"] is True


def test_status_json_is_machine_readable(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "null")
    monkeypatch.setattr(onboarding, "detect_slack", lambda: False)
    lines = []
    assert cli.run_status(as_json=True, emit=lines.append) == 0
    payload = json.loads("\n".join(lines))
    assert set(payload) == {
        "slack_installed",
        "configured",
        "onboarding_state",
        "team_name",
        "should_offer_setup",
        "running",
    }


def test_stop_without_a_running_bridge_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "PID_PATH", tmp_path / "slack.pid")
    lines = []
    assert cli.run_stop(emit=lines.append) == 0
    assert any("No backgrounded" in line for line in lines)


def test_stop_clears_a_stale_pid_file(monkeypatch, tmp_path):
    """A pid that no longer exists must not leave the bridge un-startable."""
    pid_file = tmp_path / "slack.pid"
    # PID 1 is init; a kill from a normal user raises PermissionError, so use a
    # pid that genuinely cannot exist instead.
    pid_file.write_text("999999999", encoding="utf-8")
    monkeypatch.setattr(cli, "PID_PATH", pid_file)
    lines = []
    assert cli.run_stop(emit=lines.append) == 0
    assert not pid_file.exists()


# ----------------------------------------------------------------------
# setup
# ----------------------------------------------------------------------


def test_setup_refuses_swapped_tokens_before_any_network_call(monkeypatch):
    answers = iter(["xoxb-wrong-field", "xapp-wrong-field"])
    lines = []
    code = cli.run_setup(
        prompt=lambda _: next(answers), emit=lines.append, open_browser=False
    )
    assert code == 2
    assert any("goes in the other field" in line for line in lines)


def test_setup_prints_the_create_url_when_no_browser_is_available():
    answers = iter(["xapp-a", "xoxb-b"])
    lines = []
    cli.run_setup(prompt=lambda _: next(answers), emit=lines.append, open_browser=False)
    assert any("api.slack.com/apps" in line for line in lines)


def test_setup_does_not_promise_automatic_configuration():
    """Saying 'automatic' strands the user on a settings page thirty seconds
    later. The copy has to say what they will actually do."""
    answers = iter(["xapp-a", "xoxb-b"])
    lines = []
    cli.run_setup(prompt=lambda _: next(answers), emit=lines.append, open_browser=False)
    joined = " ".join(lines).lower()
    assert "cannot be fully automatic" in joined
