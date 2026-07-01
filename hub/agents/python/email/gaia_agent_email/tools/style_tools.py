# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Voice/style-profile tools mixin for ``EmailTriageAgent`` (#1607).

Builds a local voice/style profile from a sample of the user's **Sent**
messages so ``draft_reply`` bodies read like the user wrote them —
greeting, sign-off, typical length, and formality — instead of a generic
scaffold. Extends #1269 (per-request tone matching) with a *persisted*
profile.

Privacy invariant (AC3): the profile is DERIVED FEATURES ONLY — no raw
Sent bodies are ever stored. Extraction is deterministic string analysis
on-device (no LLM call), the profile lives in the agent's local
MemoryStore (``~/.gaia/email/memory.db``), and the prompt fragment it
feeds is only ever sent to the local Lemonade backend (the agent's
``base_url`` allowlist forbids cloud LLMs). No Sent content leaves the
device.

Profiles are stored per mailbox provider (a work Outlook voice and a
personal Gmail voice can differ) under one rolling record each — entity
``email:style_profile:<provider>`` — using the same upsert pattern as
``preference_tools.py``. When memory is disabled the tools still work
in-process; the profile just does not survive a restart.

The profile reaches the LLM through ``get_voice_style_system_prompt()``,
auto-discovered by the base agent's ``_get_mixin_prompts()``. The draft
itself is still only ever created via ``draft_reply`` (a Gmail/Outlook
*draft*, never a send) and sending stays confirmation-gated (#1264).

Tools registered:

- ``build_style_profile(sample_size, mailbox)`` — sample Sent mail, build
  and persist the profile, refresh the system prompt.
- ``get_style_profile(mailbox)`` — inspect the stored profile(s).
- ``clear_style_profile(mailbox)`` — remove profile(s), in-process and
  persisted.
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from gaia_agent_email.gmail_backend import decode_message_body

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)

# Stable entity prefix for per-mailbox style-profile records.
# Entity = f"{_STYLE_ENTITY_PREFIX}{provider}" (e.g. "email:style_profile:google")
_STYLE_ENTITY_PREFIX = "email:style_profile:"
_STYLE_DOMAIN = "email_agent_style"
_STYLE_CATEGORY = "style_profile"

# How many Sent messages to sample by default, and the floor below which a
# profile would be noise rather than signal. Both module-level so tests and
# callers can import them.
DEFAULT_SENT_SAMPLE_SIZE = 25
MIN_STYLE_SAMPLE = 3
MAX_SENT_SAMPLE_SIZE = 100

# Sanity ceiling mirroring profile_tools: never silently truncate coverage.
_MAX_STYLE_RECORDS = 100

# ---------------------------------------------------------------------------
# Deterministic feature extraction (pure functions — no LLM, no I/O)
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(
    r"^(good (?:morning|afternoon|evening)|hi there|hey there|hi|hello|hey|"
    r"dear|greetings)\b(?P<rest>[^\n]*)$",
    re.IGNORECASE,
)

_SIGNOFF_RE = re.compile(
    r"^(best regards|kind regards|warm regards|warm wishes|many thanks|"
    r"thanks so much|thank you|thanks|thx|best|cheers|regards|sincerely|"
    r"talk soon|take care|br)\s*[,.!]?$",
    re.IGNORECASE,
)

# Reply/forward separators — everything from the first match down is quoted
# history, not the user's own words.
_QUOTE_BLOCK_RES = (
    re.compile(r"^On .{4,200} wrote:\s*$"),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Forwarded message\s*-{2,}$", re.IGNORECASE),
)

_CASUAL_MARKERS = (
    "hey",
    "yeah",
    "yep",
    "gonna",
    "wanna",
    "btw",
    "fyi",
    "thx",
    "lol",
    "cheers",
    "no worries",
    "sounds good",
)
_FORMAL_MARKERS = (
    "dear",
    "sincerely",
    "regards",
    "please find",
    "kindly",
    "hereby",
    "pursuant",
    "attached please",
    "to whom it may concern",
)

_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f02f]"
)


