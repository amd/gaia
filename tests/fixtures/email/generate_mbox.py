#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Generate deterministic synthetic .mbox fixtures for email triage tests.

This script creates:
- tests/fixtures/email/synthetic_inbox.mbox
- tests/fixtures/email/ground_truth.json

The dataset is fully synthetic (RFC 2606 domains) and deterministic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mailbox
import random
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any

SEED = 23023
TOTAL_MESSAGES = 220

OUT_DIR = Path(__file__).resolve().parent
OUT_MBOX = OUT_DIR / "synthetic_inbox.mbox"
OUT_GT = OUT_DIR / "ground_truth.json"

CATEGORIES = ["urgent", "actionable", "informational", "low_priority"]

TARGET_COUNTS = {
    "urgent": 24,
    "actionable": 51,
    "informational": 66,
    "low_priority": 37,
    "spam": 20,
    "ambiguous": 15,
    "malformed": 7,
}

if sum(TARGET_COUNTS.values()) != TOTAL_MESSAGES:
    raise ValueError("Target counts do not sum to TOTAL_MESSAGES")


@dataclass(frozen=True)
class Persona:
    key: str
    display_name: str
    address: str
    role: str


PERSONAS = {
    "sarah_chen": Persona(
        "sarah_chen",
        "Sarah Chen",
        "sarah.chen@acme-corp.example.com",
        "VP Engineering",
    ),
    "alex_kumar": Persona(
        "alex_kumar",
        "Alex Kumar",
        "alex.kumar@acme-corp.example.com",
        "Senior Engineer",
    ),
    "jordan_lee": Persona(
        "jordan_lee",
        "Jordan Lee",
        "jordan.lee@acme-corp.example.com",
        "Product Manager",
    ),
    "it_systems": Persona(
        "it_systems",
        "IT Systems",
        "noreply@acme-corp.example.com",
        "Automated",
    ),
    "hr_team": Persona(
        "hr_team",
        "HR Team",
        "hr@acme-corp.example.com",
        "Automated",
    ),
    "maria_santos": Persona(
        "maria_santos",
        "Maria Santos",
        "maria.santos@globaltech.example.net",
        "External partner",
    ),
    "devops_bot": Persona(
        "devops_bot",
        "DevOps Bot",
        "alerts@acme-corp.example.com",
        "CI/CD alerts",
    ),
    "newsletter_tech": Persona(
        "newsletter_tech",
        "Tech Insider Weekly",
        "digest@tech-insider.example.com",
        "Newsletter",
    ),
    "newsletter_market": Persona(
        "newsletter_market",
        "Market Pulse",
        "news@marketpulse.example.com",
        "Newsletter",
    ),
    "marketing_vendor": Persona(
        "marketing_vendor",
        "GrowthStack Solutions",
        "hello@growthstack.example.com",
        "Cold outreach",
    ),
}

PRIMARY_TO = "Taylor Morgan <taylor.morgan@acme-corp.example.com>"
PRIMARY_CC = "Eng Leadership <eng-leadership@acme-corp.example.com>"

CORP_DISCLAIMER = (
    "This email and any attachments are confidential and intended only for the "
    "named recipient. If you received this in error, please notify the sender "
    "and delete this message."
)

RECEIVED_TEMPLATE = (
    "from smtp-{hop}.acme-corp.example.com (smtp-{hop}.acme-corp.example.com "
    "[192.0.2.{octet}]) by mx-{next_hop}.acme-corp.example.com with ESMTPS id {rid}; "
    "{date}"
)


class IdFactory:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.counter = 0

    def make(self, domain: str = "mail.acme-corp.example.com") -> str:
        self.counter += 1
        left = f"msg{self.counter:04d}.{self.rng.randrange(100000, 999999)}"
        return f"<{left}@{domain}>"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _weekday_weighted_datetimes(rng: random.Random, count: int) -> list[datetime]:
    base = datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)  # Monday
    values: list[datetime] = []
    for i in range(count):
        # Spread across two weeks with weekday clustering around 9 AM and 4-5 PM.
        day_offset = rng.randint(0, 13)
        d = base + timedelta(days=day_offset)
        if d.weekday() < 5:
            slot = rng.choices([9, 16, 17, 11], weights=[45, 28, 20, 7], k=1)[0]
            minute = rng.randint(0, 59)
        else:
            # Weekend batch (overnight triage scenario)
            slot = rng.choices([1, 2, 3, 4], weights=[20, 35, 30, 15], k=1)[0]
            minute = rng.randint(0, 59)
        values.append(d.replace(hour=slot, minute=minute))
    # Deterministic but not strictly sorted; inbox can arrive somewhat shuffled.
    rng.shuffle(values)
    # Nudge a batch to Monday pre-work hours from weekend.
    for i in range(min(12, len(values))):
        if i % 3 == 0:
            values[i] = values[i].replace(hour=5, minute=rng.randint(0, 59))
    return values


