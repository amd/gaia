# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Precision gate and adversarial cases for the flagship's waiting-on-you scan.

A port of the email agent's corpus gate (``tests/unit/email/
test_waiting_on_you_precision.py``), run through the flagship's real Gmail and
Graph backends instead of the email agent's fake. One false "someone is
waiting on you" costs more trust than several misses, so the corpus numbers
are asserted exactly, and every construction that once manufactured a false
positive is re-run against both providers.
"""

import json
import time
from pathlib import Path

import pytest
from _fake_mail_api import NOW_MS, USER, FakeGmailApi, FakeGraphApi, Mail, outbound

from gaia.agents.tools._email import waiting
from gaia.agents.tools._email.errors import MailboxError
from gaia.agents.tools._email.waiting import detect_waiting_on_you

_CORPUS = (
    Path(__file__).resolve().parents[4]
    / "tests"
    / "fixtures"
    / "email"
    / "vendor_corpus_seed.jsonl"
)

SUBSTANTIVE = (
    "Sure, I will take a look at the numbers and get back to you before the review."
)


def _corpus():
    with open(_CORPUS, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _promotional():
    return [r for r in _corpus() if r.get("category") == "PROMOTIONAL"]


def _fires(rec):
    s, b = (rec.get("subject") or ""), (rec.get("body") or "")
    return (
        waiting.has_direct_ask_signal(s.lower(), b.lower())
        or waiting.has_meeting_time_signal(s.lower(), b.lower())
        or waiting.is_meeting_request(s, b)
    )


def _candidate(rec, thread=None):
    return Mail(
        rec["id"],
        thread or rec["id"],
        sender=rec.get("sender") or "sender@example.com",
        to=", ".join(rec.get("to") or [USER]),
        subject=rec.get("subject") or "",
        body=rec.get("body") or "",
        age_days=1,
    )


def _scan(api_cls, mails, **kwargs):
    return detect_waiting_on_you(api_cls(mails).backend(), now_ms=NOW_MS, **kwargs)


@pytest.fixture(params=[FakeGmailApi, FakeGraphApi], ids=["gmail", "graph"])
def api(request):
    return request.param


# --------------------------------------------------------------------------
# corpus — the measurements the email agent's gate pins
# --------------------------------------------------------------------------


def test_corpus_is_the_one_the_email_agent_measured():
    rows = _promotional()
    assert len(rows) == 104
    assert sum(1 for r in rows if r.get("promotional_subtype") == "adversarial") == 42


def test_signal_firing_subset_matches_the_email_agent():
    """19 rows fire a signal on text alone — the same count the email agent's
    gate locks, which is what proves the predicates were ported faithfully."""
    assert len([r for r in _promotional() if _fires(r)]) == 19


def test_zero_false_positives_over_the_promotional_corpus(api):
    rows = _promotional()
    false_positives = [
        r["id"] for r in rows if _scan(api, [_candidate(r)])["waiting_on_you"]
    ]
    assert false_positives == []


@pytest.mark.parametrize(
    "prior",
    [
        # 6a: an opt-out-shaped one-liner from the user, in the same thread.
        ("same", "Please remove me from this list."),
        # 6b: one short unrelated inquiry in another thread.
        ("other", "what's the pricing?"),
        # 6c: a genuine, substantive message — but in a DIFFERENT thread.
        (
            "other",
            "Thanks for reaching out. Could you send over pricing for the "
            "enterprise tier and whether it includes SSO?",
        ),
    ],
    ids=["6a-opt-out-in-thread", "6b-one-liner-elsewhere", "6c-substantive-elsewhere"],
)
def test_manufactured_corroboration_never_qualifies(api, prior):
    where, body = prior
    false_positives = []
    for rec in [r for r in _promotional() if _fires(r)]:
        sender = waiting.sender_address(rec.get("sender") or "") or "sender@example.com"
        thread = rec["id"] if where == "same" else f"{rec['id']}-other"
        mails = [
            outbound(f"{rec['id']}-p", thread, to=sender, body=body, age_days=30),
            _candidate(rec),
        ]
        if _scan(api, mails)["waiting_on_you"]:
            false_positives.append(rec["id"])
    assert false_positives == []


# --------------------------------------------------------------------------
# recall — the cases this feature exists for
# --------------------------------------------------------------------------


def _thread(
    latest_body,
    *,
    prior=(SUBSTANTIVE,),
    sender="Alice <alice@example.com>",
    kickoff="Kickoff.",
    **kw,
):
    addr = waiting.sender_address(sender)
    mails = [Mail("a0", "t1", sender=sender, body=kickoff, age_days=20)]
    for i, body in enumerate(prior):
        mails.append(outbound(f"s{i}", "t1", to=addr, body=body, age_days=10 - i))
    mails.append(Mail("r1", "t1", sender=sender, body=latest_body, age_days=3, **kw))
    return mails


def test_a_direct_ask_in_a_real_conversation_qualifies(api):
    out = _scan(api, _thread("Could you please confirm the numbers by Friday?"))
    assert [i["message_id"] for i in out["waiting_on_you"]] == ["r1"]
    assert out["waiting_on_you"][0]["signal"] == "direct_ask"
    assert out["waiting_on_you"][0]["corroboration"] == "thread_reply_history"


def test_an_informal_meeting_ask_qualifies(api):
    out = _scan(api, _thread("Any chance to meet this Thursday at 9am?"))
    assert out["waiting_on_you"][0]["signal"] == "meeting_time"


def test_two_short_replies_from_the_user_are_a_conversation(api):
    out = _scan(api, _thread("Can you send it over?", prior=("ok", "thanks")))
    assert len(out["waiting_on_you"]) == 1


def test_an_html_body_is_read_as_text():
    """Outlook bodies arrive as HTML; tags must not hide the ask."""
    body = (
        "<p>Could <b>you</b> please <i>confirm</i>?</p><p>could you please confirm</p>"
    )
    out = _scan(FakeGraphApi, _thread(body, html=True))
    assert len(out["waiting_on_you"]) == 1


def test_an_auto_reply_header_is_not_a_waiting_request(api):
    mails = _thread("Could you please confirm?", labels=["INBOX", "AUTO_REPLY"])
    assert _scan(api, mails)["waiting_on_you"] == []


OLD_ASK = "Could you please confirm the numbers?"


def _asks(api, body, **kw):
    out = _scan(api, _thread(body, kickoff=OLD_ASK, **kw))
    return [i["message_id"] for i in out["waiting_on_you"]]


def test_a_quoted_old_ask_is_not_a_new_request(api):
    body = f"Thanks for the update.\n\nOn Monday Alice wrote:\n{OLD_ASK}"
    assert _asks(api, body) == []


def test_a_cc_only_request_is_not_assigned_to_the_user(api):
    mails = _thread(
        "Could you please confirm?",
        to="Owner <owner@example.com>",
        cc=f"Me <{USER}>",
    )
    assert _scan(api, mails)["waiting_on_you"] == []


_GMAIL_HTML_QUOTE = (
    '<div dir="ltr">Thanks for the update.</div><br><div class="gmail_quote">'
    '<div dir="ltr" class="gmail_attr">On Mon, Alice wrote:<br></div>'
    "<blockquote>Could you please confirm the numbers?</blockquote></div>"
)
_OUTLOOK_HTML_QUOTE = (
    "<html><body><div>Thanks for the update.</div>"
    '<div id="appendonsend"></div><hr style="display:inline-block;width:98%">'
    '<div id="divRplyFwdMsg" dir="ltr"><b>From:</b> Alice<br><b>Sent:</b> Monday'
    "</div><div>Could you please confirm the numbers?</div></body></html>"
)


@pytest.mark.parametrize("body", [_GMAIL_HTML_QUOTE, _OUTLOOK_HTML_QUOTE])
def test_an_html_quoted_old_ask_is_not_a_new_request(api, body):
    assert _asks(api, body, html=True) == []


@pytest.mark.parametrize("body", [_GMAIL_HTML_QUOTE, _OUTLOOK_HTML_QUOTE])
def test_an_html_new_ask_above_a_quote_still_qualifies(api, body):
    body = body.replace("Thanks for the update.", "Could you send the deck?")
    assert _asks(api, body, html=True) == ["r1"]


# --------------------------------------------------------------------------
# an answer written below or inside a quote is new text, not quoted history
# --------------------------------------------------------------------------


def test_a_bottom_posted_plain_text_ask_qualifies(api):
    body = f"On Monday Alice wrote:\n> {OLD_ASK}\n\nCould you send the deck too?"
    assert _asks(api, body) == ["r1"]


def test_an_inline_plain_text_ask_under_the_attribution_qualifies(api):
    body = (
        f"On Monday Alice wrote:\n> {OLD_ASK}\nDone. Could you send the deck?"
        "\n> Thanks.\n"
    )
    assert _asks(api, body) == ["r1"]


def test_an_html_ask_after_a_blockquote_qualifies(api):
    body = (
        '<div class="gmail_quote"><div class="gmail_attr">On Mon, Alice wrote:'
        f"</div><blockquote>{OLD_ASK}</blockquote></div>"
        "<div>Done. Could you send the deck?</div>"
    )
    assert _asks(api, body, html=True) == ["r1"]


def test_an_html_ask_between_two_blockquotes_qualifies(api):
    body = (
        f"<blockquote>{OLD_ASK}</blockquote><p>Could you send the deck?</p>"
        "<blockquote><blockquote>Kickoff.</blockquote></blockquote>"
    )
    assert _asks(api, body, html=True) == ["r1"]


def test_an_inline_answer_inside_outlooks_quoted_section_qualifies(api):
    body = (
        '<div>See inline.</div><div id="appendonsend"></div><hr>'
        '<div id="divRplyFwdMsg"><b>From:</b> Alice<br><b>Sent:</b> Monday<br>'
        f"<b>Subject:</b> Numbers</div><div>{OLD_ASK}</div>"
        '<div style="color:red">Could you send the deck by Friday?</div>'
    )
    assert _asks(api, body, html=True) == ["r1"]


def test_an_unclosed_blockquote_hides_the_rest(api):
    body = f"<p>Thanks.</p><blockquote><p>{OLD_ASK}</p>"
    assert _asks(api, body, html=True) == []


def _parse_seconds(body):
    message = {"body": body, "body_content_type": "html"}
    started = time.perf_counter()
    waiting.new_text(message, waiting.quoted_lines([message]))
    waiting.strip_quoted_text(body.replace("<", "a"))
    return time.perf_counter() - started


# A builder's divisor scales its input: 1 is full size, _SHRINK a fraction.
_SHRINK = 8


@pytest.mark.parametrize(
    "build",
    [
        lambda d: "<" * (300_000 // d) + "\nFrom: x" * (25_000 // d),
        lambda d: "<" + "appendonsend " * (40_000 // d),
        lambda d: "<" + "divRplyFwdMsg moz-cite-prefix " * (17_000 // d),
        lambda d: "<blockquote " * (40_000 // d),
        lambda d: "<br " * (120_000 // d),
        lambda d: ("<div id=appendonsend>" + "<" * 20) * (12_000 // d),
    ],
    ids=[
        "unclosed-and-headers",
        "marker-run",
        "mixed-markers",
        "blockquote-run",
        "break-run",
        "marker-tags-and-unclosed",
    ],
)
def test_large_malformed_html_and_header_lines_stay_fast(build):
    # Timed against the same input at 1/8 size, so runner speed cancels out:
    # linear work grows ~8x, quadratic ~64x.
    small, large = build(_SHRINK), build(1)
    best_small = best_large = float("inf")
    for _ in range(3):
        best_small = min(best_small, _parse_seconds(small))
        best_large = min(best_large, _parse_seconds(large))
        if best_large < 3 * _SHRINK * best_small:
            break
    assert best_large < 3 * _SHRINK * best_small, (
        f"{len(large):,} chars took {best_large:.3f}s, "
        f"{best_large / best_small:.0f}x the {best_small:.4f}s for 1/{_SHRINK} "
        f"of it; linear parsing would be ~{_SHRINK}x"
    )
    assert best_large < 15, f"{len(large):,} chars took {best_large:.1f}s"


def test_an_outlook_plain_text_header_block_is_not_a_new_request(api):
    body = (
        "Thanks, noted.\n\n________________________________\nFrom: Alice\n"
        f"Sent: Monday, October 26, 2026 9:00 AM\nTo: Me\n{OLD_ASK}"
    )
    assert _asks(api, body) == []


def test_a_from_line_without_a_sent_line_is_not_a_quote(api):
    body = "From: the finance team side, could you please confirm the numbers?"
    assert len(_scan(api, _thread(body))["waiting_on_you"]) == 1


def test_quote_stripping_keeps_the_new_text_line_breaks():
    body = "Hi,\nCould you confirm?\n> old line\nOn Monday Alice wrote:\nolder ask"
    stripped = waiting.strip_quoted_text(body, {"older ask"})
    assert stripped == "Hi,\nCould you confirm?"


def test_a_meeting_time_on_its_own_line_still_qualifies(api):
    out = _scan(api, _thread("Free to meet\nThursday?"))
    assert out["waiting_on_you"][0]["signal"] == "meeting_time"


def test_a_new_ask_above_a_quoted_reply_still_qualifies(api):
    body = "Could you send the deck?\n\nOn Monday Alice wrote:\nThanks for the update."
    out = _scan(api, _thread(body))
    assert [i["message_id"] for i in out["waiting_on_you"]] == ["r1"]


def test_a_chevron_quoted_ask_is_not_a_new_request(api):
    body = "Thanks, noted.\n> Could you please confirm the numbers?"
    assert _scan(api, _thread(body))["waiting_on_you"] == []


def test_an_outlook_original_message_block_is_not_a_new_request(api):
    body = f"Thanks, noted.\n\n-----Original Message-----\nFrom: Bob\n{OLD_ASK}"
    assert _asks(api, body) == []


def test_a_user_on_both_to_and_cc_is_still_asked(api):
    mails = _thread("Could you please confirm?", cc=f"Me <{USER}>")
    assert len(_scan(api, mails)["waiting_on_you"]) == 1


def test_a_user_in_cc_beside_a_differently_cased_to_is_still_asked(api):
    mails = _thread("Could you please confirm?", to=USER.upper())
    mails[-1].cc = f"Me <{USER}>"
    assert len(_scan(api, mails)["waiting_on_you"]) == 1


@pytest.mark.parametrize(
    "auto_submitted, x_autoreply, expected",
    [
        ("no", "", False),
        (" No ", "", False),
        ("", "", False),
        ("", "no", False),
        ("auto-replied", "", True),
        ("Auto-Generated", "", True),
        ("", "YES", True),
        ("", " true ", True),
    ],
)
def test_auto_reply_headers_follow_rfc_3834(auto_submitted, x_autoreply, expected):
    message = {"auto_submitted": auto_submitted, "x_autoreply": x_autoreply}
    assert waiting.is_auto_reply(message) is expected


def test_graph_reads_auto_reply_headers_case_insensitively():
    from gaia.agents.tools._email import graph

    summary = graph.message_summary(
        {
            "id": "m1",
            "internetMessageHeaders": [
                {"name": "AUTO-SUBMITTED", "value": "auto-replied"},
                {"name": None, "value": "ignored"},
            ],
        }
    )
    assert summary["auto_submitted"] == "auto-replied"
    assert summary["x_autoreply"] == ""


# --------------------------------------------------------------------------
# precision — each way a thread must NOT qualify
# --------------------------------------------------------------------------


def test_one_short_prior_message_is_not_a_conversation(api):
    out = _scan(api, _thread("Can you send it over?", prior=("thanks",)))
    assert out["waiting_on_you"] == []


def test_a_vendor_intro_plus_one_word_from_the_user_is_not_two_messages(api):
    """Only the USER's own prior messages count toward the two-message bar."""
    mails = [
        Mail("v0", "t1", sender="V <v@vendor.com>", body="Intro", age_days=9),
        Mail("v1", "t1", sender="V <v@vendor.com>", body="Bump", age_days=8),
        outbound("s0", "t1", to="v@vendor.com", body="thanks", age_days=7),
        Mail(
            "v2", "t1", sender="V <v@vendor.com>", body="Could you review?", age_days=2
        ),
    ]
    assert _scan(api, mails)["waiting_on_you"] == []


