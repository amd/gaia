# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""No credential reaches an agent, and none survives into anything a run writes."""

import json
from pathlib import Path

import pytest

from gaia.eval.bench import harness, leaks

FAKE_FIREWORKS = "fw_" + "A1b2C3d4E5f6G7h8J9k0"
FAKE_ANTHROPIC = "sk-ant-" + "api03-abcdefghijklmnopqrst"
FAKE_GITHUB = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123"
LEMONADE_KEY = "lemonade-local-key-" + "0123456789"
#: Split so the repository itself holds no credential-shaped string.
BEARER_WORD, BEARER_TOKEN = "Bear" + "er", "abcdefghijklmnopqrstuvwxyz"

PROVIDER_ENV = {
    "FIREWORKS_API_KEY": FAKE_FIREWORKS,
    "LEMONADE_FIREWORKS_API_KEY": FAKE_FIREWORKS,
    "LEMONADE_API_KEY": LEMONADE_KEY,
    "ANTHROPIC_API_KEY": FAKE_ANTHROPIC,
    "CLAUDE_CODE_OAUTH_TOKEN": "oauth-" + "abcdefghijklmnop",
    "GH_TOKEN": FAKE_GITHUB,
    "GITHUB_TOKEN": FAKE_GITHUB,
    "OPENAI_API_KEY": "sk-" + "proj-abcdefghijklmnopqrstu",
}


@pytest.mark.parametrize("harness_name", [harness.GAIA, harness.CLAUDE_CODE])
@pytest.mark.parametrize("model", ["fireworks.glm-5p3-flash", "claude-sonnet-5"])
def test_no_provider_key_is_in_either_agents_environment(
    monkeypatch, harness_name, model
):
    for name, value in PROVIDER_ENV.items():
        monkeypatch.setenv(name, value)
    conditions = harness.Conditions(
        time_limit_s=10,
        path_prefix=("/stub/bin", "/venv/bin"),
        extra_env={},
        gateway_url="http://127.0.0.1:1234",
    )
    env = harness.conditions_env(conditions, harness_name, model)
    for name in PROVIDER_ENV:
        assert name not in env, name
    values = set(env.values())
    assert not values & set(PROVIDER_ENV.values())
    # The only credential-named value an agent sees is the gateway's placeholder.
    named = {k: v for k, v in env.items() if leaks.is_secret_name(k)}
    assert set(named.values()) <= {harness.GATEWAY_PLACEHOLDER}


def test_the_scrubber_redacts_known_values_and_secret_shapes():
    scrubber = leaks.Scrubber.from_environment(
        {"LEMONADE_API_KEY": LEMONADE_KEY, "HOME": "/home/x", "SHORT_KEY": "abc"}
    )
    text = (
        f"env: LEMONADE_API_KEY={LEMONADE_KEY}\n"
        f"curl -H 'Authorization: {BEARER_WORD} {BEARER_TOKEN}' {FAKE_FIREWORKS}\n"
        f"{FAKE_ANTHROPIC} {FAKE_GITHUB} AKIAABCDEFGHIJKLMNOP"
    )
    clean = scrubber.scrub(text)
    for secret in (LEMONADE_KEY, FAKE_FIREWORKS, FAKE_ANTHROPIC, FAKE_GITHUB):
        assert secret not in clean
    assert BEARER_TOKEN not in clean
    assert f"{BEARER_WORD} [REDACTED:bearer]" in clean
    assert scrubber.leaks(clean) == []
    # A path or a short value is not a secret to hunt for.
    assert "/home/x" not in scrubber._values and "abc" not in scrubber._values


def test_a_secret_that_survives_redaction_stops_the_write(tmp_path, monkeypatch):
    scrubber = leaks.Scrubber([LEMONADE_KEY])
    monkeypatch.setattr(scrubber, "scrub", lambda text: text)
    target = tmp_path / "transcript.json"
    with pytest.raises(leaks.SecretLeakError) as raised:
        scrubber.write_json(target, {"answer": f"the key is {LEMONADE_KEY}"})
    assert not target.exists()
    # The error names the kind of secret, never its value.
    assert LEMONADE_KEY not in str(raised.value)


def test_scrubbed_json_stays_valid(tmp_path):
    scrubber = leaks.Scrubber([LEMONADE_KEY])
    target = tmp_path / "t.json"
    scrubber.write_json(target, {"stdout": f"LEMONADE_API_KEY={LEMONADE_KEY}"})
    data = json.loads(target.read_text())
    assert data == {"stdout": "LEMONADE_API_KEY=[REDACTED:known-secret]"}


def test_agent_env_puts_the_stand_in_and_toolchain_first():
    env = leaks.agent_env(
        {"PATH": "/usr/bin", "API_TOKEN": "x" * 20, "HOME": "/h"},
        path_prefix=["/stub", "/venv/bin"],
        extra={"GH_CONFIG_DIR": "/empty"},
    )
    assert env == {
        "PATH": f"/stub{__import__('os').pathsep}/venv/bin{__import__('os').pathsep}/usr/bin",
        "HOME": "/h",
        "GH_CONFIG_DIR": "/empty",
    }


def test_a_run_writes_no_secret_even_when_the_agent_prints_one(
    fake_agent_run, tmp_path, monkeypatch
):
    monkeypatch.setenv("LEMONADE_API_KEY", LEMONADE_KEY)
    card, out = fake_agent_run(
        answer=f"I found the key: {LEMONADE_KEY} and {FAKE_FIREWORKS}",
        tool_output=f"FIREWORKS_API_KEY={FAKE_FIREWORKS}\nLEMONADE_API_KEY={LEMONADE_KEY}",
    )
    written = "".join(p.read_text() for p in Path(out).rglob("*") if p.is_file())
    assert LEMONADE_KEY not in written and FAKE_FIREWORKS not in written
    assert "[REDACTED" in written