def _received_headers(dt: datetime, rng: random.Random) -> list[str]:
    items = []
    for hop in range(1, 3):
        items.append(
            RECEIVED_TEMPLATE.format(
                hop=hop,
                next_hop=hop + 1,
                octet=20 + hop,
                rid=f"R{rng.randrange(10000, 99999)}",
                date=format_datetime(dt - timedelta(minutes=hop * 2)),
            )
        )
    return items


def _make_attachment(
    name: str,
    size: int,
    mime: tuple[str, str],
    rng: random.Random,
) -> tuple[str, bytes, str, str]:
    body = bytes(rng.randrange(0, 255) for _ in range(size))
    maintype, subtype = mime
    return name, body, maintype, subtype


def _base_message(
    *,
    persona: Persona,
    subject: str,
    body_text: str,
    date_value: datetime,
    message_id: str,
    category: str,
    sender_persona: str,
    rng: random.Random,
    to: str = PRIMARY_TO,
    cc: str | None = None,
    html_body: str | None = None,
    x_priority: str | None = None,
    importance: str | None = None,
    x_mailer: str | None = None,
    reply_to: str | None = None,
    list_unsubscribe: str | None = None,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    has_inline_image: bool = False,
    attachments: list[tuple[str, bytes, str, str, str]] | None = None,
    forwarded_raw: str | None = None,
) -> tuple[EmailMessage, dict[str, Any]]:
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = f"{persona.display_name} <{persona.address}>"
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = format_datetime(date_value)
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = " ".join(references)
    if x_priority:
        msg["X-Priority"] = x_priority
    if importance:
        msg["Importance"] = importance
    if x_mailer:
        msg["X-Mailer"] = x_mailer
    if reply_to:
        msg["Reply-To"] = reply_to
    if list_unsubscribe:
        msg["List-Unsubscribe"] = list_unsubscribe

    for rec in _received_headers(date_value, rng):
        msg["Received"] = rec

    text = f"{body_text}\n\n{CORP_DISCLAIMER}\n"
    if html_body:
        html = (
            "<html><body>"
            f"<div>{html_body}</div>"
            "<hr><small>"
            f"{CORP_DISCLAIMER}"
            "</small></body></html>"
        )
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(text)

    if has_inline_image:
        if msg.get_content_maintype() != "multipart":
            msg.make_mixed()
        inline_bytes = bytes((i % 255 for i in range(1024)))
        msg.add_attachment(
            inline_bytes,
            maintype="image",
            subtype="png",
            filename="logo-inline.png",
            disposition="inline",
            cid="<logo-inline-1>",
        )

    if attachments:
        for filename, payload, mt, st, disposition in attachments:
            msg.add_attachment(
                payload,
                maintype=mt,
                subtype=st,
                filename=filename,
                disposition=disposition,
            )

    if forwarded_raw:
        if msg.get_content_maintype() != "multipart":
            msg.make_mixed()
        forwarded = EmailMessage(policy=policy.SMTP)
        forwarded.set_content(forwarded_raw)
        forwarded["Subject"] = "Fwd payload"
        forwarded["From"] = "legacy.sender@example.org"
        forwarded["To"] = PRIMARY_TO
        forwarded["Date"] = format_datetime(date_value - timedelta(days=1))
        msg.attach(forwarded)

    meta = {
        "category": category,
        "priority": "high" if category == "urgent" else "normal",
        "is_thread_root": in_reply_to is None,
        "thread_id": (
            message_id
            if in_reply_to is None
            else (references[0] if references else in_reply_to)
        ),
        "has_attachment": bool(attachments or has_inline_image or forwarded_raw),
        "is_spam": False,
        "is_phishing": False,
        "ambiguous": False,
        "rationale": "",
        "sender_persona": sender_persona,
    }
    return msg, meta