def test_the_user_speaking_last_means_nothing_is_owed(api):
    mails = _thread("Could you please confirm?") + [
        outbound("s9", "t1", to="alice@example.com", body=SUBSTANTIVE, age_days=1)
    ]
    assert _scan(api, mails)["waiting_on_you"] == []


def test_an_unsent_draft_reply_does_not_count_as_replying(api):
    mails = _thread("Could you please confirm?") + [
        Mail("d1", "t1", sender=f"Me <{USER}>", body="draft", age_days=1, draft=True)
    ]
    assert len(_scan(api, mails)["waiting_on_you"]) == 1


@pytest.mark.parametrize(
    "sender",
    ["Billing <noreply@shop.com>", "Alerts <alerts@ci.example>", "no-reply@x.com"],
)
def test_an_automated_sender_never_qualifies(api, sender):
    assert (
        _scan(api, _thread("Could you confirm?", sender=sender))["waiting_on_you"] == []
    )


def test_a_sender_told_to_stop_elsewhere_is_suppressed(api):
    mails = _thread("Could you please confirm?") + [
        outbound(
            "o1",
            "t-other",
            to="alice@example.com",
            body="Please unsubscribe me.",
            age_days=40,
        )
    ]
    assert _scan(api, mails)["waiting_on_you"] == []


