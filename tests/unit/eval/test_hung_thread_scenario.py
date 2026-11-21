# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pin the answer key of the hung-thread email scenario against its fixture.

A correct agent must not be failed for naming mail that really does ask for a
reply, or for calling ``list_waiting_on_you`` (which is empty on this fixture).
"""

import mailbox
import re
from email.utils import parseaddr
from pathlib import Path

import pytest
import yaml

from gaia.eval.runner import SCENARIOS_DIR, validate_scenario

SCENARIO = SCENARIOS_DIR / "gaia_email" / "email_t3_hung_thread_followups.yaml"
MBOX = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "gaia"
    / "email"
    / "eval_inbox.mbox"
)
USER_ADDR = "user@example.com"


@pytest.fixture(scope="module")
def scenario():
    data = yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))
    validate_scenario(SCENARIO, data)
    return data


@pytest.fixture(scope="module")
def messages():
    return list(mailbox.mbox(str(MBOX)))


def _turn(scenario, n):
    return next(t for t in scenario["turns"] if t["turn"] == n)


def _critical_clause(criteria: str) -> str:
    match = re.search(r"CRITICAL FAIL(.*)", criteria, re.S)
    assert match, "turn 1 must keep a CRITICAL FAIL clause"
    return match.group(1)


def test_alex_thread_is_in_the_fixture(messages):
    alex = [m for m in messages if parseaddr(m["From"])[1] == "alex@company.example"]
    assert any("blocked on review" in (m["Subject"] or "") for m in alex)


def test_fixture_has_no_message_from_the_user(messages):
    # The scenario's claim that list_waiting_on_you returns nothing rests on this.
    senders = {parseaddr(m["From"])[1].lower() for m in messages}
    assert USER_ADDR not in senders


def test_turn1_asks_about_promises_not_any_reply(scenario):
    message = _turn(scenario, 1)["user_message"].lower()
    assert "promised" in message


@pytest.mark.parametrize(
    "reply_owed",
    ["boss", "q4 budget", "escalation", "lunch", "marcus"],
)
def test_reply_owed_mail_is_not_a_critical_fail(scenario, reply_owed):
    clause = _critical_clause(_turn(scenario, 1)["success_criteria"]).lower()
    assert reply_owed not in clause


@pytest.mark.parametrize("fyi", ["sam", "hr enrollment", "priya"])
def test_pure_fyis_stay_a_critical_fail(scenario, fyi):
    clause = _critical_clause(_turn(scenario, 1)["success_criteria"]).lower()
    assert fyi in clause


def test_empty_waiting_list_must_be_cross_checked(scenario):
    criteria = _turn(scenario, 1)["success_criteria"]
    assert "list_waiting_on_you" in criteria
    assert "empty" in criteria


def test_turn3_accepts_chat_reply_and_forbids_sending(scenario):
    criteria = _turn(scenario, 3)["success_criteria"].lower()
    assert "written in chat is enough" in criteria
    assert "sending it without the user asking is a fail" in criteria