def strip_quoted_text(body: str) -> str:
    """Return only the user-authored part of a Sent body.

    Cuts everything below the first reply/forward separator and drops
    ``>``-quoted lines, so quoted history never pollutes the style sample.
    """
    kept: List[str] = []
    for line in (body or "").splitlines():
        stripped = line.strip()
        if any(rx.match(stripped) for rx in _QUOTE_BLOCK_RES):
            break
        if stripped.startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def extract_greeting(body: str) -> Optional[str]:
    """Normalize the first line into a greeting template, or None.

    ``"Hi Bob,"`` → ``"Hi {name},"`` and ``"Hey,"`` → ``"Hey,"`` — the
    recipient's name is replaced with a ``{name}`` placeholder so the
    template generalizes (and so no correspondent name is stored).
    """
    for line in (body or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _GREETING_RE.match(line)
        if not m:
            return None
        salutation = m.group(1)
        rest = m.group("rest")
        trailing = line[-1] if line[-1] in ",!:—-" else ""
        has_name = bool(rest.strip().rstrip(",!:—-").strip())
        if has_name:
            return f"{salutation} {{name}}{trailing}"
        return f"{salutation}{trailing}"
    return None


def extract_signoff(body: str) -> Optional[str]:
    """Return the closing template from the last lines of a body, or None.

    A sign-off is a known closing phrase (``Thanks,`` / ``Best regards.``
    / …) optionally followed by a short name line. The name line is kept —
    it is the user's OWN sign-off name, part of their voice, not
    correspondent content.
    """
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    # Scan the last few lines; the sign-off phrase is never buried deep.
    for idx in range(len(lines) - 1, max(len(lines) - 4, -1), -1):
        if _SIGNOFF_RE.match(lines[idx]):
            name_lines = lines[idx + 1 :]
            # A trailing short line (≤4 words) right after the phrase is the
            # signature name; anything longer is body text, not a signature.
            if len(name_lines) == 1 and len(name_lines[0].split()) <= 4:
                return f"{lines[idx]}\n{name_lines[0]}"
            if not name_lines:
                return lines[idx]
    return None


def classify_formality(body: str) -> str:
    """Heuristic formality label for one body: formal / neutral / casual."""
    low = (body or "").lower()
    casual = sum(low.count(marker) for marker in _CASUAL_MARKERS)
    casual += low.count("!")
    casual += len(_EMOJI_RE.findall(low))
    formal = sum(low.count(marker) for marker in _FORMAL_MARKERS)
    if casual > formal:
        return "casual"
    if formal > casual:
        return "formal"
    return "neutral"


def _top_variant(counts: Dict[str, int]) -> Optional[str]:
    """Most frequent variant; ties broken lexicographically for determinism."""
    if not counts:
        return None
    return max(counts, key=lambda k: (counts[k], k))


def build_profile_from_bodies(bodies: List[str], *, mailbox: str) -> Dict[str, Any]:
    """Build the style profile dict from user-authored Sent bodies.

    Deterministic — same bodies always yield the same profile. The result
    contains ONLY aggregate features (templates, counts, labels), never the
    bodies themselves; ``test_email_style_profile.py`` enforces that no raw
    Sent content survives into the stored profile.
    """
    greeting_counts: Dict[str, int] = {}
    signoff_counts: Dict[str, int] = {}
    word_counts: List[int] = []
    formality_votes: Dict[str, int] = {"casual": 0, "neutral": 0, "formal": 0}
    emoji_seen = False

    for body in bodies:
        greeting = extract_greeting(body)
        if greeting:
            greeting_counts[greeting] = greeting_counts.get(greeting, 0) + 1
        signoff = extract_signoff(body)
        if signoff:
            signoff_counts[signoff] = signoff_counts.get(signoff, 0) + 1
        word_counts.append(len(body.split()))
        formality_votes[classify_formality(body)] += 1
        emoji_seen = emoji_seen or bool(_EMOJI_RE.search(body))

    formality = _top_variant(formality_votes) or "neutral"
    return {
        "mailbox": mailbox,
        "sample_size": len(bodies),
        "greeting": _top_variant(greeting_counts),
        "signoff": _top_variant(signoff_counts),
        "median_word_count": int(statistics.median(word_counts)) if word_counts else 0,
        "formality": formality,
        "uses_emoji": emoji_seen,
        "built_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def format_style_guidance(profiles: Dict[str, Dict[str, Any]]) -> str:
    """Render stored profiles into the draft-composition prompt fragment.

    Returns "" when no profile exists, so the composed system prompt is
    byte-identical to the pre-#1607 prompt until the user builds one.
    """
    if not profiles:
        return ""
    parts: List[str] = [
        "VOICE & STYLE PROFILE (learned locally from the user's Sent mail; "
        "stored on-device only):",
        "When composing the body for draft_reply or draft_forward, write in "
        "the user's own voice:",
    ]
    multiple = len(profiles) > 1
    for provider in sorted(profiles):
        profile = profiles[provider]
        if multiple:
            parts.append(f"[mailbox: {provider}]")
        if profile.get("greeting"):
            parts.append(
                f'- Open with: "{profile["greeting"]}" '
                "(replace {name} with the recipient's first name)"
            )
        if profile.get("signoff"):
            # Verbatim on its own lines — small local models follow a literal
            # sign-off block far more reliably than an inlined description.
            parts.append(
                "- End every draft with this exact sign-off on its own "
                f"lines:\n{profile['signoff']}"
            )
        if profile.get("median_word_count"):
            parts.append(
                f"- Typical length: about {profile['median_word_count']} words "
                "— keep drafts near this unless the user asks otherwise."
            )
        parts.append(f"- Formality: {profile.get('formality', 'neutral')}.")
        if not profile.get("uses_emoji"):
            parts.append("- The user does not use emoji; avoid them.")
    parts.append(
        "Drafts are ALWAYS returned for the user's approval — never send "
        "without explicit confirmation."
    )
    return "\n".join(parts)


def collect_sent_bodies(
    backend: Any, *, sample_size: int
) -> List[str]:
    """Fetch up to *sample_size* Sent messages and return user-authored bodies.

    Uses the ``SENT`` system label (Gmail native; mapped to the
    ``sentitems`` folder by the Outlook backend). Quoted history is
    stripped; empty results are dropped.
    """
    listing = backend.list_messages(label_ids=["SENT"], max_results=sample_size)
    bodies: List[str] = []
    for ref in listing.get("messages", []):
        msg = backend.get_message(ref["id"])
        body, _attachments = decode_message_body(msg.get("payload") or {})
        authored = strip_quoted_text(body)
        if authored:
            bodies.append(authored)
    return bodies


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class StyleToolsMixin:
    """Mixin that registers voice/style-profile tools (#1607).

    State-free at construction time except for ``self._style_profiles``
    (a ``dict[provider, profile]`` set by the agent's ``__init__``).
    Persistence mirrors ``preference_tools.py``: one MemoryStore record
    per mailbox, upserted in place; skipped when memory is disabled or
    the session is incognito.
    """

    def get_voice_style_system_prompt(self) -> str:
        """Prompt fragment auto-discovered by ``_get_mixin_prompts()``.

        Empty (fragment omitted) until a profile has been built.
        """
        return format_style_guidance(getattr(self, "_style_profiles", {}) or {})

    def _load_persisted_style_profiles(self) -> None:
        """Seed ``_style_profiles`` from persisted records on construction."""
        store = getattr(self, "_memory_store", None)
        if store is None:
            return
        rows = store.get_by_category(
            _STYLE_CATEGORY, domain=_STYLE_DOMAIN, limit=_MAX_STYLE_RECORDS
        )
        profiles = getattr(self, "_style_profiles", None)
        if profiles is None:
            return
        for row in rows:
            try:
                payload = json.loads(row["content"])
                provider = payload["mailbox"]
            except (json.JSONDecodeError, KeyError, TypeError):
                log.warning(
                    "style_tools: skipping malformed style-profile record %s",
                    row.get("id"),
                )
                continue
            profiles[provider] = payload

    def _persist_style_profile(self, provider: str, profile: Dict[str, Any]) -> bool:
        """Upsert the profile record for *provider*. Returns True when persisted.

        Skipped (returns False) when memory is disabled or the session is
        incognito — the profile then lives in-process only, matching the
        preference-tools semantics.
        """
        store = getattr(self, "_memory_store", None)
        if store is None or getattr(self, "_incognito", False):
            return False
        entity = f"{_STYLE_ENTITY_PREFIX}{provider}"
        content = json.dumps(profile)
        existing = store.get_by_entity(entity)
        if existing:
            store.update(existing[0]["id"], content=content)
        else:
            store.store(
                category=_STYLE_CATEGORY,
                content=content,
                domain=_STYLE_DOMAIN,
                entity=entity,
                context=getattr(self, "_memory_context", "email"),
                confidence=1.0,
                source="style_tools",
            )
        return True

    def _delete_persisted_style_profile(self, provider: str) -> None:
        store = getattr(self, "_memory_store", None)
        if store is None or getattr(self, "_incognito", False):
            return
        for row in store.get_by_entity(f"{_STYLE_ENTITY_PREFIX}{provider}"):
            store.delete(row["id"])

    def _register_style_tools(self) -> None:
        agent = self  # closure for live access to backends / memory / prompt

        @tool
        def build_style_profile(
            sample_size: int = DEFAULT_SENT_SAMPLE_SIZE, mailbox: str = ""
        ) -> str:
            """Learn the user's writing voice from their Sent mail (local-only).

            Samples recent Sent messages, derives greeting / sign-off /
            typical length / formality with deterministic on-device
            analysis (no message content is sent anywhere, not even to the
            local LLM), and persists the profile so future draft_reply
            bodies match the user's own voice. Rebuilding replaces the
            previous profile for that mailbox.

            Args:
                sample_size: How many recent Sent messages to sample
                    (default 25, max 100). At least 3 usable messages are
                    required.
                mailbox: Which connected mailbox to learn from (e.g.
                    ``google``); defaults to the primary mailbox.
            """
            try:
                size = int(sample_size)
                if size < MIN_STYLE_SAMPLE:
                    return _envelope_err(
                        f"build_style_profile: sample_size must be at least "
                        f"{MIN_STYLE_SAMPLE} (got {sample_size})."
                    )
                size = min(size, MAX_SENT_SAMPLE_SIZE)
                # Long-lived Agent UI instances: see newly connected mailboxes
                # without a session restart (same refresh as triage).
                agent._refresh_mail_backends()
                provider = mailbox or next(iter(agent._backends))
                backend = agent._backends.get(provider)
                if backend is None:
                    return _envelope_err(
                        f"build_style_profile: mailbox {provider!r} is not "
                        f"connected. Connected: "
                        f"{', '.join(agent._backends) or 'none'}."
                    )
                bodies = collect_sent_bodies(backend, sample_size=size)
                if len(bodies) < MIN_STYLE_SAMPLE:
                    return _envelope_err(
                        f"build_style_profile: only {len(bodies)} usable Sent "
                        f"message(s) found in mailbox {provider!r}; need at "
                        f"least {MIN_STYLE_SAMPLE}. Send a few emails first, "
                        "then rebuild the profile."
                    )
                profile = build_profile_from_bodies(bodies, mailbox=provider)
                agent._style_profiles[provider] = profile
                persisted = agent._persist_style_profile(provider, profile)
                # The profile feeds the draft-composition prompt; recompose so
                # it takes effect this session, not after a restart.
                agent.rebuild_system_prompt()
                return _envelope_ok({"profile": profile, "persisted": persisted})
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("build_style_profile failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def get_style_profile(mailbox: str = "") -> str:
            """Show the stored voice/style profile(s) learned from Sent mail.

            Args:
                mailbox: Restrict to one connected mailbox; default returns
                    every stored profile.
            """
            try:
                profiles = agent._style_profiles
                if mailbox:
                    profile = profiles.get(mailbox)
                    data = {"profiles": [profile] if profile else []}
                else:
                    data = {"profiles": [profiles[p] for p in sorted(profiles)]}
                if not data["profiles"]:
                    data["hint"] = (
                        "No style profile yet — run build_style_profile to "
                        "learn the user's voice from their Sent mail."
                    )
                return _envelope_ok(data)
            except Exception as exc:
                log.exception("get_style_profile failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def clear_style_profile(mailbox: str = "") -> str:
            """Delete the stored voice/style profile(s), in-process and persisted.

            Drafts fall back to the default (profile-free) composition
            prompt immediately.

            Args:
                mailbox: Clear only this mailbox's profile; default clears
                    all of them.
            """
            try:
                providers = [mailbox] if mailbox else list(agent._style_profiles)
                cleared: List[str] = []
                for provider in providers:
                    if provider in agent._style_profiles:
                        del agent._style_profiles[provider]
                        cleared.append(provider)
                    agent._delete_persisted_style_profile(provider)
                agent.rebuild_system_prompt()
                return _envelope_ok({"cleared": cleared})
            except Exception as exc:
                log.exception("clear_style_profile failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