def test_a_plus_tag_cannot_dodge_an_opt_out(api):
    mails = _thread(
        "Could you please confirm?", sender="Alice <alice+news@example.com>"
    ) + [
        outbound(
            "o1",
            "t-other",
            to="ALICE@example.com",
            body="stop emailing me",
            age_days=40,
        )
    ]
    assert _scan(api, mails)["waiting_on_you"] == []


def test_an_opt_out_inside_the_thread_suppresses_it(api):
    out = _scan(
        api,
        _thread(
            "Could you confirm?",
            prior=(SUBSTANTIVE, "Please remove me from this list."),
        ),
    )
    assert out["waiting_on_you"] == []


def test_a_promotions_tab_message_is_vetoed():
    mails = _thread("Could you take a look?", labels=["INBOX", "CATEGORY_PROMOTIONS"])
    assert _scan(FakeGmailApi, mails)["waiting_on_you"] == []


def test_a_real_deadline_overrides_the_promotions_tab():
    mails = _thread(
        "Could you confirm? Payment due Friday.",
        labels=["INBOX", "CATEGORY_PROMOTIONS"],
    )
    assert len(_scan(FakeGmailApi, mails)["waiting_on_you"]) == 1


def test_a_promo_subject_is_vetoed_on_outlook_too():
    mails = _thread("Could you take a look?", subject="Special offer just for you")
    assert _scan(FakeGraphApi, mails)["waiting_on_you"] == []