def _make_thread(
    *,
    root_subject: str,
    persona_keys: list[str],
    depth: int,
    date_values: list[datetime],
    id_factory: IdFactory,
    rng: random.Random,
    category: str,
    ambiguous: bool = False,
    rationale: str = "",
) -> list[tuple[EmailMessage, dict[str, Any], str]]:
    messages: list[tuple[EmailMessage, dict[str, Any], str]] = []
    refs: list[str] = []
    root_id = id_factory.make()
    for i in range(depth):
        persona_key = persona_keys[i % len(persona_keys)]
        persona = PERSONAS[persona_key]
        message_id = root_id if i == 0 else id_factory.make()
        refs_local = refs[:] if refs else None
        in_reply = refs[-1] if refs else None
        subject = root_subject if i == 0 else f"Re: {root_subject}"
        text = (
            "Update "
            f"{i + 1}/{depth}: please review the latest status and confirm owner.\n"
            "Thanks,\n"
            f"{persona.display_name}\n\n"
            "> Previous thread context included below."
        )
        msg, meta = _base_message(
            persona=persona,
            subject=subject,
            body_text=text,
            date_value=date_values[i],
            message_id=message_id,
            category=category,
            sender_persona=persona_key,
            rng=rng,
            cc=PRIMARY_CC,
            html_body=(
                "<p>Top-posted update for thread participants.</p>"
                "<blockquote>&gt; Prior message quoted content</blockquote>"
            ),
            in_reply_to=in_reply,
            references=refs_local,
            x_mailer=rng.choice(["Microsoft Outlook 16.0", "Thunderbird", "Gmail"]),
        )
        if ambiguous:
            meta["ambiguous"] = True
            meta["rationale"] = rationale
        if i == 0:
            meta["is_thread_root"] = True
            meta["thread_id"] = message_id
        else:
            meta["is_thread_root"] = False
            meta["thread_id"] = root_id
        messages.append((msg, meta, message_id))
        refs.append(message_id)
    return messages


def _mailbox_from_records(
    records: list[tuple[EmailMessage, dict[str, Any], str]],
    malformed_raw: list[tuple[str, dict[str, Any], str]],
    out_mbox: Path,
    out_gt: Path,
) -> None:
    out_mbox.parent.mkdir(parents=True, exist_ok=True)
    if out_mbox.exists():
        out_mbox.unlink()

    box = mailbox.mbox(str(out_mbox), create=True)
    gt: dict[str, Any] = {}

    for msg, meta, message_id in records:
        box.add(msg)
        gt[message_id] = meta

    box.flush()
    box.close()

    # Append malformed messages directly as raw mbox entries. Use a
    # deterministic timestamp on the From_ line so generated mbox files are
    # reproducible across runs.
    deterministic_from_time = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
    with out_mbox.open("ab") as f:
        for raw_msg, meta, message_id in malformed_raw:
            from_line = (
                "From malformed@example.com "
                f"{deterministic_from_time.ctime()}\n"
            )
            payload = raw_msg.replace("\n", "\n").encode("utf-8", errors="replace")
            if not payload.endswith(b"\n"):
                payload += b"\n"
            f.write(from_line.encode("utf-8"))
            f.write(payload)
            f.write(b"\n")
            gt[message_id] = meta

    out_gt.write_text(json.dumps(gt, indent=2, sort_keys=True), encoding="utf-8")