def test_a_bare_question_mark_is_not_an_ask(api):
    assert _scan(api, _thread("Great news?"))["waiting_on_you"] == []


def test_min_age_hours_skips_fresh_mail(api):
    out = _scan(api, _thread("Could you please confirm?"), min_age_hours=24 * 5)
    assert out["waiting_on_you"] == []


def test_a_negative_age_or_budget_is_refused():
    backend = FakeGmailApi([]).backend()
    with pytest.raises(ValueError, match="min_age_hours"):
        detect_waiting_on_you(backend, min_age_hours=-1)
    with pytest.raises(ValueError, match="max_inbox"):
        detect_waiting_on_you(backend, max_inbox=0)


def test_an_unreadable_timestamp_fails_loudly():
    with pytest.raises(MailboxError, match="received time"):
        waiting.received_ms({"id": "m1", "received": "yesterday"})
    with pytest.raises(MailboxError, match="no received time"):
        waiting.received_ms({"id": "m1", "received": ""})


def test_graph_fractional_seconds_parse():
    ms = waiting.received_ms({"id": "m", "received": "2026-09-02T08:15:00.1234567Z"})
    assert ms == waiting.received_ms(
        {"id": "m", "received": "2026-09-02T08:15:00.123456Z"}
    )


def test_a_thread_fetch_failure_fails_the_scan_not_shortens_it(api, monkeypatch):
    backend = api(_thread("Could you please confirm?")).backend()

    def broken(thread_id):
        raise MailboxError("boom")

    monkeypatch.setattr(backend, "get_thread", broken)
    with pytest.raises(MailboxError, match="boom"):
        detect_waiting_on_you(backend, now_ms=NOW_MS)