def _build_dataset(
    seed: int = SEED,
) -> tuple[
    list[tuple[EmailMessage, dict[str, Any], str]],
    list[tuple[str, dict[str, Any], str]],
]:
    rng = random.Random(seed)
    id_factory = IdFactory(rng)
    date_pool = _weekday_weighted_datetimes(rng, TOTAL_MESSAGES)
    date_idx = 0

    def next_date() -> datetime:
        nonlocal date_idx
        d = date_pool[date_idx]
        date_idx += 1
        return d

    records: list[tuple[EmailMessage, dict[str, Any], str]] = []
    malformed: list[tuple[str, dict[str, Any], str]] = []

    # Ensure recurring personas appear between 3-8 times.
    persona_usage = {k: 0 for k in PERSONAS}

    def pick_persona(keys: list[str]) -> str:
        key = rng.choice(keys)
        persona_usage[key] += 1
        return key

    # Build threaded corp conversations (3-5 deep).
    thread_specs = [
        (
            "Prod incident follow-up",
            ["sarah_chen", "alex_kumar", "devops_bot"],
            5,
            "urgent",
        ),
        (
            "Q2 contract redlines",
            ["maria_santos", "sarah_chen", "jordan_lee"],
            4,
            "urgent",
        ),
        (
            "Roadmap dependency sync",
            ["jordan_lee", "alex_kumar", "sarah_chen"],
            4,
            "actionable",
        ),
        (
            "Security advisory triage",
            ["it_systems", "alex_kumar", "devops_bot"],
            3,
            "urgent",
        ),
        (
            "Audit evidence request",
            ["hr_team", "jordan_lee", "alex_kumar"],
            3,
            "actionable",
        ),
    ]
    for subject, pkeys, depth, cat in thread_specs:
        for msg, meta, mid in _make_thread(
            root_subject=subject,
            persona_keys=pkeys,
            depth=depth,
            date_values=[next_date() for _ in range(depth)],
            id_factory=id_factory,
            rng=rng,
            category=cat,
        ):
            persona_usage[meta["sender_persona"]] += 1
            records.append((msg, meta, mid))

    # Utility to add single message.
    def add_single(
        *,
        persona_key: str,
        subject: str,
        body: str,
        category: str,
        spam: bool = False,
        phishing: bool = False,
        ambiguous: bool = False,
        rationale: str = "",
        html: bool = False,
        x_priority: str | None = None,
        importance: str | None = None,
        list_unsub: bool = False,
        attachments: list[tuple[str, bytes, str, str, str]] | None = None,
        inline_image: bool = False,
        reply_to: str | None = None,
        x_mailer: str | None = None,
        forwarded: bool = False,
        to: str = PRIMARY_TO,
        cc: str | None = None,
    ) -> None:
        persona_usage[persona_key] += 1
        message_id = id_factory.make()
        msg, meta = _base_message(
            persona=PERSONAS[persona_key],
            subject=subject,
            body_text=body,
            date_value=next_date(),
            message_id=message_id,
            category=category,
            sender_persona=persona_key,
            rng=rng,
            cc=cc,
            to=to,
            html_body=(
                f'<p>{body}</p><p><img src="cid:logo-inline-1" alt="logo"></p>'
                if html
                else None
            ),
            x_priority=x_priority,
            importance=importance,
            x_mailer=x_mailer,
            reply_to=reply_to,
            list_unsubscribe=(
                "<mailto:unsubscribe@example.com>, <https://example.com/unsub>"
                if list_unsub
                else None
            ),
            has_inline_image=inline_image,
            attachments=attachments,
            forwarded_raw=(
                "-----Forwarded Message-----\nLegacy Outlook payload"
                if forwarded
                else None
            ),
        )
        meta["is_spam"] = spam
        meta["is_phishing"] = phishing
        meta["ambiguous"] = ambiguous
        meta["rationale"] = rationale
        if spam:
            meta["priority"] = "low"
        records.append((msg, meta, message_id))

    # Create content pools by category.
    corp_templates = {
        "urgent": [
            "[SEV1] API latency above SLA - owner needed",
            "Client deadline: contract signature required by EOD",
            "Security advisory: rotate credentials within 4 hours",
            "Prod incident report requires exec review",
            "Compliance acknowledgment due by EOD",
        ],
        "actionable": [
            "Please review PR #4821 by tomorrow",
            "Can you approve expense report TR-2288?",
            "Need your decision on vendor shortlist",
            "Meeting invite: launch readiness review",
            "JIRA ticket assigned: GAIA-2024",
        ],
        "informational": [
            "VPN maintenance window this Saturday",
            "Benefits enrollment reminder",
            "All-hands recap and recording",
            "Confluence page updated: onboarding checklist",
            "Shipping confirmation for office equipment",
            "Quarterly financial digest",
        ],
        "low_priority": [
            "Try our premium analytics package",
            "Top 10 growth hacks for your startup",
            "You were mentioned in a social thread",
            "Special discount expires tonight",
            "Weekly promo digest",
        ],
    }

    attachment_specs = [
        ("report.pdf", ("application", "pdf")),
        ("costs.csv", ("text", "csv")),
        ("diagram.png", ("image", "png")),
        (
            "brief.docx",
            (
                "application",
                "vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        ),
        ("invite.ics", ("text", "calendar")),
    ]

    # Generate regular corp/personal mail such that total stays exactly 220.
    # Breakdown:
    # - threaded corp messages: len(records) (already added)
    # - regular messages: 129
    # - personal/consumer messages: 30
    # - spam/phishing: 20
    # - ambiguous: 15
    # - malformed: 7
    regular_count = 129

    corp_personas = [
        "sarah_chen",
        "alex_kumar",
        "jordan_lee",
        "it_systems",
        "hr_team",
        "maria_santos",
        "devops_bot",
        "newsletter_tech",
        "newsletter_market",
        "marketing_vendor",
    ]

    regular_category_weights = [
        ("urgent", 20),
        ("actionable", 34),
        ("informational", 53),
        ("low_priority", 22),
    ]

    for i in range(regular_count):
        cat = rng.choices(
            [c for c, _ in regular_category_weights],
            weights=[w for _, w in regular_category_weights],
            k=1,
        )[0]
        persona_key = pick_persona(corp_personas)
        template = rng.choice(corp_templates[cat])
        topic = rng.choice(
            [
                "IT maintenance",
                "expense approval",
                "meeting update",
                "policy notice",
                "build status",
                "client follow-up",
                "receipt",
                "newsletter",
            ]
        )
        subject = f"{template} - {topic}"
        body = (
            f"Hello Taylor,\n\n{template}."
            " Please review details and reply if needed."
        )
        attachment_template = rng.choice(attachment_specs)
        add_single(
            persona_key=persona_key,
            subject=subject,
            body=body,
            category=cat,
            html=rng.random() < 0.4,
            x_priority=(
                "1 (Highest)" if cat == "urgent" and rng.random() < 0.6 else None
            ),
            importance=("High" if cat == "urgent" and rng.random() < 0.6 else None),
            list_unsub=(
                cat in {"informational", "low_priority"} and rng.random() < 0.5
            ),
            attachments=(
                [
                    (
                        name,
                        payload,
                        mt,
                        st,
                        "attachment",
                    )
                    for name, payload, mt, st in [
                        _make_attachment(
                            name=attachment_template[0],
                            size=rng.randint(1024, 4096),
                            mime=attachment_template[1],
                            rng=rng,
                        )
                    ]
                ]
                if rng.random() < 0.25
                else None
            ),
            inline_image=rng.random() < 0.08,
            forwarded=rng.random() < 0.06,
            x_mailer=rng.choice(
                [
                    "Microsoft Outlook 16.0",
                    "Gmail",
                    "Thunderbird",
                    "Jira Mailer",
                    "PagerDuty",
                ]
            ),
            cc=(PRIMARY_CC if rng.random() < 0.35 else None),
        )

    # Personal / consumer messages (~30) blended into informational/
    # low-priority/actionable buckets.
    personal_subjects = [
        ("Your Amazon order has shipped", "informational"),
        ("Flight confirmation ACM-8722", "actionable"),
        ("Bank alert: transaction posted", "informational"),
        ("LinkedIn: 3 new profile views", "low_priority"),
        ("Hotel booking confirmation", "informational"),
        ("GitHub: 12 new stars this week", "informational"),
        ("Calendar reminder: dentist appointment", "actionable"),
        ("Retail receipt for your purchase", "informational"),
    ]
    for i in range(30):
        subj, cat = personal_subjects[i % len(personal_subjects)]
        add_single(
            persona_key=rng.choice(
                ["newsletter_tech", "newsletter_market", "marketing_vendor"]
            ),
            subject=f"{subj} [{i + 1}]",
            body="Automated consumer notification for synthetic triage dataset.",
            category=cat,
            list_unsub=(cat in {"informational", "low_priority"}),
            html=rng.random() < 0.55,
            x_mailer=rng.choice(["Gmail", "Outlook.com", "MailChimp"]),
        )

    # Spam / phishing (pre-triage filter).
    spam_templates = [
        (
            "URGENT: verify account password immediately",
            True,
            True,
            "IT Support <it-helpdesk@acme-security-login.example.org>",
            "verify your account to avoid deactivation",
            "security-team@acme-corp.example.com",
            "invoice-update.pdf.exe",
        ),
        (
            "Wire transfer needed now - from CEO",
            True,
            True,
            "Sarah Chen <sarah.chen@acme-payroll.example.co>",
            "send funds before 30 minutes",
            "payments@acme-corp.example.com",
            "wire_instructions.doc",
        ),
        (
            "Package held at customs - click to release",
            True,
            False,
            "FedEx Notice <tracking@fedex-alerts.example.biz>",
            "tracking update requires payment",
            None,
            "tracking_label.zip",
        ),
        (
            "You won the crypto lottery",
            True,
            False,
            "Claims Desk <claims@luckywallet.example.io>",
            "confirm wallet and private key",
            None,
            "claim_form.pdf",
        ),
    ]
    for i in range(TARGET_COUNTS["spam"]):
        (
            subj,
            spam_flag,
            phishing_flag,
            from_display,
            body_hint,
            reply_to,
            bad_attachment,
        ) = spam_templates[i % len(spam_templates)]
        message_id = id_factory.make("mail.suspicious.example.org")
        dt = next_date()
        msg = EmailMessage(policy=policy.SMTP)
        msg["From"] = from_display
        msg["To"] = PRIMARY_TO
        msg["Subject"] = f"{subj} #{i + 1}"
        msg["Date"] = format_datetime(dt)
        msg["Message-ID"] = message_id
        msg["X-Mailer"] = rng.choice(["PHPMailer", "Roundcube", "Unknown MTA"])
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(
            f"Dear user, {body_hint}. immediate action needed!!! please do not delay."
        )
        att_name, payload, mt, st = _make_attachment(
            bad_attachment,
            size=rng.randint(1024, 3072),
            mime=("application", "octet-stream"),
            rng=rng,
        )
        msg.add_attachment(
            payload,
            maintype=mt,
            subtype=st,
            filename=att_name,
            disposition="attachment",
        )
        meta = {
            "category": rng.choice(["informational", "low_priority"]),
            "priority": "low",
            "is_thread_root": True,
            "thread_id": message_id,
            "has_attachment": True,
            "is_spam": spam_flag,
            "is_phishing": phishing_flag,
            "ambiguous": False,
            "rationale": "",
            "sender_persona": "spam_unknown",
        }
        records.append((msg, meta, message_id))

    # Ambiguous / borderline.
    ambiguous_pool = [
        (
            "Meeting invite from unknown external contact",
            "actionable",
            "Invite could be relevant partnership kickoff but sender is unknown.",
        ),
        (
            "Vendor invoice with no prior relationship",
            "informational",
            "Could be fraud; triage as informational pending verification.",
        ),
        (
            "Automated JIRA ticket for unfamiliar project",
            "informational",
            "No ownership signal; likely informational for this user.",
        ),
        (
            "Newsletter from tool you actively use",
            "informational",
            "Product updates can impact active workflows.",
        ),
        (
            "Quick question from unknown sender",
            "low_priority",
            "No context and no direct urgency indicators.",
        ),
        (
            "Compliance notice requiring acknowledgement by EOD",
            "urgent",
            "Hard deadline suggests urgency, though action is lightweight.",
        ),
        (
            "Reply-all where user only CC'd",
            "informational",
            "Likely FYI unless directly asked a question.",
        ),
    ]
    for i in range(TARGET_COUNTS["ambiguous"]):
        subject, cat, rationale = ambiguous_pool[i % len(ambiguous_pool)]
        add_single(
            persona_key=rng.choice(
                ["jordan_lee", "maria_santos", "hr_team", "newsletter_tech"]
            ),
            subject=f"[Ambiguous] {subject} ({i + 1})",
            body="Boundary-case message intentionally designed for triage calibration.",
            category=cat,
            ambiguous=True,
            rationale=rationale,
            html=rng.random() < 0.3,
            x_mailer=rng.choice(["Microsoft Outlook 16.0", "Gmail", "Zendesk Mailer"]),
        )

    # Malformed / parser edge cases (raw entries).
    malformed_specs = [
        (
            "missing-subject",
            "From: IT Systems <noreply@acme-corp.example.com>\n"
            f"To: {PRIMARY_TO}\n"
            f"Date: {format_datetime(next_date())}\n"
            f"Message-ID: {id_factory.make()}\n"
            "\n"
            "Body with missing subject header.\n",
            {"category": "informational", "sender_persona": "it_systems"},
        ),
        (
            "empty-body",
            "From: HR Team <hr@acme-corp.example.com>\n"
            f"To: {PRIMARY_TO}\n"
            "Subject: Empty body test\n"
            f"Date: {format_datetime(next_date())}\n"
            f"Message-ID: {id_factory.make()}\n"
            "\n",
            {"category": "informational", "sender_persona": "hr_team"},
        ),
        (
            "truncated-multipart",
            "From: Jordan Lee <jordan.lee@acme-corp.example.com>\n"
            f"To: {PRIMARY_TO}\n"
            "Subject: Truncated MIME boundary\n"
            f"Date: {format_datetime(next_date())}\n"
            f"Message-ID: {id_factory.make()}\n"
            "MIME-Version: 1.0\n"
            "Content-Type: multipart/alternative; boundary=XYZBOUND\n"
            "\n"
            "--XYZBOUND\n"
            "Content-Type: text/plain; charset=utf-8\n"
            "\n"
            "Part one only, boundary never closes properly.\n",
            {"category": "actionable", "sender_persona": "jordan_lee"},
        ),
        (
            "invalid-date",
            "From: DevOps Bot <alerts@acme-corp.example.com>\n"
            f"To: {PRIMARY_TO}\n"
            "Subject: Invalid Date header example\n"
            "Date: not-a-real-date\n"
            f"Message-ID: {id_factory.make()}\n"
            "\n"
            "Invalid date parser edge case.\n",
            {"category": "urgent", "sender_persona": "devops_bot"},
        ),
        (
            "double-encoded-subject",
            "From: Alex Kumar <alex.kumar@acme-corp.example.com>\n"
            f"To: {PRIMARY_TO}\n"
            "Subject: =?UTF-8?B?PT89VVRGLTg/Qj9VbWx6WldRZ1VIVnlaU0U9Pz0=?=\n"
            f"Date: {format_datetime(next_date())}\n"
            f"Message-ID: {id_factory.make()}\n"
            "\n"
            "Subject intentionally odd for decoder robustness.\n",
            {"category": "informational", "sender_persona": "alex_kumar"},
        ),
        (
            "bad-base64",
            "From: Maria Santos <maria.santos@globaltech.example.net>\n"
            f"To: {PRIMARY_TO}\n"
            "Subject: Base64 body padding issue\n"
            f"Date: {format_datetime(next_date())}\n"
            f"Message-ID: {id_factory.make()}\n"
            "Content-Transfer-Encoding: base64\n"
            "\n"
            "SGVsbG8gd29ybGQh====\n",
            {"category": "actionable", "sender_persona": "maria_santos"},
        ),
        (
            "no-from-long-subject",
            f"To: {PRIMARY_TO}\n"
            "Subject: " + ("LongSubject-" * 45) + "\n"
            f"Date: {format_datetime(next_date())}\n"
            f"Message-ID: {id_factory.make()}\n"
            "\n"
            "No From header and very long subject.\n",
            {"category": "low_priority", "sender_persona": "unknown"},
        ),
    ]

    for _, raw, extras in malformed_specs:
        mid_match = re.search(r"^Message-ID:\s*(<[^>]+>)", raw, re.MULTILINE)
        if not mid_match:
            raise ValueError("Malformed raw message missing Message-ID")
        message_id = mid_match.group(1)
        meta = {
            "category": extras["category"],
            "priority": "normal",
            "is_thread_root": True,
            "thread_id": message_id,
            "has_attachment": False,
            "is_spam": False,
            "is_phishing": False,
            "ambiguous": False,
            "rationale": "Malformed parser edge-case message",
            "sender_persona": extras["sender_persona"],
        }
        malformed.append((raw, meta, message_id))

    # Enforce total count and postconditions.
    if len(records) + len(malformed) != TOTAL_MESSAGES:
        raise ValueError(
            "Generated "
            f"{len(records) + len(malformed)} messages, expected {TOTAL_MESSAGES}"
        )

    # Ensure UTF-8 and ISO-8859-1 non-ASCII coverage by replacing one record in-place.
    message_id = id_factory.make()
    non_ascii_msg, meta = _base_message(
        persona=PERSONAS["jordan_lee"],
        subject="Résumé update – équipe européenne",
        body_text="Voici la mise à jour du résumé du projet pour l'équipe européenne.",
        date_value=date_pool[-1],
        message_id=message_id,
        category="informational",
        sender_persona="jordan_lee",
        rng=rng,
        html_body="<p>Olá, atualização com caracteres acentuados.</p>",
    )
    # Avoid calling set_charset on multipart messages (Message.get_payload()
    # may be a list for multipart), instead set charset on text parts.
    if non_ascii_msg.is_multipart():
        for part in non_ascii_msg.walk():
            if part.get_content_maintype() == "text":
                try:
                    part.set_charset("iso-8859-1")
                except Exception:
                    # Be defensive: on some stdlib versions this may raise.
                    pass
    else:
        non_ascii_msg.set_charset("iso-8859-1")
    # Insert the non-ASCII message by replacing the first informational
    # record, keeping the total message counts stable and preserving
    # category distribution.
    replaced = False
    for i, (_msg, _meta, _mid) in enumerate(records):
        if _meta.get("category") == "informational":
            records[i] = (non_ascii_msg, meta, message_id)
            replaced = True
            break
    if not replaced:
        # Fallback: append if no informational slot found (shouldn't happen)
        records.append((non_ascii_msg, meta, message_id))

    # Validate persona repeat ranges for core personas.
    core_personas = [
        "sarah_chen",
        "alex_kumar",
        "jordan_lee",
        "it_systems",
        "hr_team",
        "maria_santos",
        "devops_bot",
        "newsletter_tech",
        "newsletter_market",
    ]
    for key in core_personas:
        if persona_usage.get(key, 0) < 3:
            raise ValueError(f"Persona {key} appears fewer than 3 times")

    return records, malformed


def generate(
    out_mbox: Path = OUT_MBOX,
    out_gt: Path = OUT_GT,
    seed: int = SEED,
) -> tuple[str, str]:
    records, malformed = _build_dataset(seed)
    _mailbox_from_records(records, malformed, out_mbox, out_gt)

    mbox_size = out_mbox.stat().st_size
    if mbox_size >= 1024 * 1024:
        raise ValueError(f"Generated mbox exceeds 1 MB ({mbox_size} bytes)")

    mbox_hash = _sha256(out_mbox)
    gt_hash = _sha256(out_gt)
    return mbox_hash, gt_hash


def verify() -> int:
    if not OUT_MBOX.exists() or not OUT_GT.exists():
        print("Missing pre-built fixtures; run without --verify first.")
        return 1

    existing_mbox_hash = _sha256(OUT_MBOX)
    existing_gt_hash = _sha256(OUT_GT)

    with tempfile.TemporaryDirectory() as td:
        temp_mbox = Path(td) / "synthetic_inbox.mbox"
        temp_gt = Path(td) / "ground_truth.json"
        gen_mbox_hash, gen_gt_hash = generate(temp_mbox, temp_gt, seed=SEED)

    ok = existing_mbox_hash == gen_mbox_hash and existing_gt_hash == gen_gt_hash
    print(f"existing mbox sha256: {existing_mbox_hash}")
    print(f"generated mbox sha256: {gen_mbox_hash}")
    print(f"existing gt   sha256: {existing_gt_hash}")
    print(f"generated gt   sha256: {gen_gt_hash}")

    if ok:
        print("VERIFY OK: checked-in fixtures match deterministic generator output")
        return 0

    print("VERIFY FAILED: checked-in fixtures differ from generated output")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify", action="store_true", help="Verify checked-in fixtures"
    )
    parser.add_argument("--seed", type=int, default=SEED, help="Random seed")
    args = parser.parse_args()

    if args.verify:
        return verify()

    mbox_hash, gt_hash = generate(seed=args.seed)
    print(f"Wrote: {OUT_MBOX} ({OUT_MBOX.stat().st_size} bytes)")
    print(f"Wrote: {OUT_GT} ({OUT_GT.stat().st_size} bytes)")
    print(f"mbox sha256: {mbox_hash}")
    print(f"gt   sha256: {gt_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