def test_the_scan_never_writes(api):
    """Read-only: every request the scan makes is a GET."""
    fake = api(_thread("Could you please confirm?"))
    detect_waiting_on_you(fake.backend(), now_ms=NOW_MS)
    assert fake.requests
    assert {r.method for r in fake.requests} == {"GET"}


# --------------------------------------------------------------------------
# parity with the email agent, while it still ships
# --------------------------------------------------------------------------


def test_predicates_match_the_email_agent_row_for_row():
    pytest.importorskip("gaia_agent_email")
    from gaia_agent_email.tools.calendar_tools import detect_meeting_request_heuristic
    from gaia_agent_email.tools.text_signals import (
        has_direct_ask_signal,
        has_meeting_time_signal,
    )

    for rec in _corpus():
        s, b = (rec.get("subject") or ""), (rec.get("body") or "")
        assert waiting.has_direct_ask_signal(
            s.lower(), b.lower()
        ) == has_direct_ask_signal(s.lower(), b.lower()), rec["id"]
        assert waiting.has_meeting_time_signal(
            s.lower(), b.lower()
        ) == has_meeting_time_signal(s.lower(), b.lower()), rec["id"]
        theirs = detect_meeting_request_heuristic(s, b)
        assert waiting.is_meeting_request(s, b) == bool(
            theirs.is_meeting_request and theirs.confidence == "high"
        ), rec["id"]
