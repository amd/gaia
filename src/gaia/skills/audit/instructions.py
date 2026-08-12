# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Instruction-body analysis — the prompt-injection scanner.

A skill's Markdown body is concatenated into the model's context when the skill
triggers. That makes it untrusted input in the same way a fetched web page is,
and it is the reason an instruction-only skill is not a "safe" skill: it carries
no code, but it can still tell the model to bypass a confirmation, conceal an
action from the user, or read a credential file with the agent's own tools.

This module resolves the body-injection open question recorded in both
``docs/spec/agent-skills.mdx`` and ``docs/plans/skill-format.mdx``. The answer:
**scan every skill's body at publish, tier-scale the consequence.** Because the
findings feed the shared tier gate, ``experimental`` gets the finding as advice
while ``community`` and above are actually gated by it — which is exactly the
"required for community+, advisory for experimental" split the specs asked about.

Two design choices keep the scan trustworthy:

- **Regex is the right tool here** — unlike the code analyzer's AST, the body is
  prose, and there is no structure to parse.
- **Fenced and quoted text is downgraded by exactly one step — never ignored.**
  A skill that documents injection patterns so the model can *refuse* them is
  the good case, and blocking it would punish the security-conscious author. But
  fenced text is still concatenated into the model's context, and models do not
  reliably treat it as inert data. So a fence buys a step (``critical`` →
  ``high``: reviewed at ``community`` instead of rejected) and never a pass —
  otherwise "put the directive in a code block" would be the standing bypass.
  Hidden Unicode is exempt from the downgrade entirely, because there is no such
  thing as a documented zero-width character.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from gaia.skills.audit.findings import (
    CATEGORY_PROMPT_INJECTION,
    SEVERITY_ORDER,
    Finding,
    Severity,
)
from gaia.skills.format import SKILL_FILENAME


@dataclass(frozen=True)
class InjectionRule:
    """One prompt-injection pattern."""

    rule_id: str
    severity: Severity
    pattern: re.Pattern[str]
    message: str
    remediation: str
    #: When False, being inside a code fence / blockquote does not soften it.
    downgradable: bool = True


def _rule(
    rule_id: str,
    severity: Severity,
    pattern: str,
    message: str,
    remediation: str,
    *,
    downgradable: bool = True,
) -> InjectionRule:
    return InjectionRule(
        rule_id=rule_id,
        severity=severity,
        pattern=re.compile(pattern, re.IGNORECASE),
        message=message,
        remediation=remediation,
        downgradable=downgradable,
    )


_OVERRIDE_FIX = (
    "A skill body adds capability; it must not attempt to replace the agent's "
    "own instructions. Describe what the model should do, not what it should "
    "stop obeying."
)

INJECTION_RULES: tuple[InjectionRule, ...] = (
    _rule(
        "body.injection.instruction_override",
        "critical",
        r"\b(?:ignore|disregard|forget|override|replace)\b[^.\n]{0,40}?"
        r"\b(?:previous|prior|above|earlier|initial|original|all)?\s*"
        r"(?:instruction|instructions|prompt|system prompt|directive|rule|rules|"
        r"guideline|guidelines|everything you)\b",
        "Instructs the model to disregard its own instructions.",
        _OVERRIDE_FIX,
    ),
    _rule(
        "body.injection.persona_override",
        "high",
        r"\b(?:you are now|act as (?:if you are|dan\b)|pretend to be|"
        r"roleplay as|developer mode|dan mode|jailbreak|"
        r"unrestricted (?:mode|assistant)|without (?:any )?restrictions)\b",
        "Attempts to replace the model's persona or lift its restrictions.",
        _OVERRIDE_FIX,
    ),
    _rule(
        "body.injection.guardrail_bypass",
        "high",
        r"\b(?:bypass|skip|suppress|disable|circumvent)\b[^.\n]{0,40}?"
        r"\b(?:confirmation|approval|permission|safety|safeguard|guardrail|check)\b"
        r"|\bauto[- ]approve\b"
        r"|\bwithout (?:asking|requesting|seeking|waiting for)\b[^.\n]{0,20}?"
        r"\b(?:permission|approval|confirmation|consent)\b",
        "Instructs the model to bypass a confirmation or approval step.",
        "Confirmation prompts are the user's only control over destructive "
        "tool calls. A skill must never instruct the model around them.",
    ),
    _rule(
        "body.injection.concealment",
        "high",
        r"\b(?:do not|don't|never)\b[^.\n]{0,30}?"
        r"\b(?:tell|inform|mention|reveal|disclose|show|notify|log)\b"
        r"[^.\n]{0,30}?\b(?:user|human|operator|anyone)\b"
        r"|\b(?:hide|conceal|withhold)\b[^.\n]{0,30}?\bfrom the (?:user|human)\b"
        r"|\bwithout (?:informing|telling|notifying|alerting)\b"
        r"[^.\n]{0,20}?\b(?:the )?(?:user|human)\b"
        r"|\b(?:do not|don't|never)\b[^.\n]{0,30}?"
        r"\bmention (?:this|these|that|it)\b",
        "Instructs the model to conceal an action from the user.",
        "Anything a skill does on the user's behalf must be visible to them. "
        "Remove the concealment instruction.",
    ),
    _rule(
        "body.injection.prompt_disclosure",
        "high",
        r"\b(?:reveal|print|output|repeat|show|disclose|dump|echo)\b"
        r"[^.\n]{0,40}?"
        r"\b(?:system prompt|system message|your instructions|"
        r"the instructions above|the text above|your prompt)\b",
        "Attempts to extract the agent's system prompt or instructions.",
        "A skill has no need for the agent's own instructions. Remove the request.",
    ),
    _rule(
        "body.injection.credential_request",
        "high",
        r"~?/?\.ssh/|\bid_rsa\b|\.aws/credentials|\bnetrc\b|\.git-credentials"
        # Verb-led: naming a secret is normal (declaring env_vars, documenting
        # setup); asking the model to go *read* one is not. The ALL_CAPS branch
        # is an object of the verb for that reason, never a standalone match.
        r"|\b(?:read|open|cat|print|show|list|include|output|reveal|dump|echo)\b"
        r"[^.\n]{0,40}?"
        r"(?:\.env\b|\bapi[_ ]key\b|\baccess[_ ]token\b|\bsecret[_ ]key\b|"
        r"\bpassword\b|\bcredentials\b|"
        r"[A-Z][A-Z0-9]*_(?:API_KEY|TOKEN|SECRET|PASSWORD)\b)",
        "Instructs the model to read credentials or secrets.",
        "Take secrets through a declared 'requirements.env_vars' entry or a "
        "connector grant. A skill must never instruct the model to go read "
        "the user's credential files.",
    ),
    _rule(
        "body.injection.exfiltration",
        "critical",
        r"\b(?:send|post|upload|transmit|forward|exfiltrate|leak|report)\b"
        r"[^.\n]{0,60}?"
        r"\b(?:environment variable|env var|credential|secret|api[_ ]key|token|"
        r"password|conversation|chat history|system prompt|user data)\w*\b"
        r"[^.\n]{0,60}?\b(?:to|at)\b[^.\n]{0,20}?(?:https?://|\bwebhook\b)"
        r"|\b(?:collect|gather|harvest)\b[^.\n]{0,50}?"
        r"\b(?:environment variables|credentials|secrets|api keys)\b"
        r"[^.\n]{0,80}?\b(?:post|send|upload|transmit)\b",
        "Instructs the model to send sensitive data to an external endpoint.",
        "Remove it. Sending user secrets or conversation content to a "
        "third-party endpoint is exfiltration regardless of the stated purpose.",
    ),
    _rule(
        "body.injection.tool_coercion",
        "medium",
        r"\balways call\b[^.\n]{0,40}?\b(?:regardless|no matter|even if|"
        r"without)\b"
        r"|\bregardless of (?:what|whether) the user\b",
        "Instructs the model to call a tool regardless of the user's request.",
        "Describe when a tool is appropriate and let the model decide. "
        "Unconditional tool coercion removes the user from the loop.",
    ),
)

#: Characters with no legitimate place in an instruction body.
_HIDDEN_CHAR_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x200B, 0x200F, "zero-width / directional formatting"),
    (0x202A, 0x202E, "bidirectional override"),
    (0x2060, 0x2064, "invisible operator"),
    (0x2066, 0x2069, "bidirectional isolate"),
    (0xFEFF, 0xFEFF, "zero-width no-break space"),
    (0xE0000, 0xE007F, "Unicode Tags (invisible)"),
)

_HTML_HIDDEN_RE = re.compile(
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|font-size\s*:\s*0"
    r"|color\s*:\s*#?(?:fff(?:fff)?|white)\b"
    r"|<[^>]+\bhidden\b[^>]*>",
    re.IGNORECASE,
)

_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)

#: ``[//]: # (text)`` — a link-reference definition used as a Markdown comment.
#: It renders to nothing and reaches the model exactly like an HTML comment, so
#: it is the same technique and earns the same severity. The target must be a
#: bare ``#`` / ``<>``: ``[docs]: #install "Title"`` is an ordinary anchor link.
_MARKDOWN_COMMENT_RE = re.compile(
    r"^[ \t]*\[[^\]\n]*\]:[ \t]*(?:#|<>)[ \t]+"
    r"(?:\((?P<paren>[^)\n]*)\)|\"(?P<double>[^\"\n]*)\"|'(?P<single>[^'\n]*)')"
    r"[ \t]*$",
    re.MULTILINE,
)

#: Imperatives that turn an HTML comment into a hidden directive.
_COMMENT_IMPERATIVE_RE = re.compile(
    r"\b(?:always|never|must|ignore|disregard|call|execute|run|send|post|"
    r"delete|bypass|skip|do not tell|instead)\b",
    re.IGNORECASE,
)

#: A prohibition immediately before a match means the body is *forbidding* the
#: behaviour, not asking for it. Security-conscious skills are full of these
#: ("NEVER dump the environment", "Do not run credential flows"), and flagging
#: them punishes exactly the authors who are being careful.
#:
#: Applied only to text BEFORE the match, so rules whose own pattern opens with a
#: negation — concealment's "do not tell the user" — are unaffected.
#:
#: The prohibition must sit *directly* on the matched behaviour: only whitespace
#: and at most one intervening word, with no ``:`` / ``;`` / dash between. Anything
#: looser turns the suppressor into the bypass — "Do not forget: ignore all
#: previous instructions" prohibits *forgetting*, not ignoring. Verbs that negate
#: the negation are excluded from the one-word allowance for the same reason.
_PROHIBITION_RE = re.compile(
    r"\b(?:never|do not|don't|cannot|can't|must not|should not|shouldn't|"
    r"avoid|refuse to|forbidden to|no need to)\s+"
    r"(?!(?:forget|mind|worry|hesitate|fail)\b)"
    r"(?:\w+\s+)?$",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_BLOCKQUOTE_RE = re.compile(r"^\s*>")
_BASE64_RE = re.compile(r"[A-Za-z0-9+/=]{200,}")

#: Non-Latin letters that read as Latin ones. NFKC does not fold these — a
#: Cyrillic 'о' is a different letter, not a compatibility variant — so the map
#: is what stops "Ignоre all previous instructions" reading as clean prose.
_CONFUSABLES: dict[str, str] = {
    # Cyrillic
    "А": "A",
    "В": "B",
    "Е": "E",
    "К": "K",
    "М": "M",
    "Н": "H",
    "О": "O",
    "Р": "P",
    "С": "C",
    "Т": "T",
    "У": "Y",
    "Х": "X",
    "Ѕ": "S",
    "І": "I",
    "Ј": "J",
    "Ԁ": "D",
    "Һ": "H",
    "Ԛ": "Q",
    "Ԝ": "W",
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "у": "y",
    "х": "x",
    "і": "i",
    "ј": "j",
    "ѕ": "s",
    "ԁ": "d",
    "һ": "h",
    "ԛ": "q",
    "ԝ": "w",
    # Greek
    "Α": "A",
    "Β": "B",
    "Ε": "E",
    "Ζ": "Z",
    "Η": "H",
    "Ι": "I",
    "Κ": "K",
    "Μ": "M",
    "Ν": "N",
    "Ο": "O",
    "Ρ": "P",
    "Τ": "T",
    "Υ": "Y",
    "Χ": "X",
    "α": "a",
    "ε": "e",
    "ι": "i",
    "κ": "k",
    "ν": "v",
    "ο": "o",
    "ρ": "p",
    "τ": "t",
    "υ": "u",
    "χ": "x",
    "γ": "y",
}

#: Ranges whose Latin letters are lookalikes by construction: fullwidth forms
#: and the mathematical alphanumeric planes.
_LOOKALIKE_RANGES: tuple[tuple[int, int], ...] = (
    (0xFF21, 0xFF3A),
    (0xFF41, 0xFF5A),
    (0x1D400, 0x1D7FF),
)

#: Markdown inline markers dropped before matching. ``_`` is deliberately absent:
#: it is not intra-word emphasis in Markdown, and the rules match on ``api_key``.
_MARKUP_CHARS = frozenset("*`~")

#: Words shorter than this are not accused of being homoglyph attacks — a
#: two-character mix like "μm" is a unit, not an evasion.
_MIN_CONFUSABLE_WORD = 3

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _downgrade(severity: Severity) -> Severity:
    """Lower a severity by one step, floored at ``low``."""
    index = SEVERITY_ORDER.index(severity)
    return SEVERITY_ORDER[max(1, index - 1)]


def _logical_blocks(body: str) -> list[tuple[str, list[tuple[int, int]]]]:
    """Group the body into paragraphs, each joined into one scannable string.

    Markdown reflows, so a line break carries no meaning — which would otherwise
    make the newline a free bypass for every rule here ("Do not\\ntell the user"
    matches nothing if you scan line by line). Each block is the paragraph's
    physical lines joined with a space, paired with a map from character offset
    to physical line number so a finding still reports the line the author sees.

    Sentence boundaries still stop a match: the rule patterns exclude ``.``, so
    joining lines cannot fabricate a match across two separate sentences.
    """
    blocks: list[tuple[str, list[tuple[int, int]]]] = []
    current: list[tuple[int, str]] = []

    def flush() -> None:
        if not current:
            return
        parts: list[str] = []
        offsets: list[tuple[int, int]] = []
        position = 0
        for number, text in current:
            offsets.append((position, number))
            parts.append(text)
            position += len(text) + 1  # +1 for the joining space
        blocks.append((" ".join(parts), offsets))
        current.clear()

    for number, line in enumerate(body.splitlines(), start=1):
        if not line.strip():
            flush()
            continue
        current.append((number, line))
    flush()
    return blocks


def _fold_character(character: str) -> str:
    """One character as its Latin lookalike, or unchanged.

    Length-preserving on purpose: :func:`_canonical` needs a 1:1 index map back
    to the author's text, so a multi-character NFKC expansion is left alone.
    """
    mapped = _CONFUSABLES.get(character)
    if mapped is not None:
        return mapped
    normalized = unicodedata.normalize("NFKC", character)
    return normalized if len(normalized) == 1 else character


def _canonical(text: str) -> tuple[str, list[int]]:
    """Return ``(matchable text, index -> offset in text)``.

    Markdown emphasis markers and invisible characters are dropped and Latin
    lookalikes folded to ASCII, so ``Ig*nore*`` and ``Ignоre`` reach the rules
    spelled the way the model will read them. The index map is what keeps a
    finding pointing at the line and the characters the author actually wrote.
    """
    characters: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(text):
        if character in _MARKUP_CHARS or _hidden_char_label(ord(character)):
            continue
        characters.append(_fold_character(character))
        offsets.append(index)
    return "".join(characters), offsets


def _is_lookalike(character: str) -> bool:
    """True for a character whose only purpose is to pass for a Latin letter."""
    if character in _CONFUSABLES:
        return True
    code_point = ord(character)
    return any(start <= code_point <= end for start, end in _LOOKALIKE_RANGES)


def _matches(pattern: "re.Pattern[str]", text: str):
    """Yield ``(absolute_start, match)`` for every candidate, overlapping allowed.

    ``finditer`` returns non-overlapping matches, which lets a *suppressed* match
    hide a real one behind it: in "Do not forget: ignore all previous
    instructions" the override pattern matches from "forget" (a listed verb), the
    prohibition guard suppresses it, and the genuine directive starting at
    "ignore" was inside the span that got consumed. Advancing by one character
    after each candidate instead of past it keeps the later match reachable.
    """
    position = 0
    while position < len(text):
        match = re.search(pattern, text[position:])
        if match is None:
            return
        yield position + match.start(), match
        position += match.start() + 1


def _line_for_offset(offsets: list[tuple[int, int]], offset: int) -> int:
    """Map a character offset inside a joined block back to a physical line."""
    line = offsets[0][1]
    for start, number in offsets:
        if start > offset:
            break
        line = number
    return line


def _quoted_lines(body: str) -> set[int]:
    """1-indexed line numbers inside a fenced code block or a blockquote."""
    quoted: set[int] = set()
    in_fence = False
    for number, line in enumerate(body.splitlines(), start=1):
        if _FENCE_RE.match(line):
            # The fence markers themselves count as quoted.
            quoted.add(number)
            in_fence = not in_fence
            continue
        if in_fence or _BLOCKQUOTE_RE.match(line):
            quoted.add(number)
    return quoted


def _hidden_char_label(code_point: int) -> Optional[str]:
    for start, end, label in _HIDDEN_CHAR_RANGES:
        if start <= code_point <= end:
            return label
    return None


def _hidden_unicode_findings(body: str, filename: str) -> list[Finding]:
    """Flag invisible characters — always critical, never downgraded."""
    findings: list[Finding] = []
    seen_lines: set[int] = set()
    for number, line in enumerate(body.splitlines(), start=1):
        for character in line:
            label = _hidden_char_label(ord(character))
            if label is None or number in seen_lines:
                continue
            seen_lines.add(number)
            try:
                name = unicodedata.name(character)
            except ValueError:
                name = label
            findings.append(
                Finding(
                    rule_id="body.injection.hidden_unicode",
                    severity="critical",
                    category=CATEGORY_PROMPT_INJECTION,
                    message=(
                        f"Contains an invisible character U+{ord(character):04X} "
                        f"({name}) — {label}."
                    ),
                    file=filename,
                    line=number,
                    remediation=(
                        "Remove it. Invisible characters are used to hide "
                        "instructions from a human reviewer while the model "
                        "still reads them; there is no legitimate use in a "
                        "skill body."
                    ),
                    snippet=line[:200],
                )
            )
            break
    return findings


def _confusable_findings(body: str, filename: str) -> list[Finding]:
    """Flag a Latin lookalike inside an otherwise-Latin word.

    Foreign-language prose is ordinary and is not touched: the rule fires only
    when one word mixes ASCII letters with characters from another script that
    are drawn to pass for them. That mix has no honest use — it is the visible
    cousin of the zero-width trick, aimed at a human reviewer rather than a
    parser.
    """
    findings: list[Finding] = []
    for number, line in enumerate(body.splitlines(), start=1):
        for word in _WORD_RE.findall(line):
            if len(word) < _MIN_CONFUSABLE_WORD:
                continue
            lookalikes = [c for c in word if _is_lookalike(c)]
            if not lookalikes or not any(c.isascii() and c.isalpha() for c in word):
                continue
            character = lookalikes[0]
            try:
                name = unicodedata.name(character)
            except ValueError:  # pragma: no cover - named in every live range
                name = "unnamed"
            findings.append(
                Finding(
                    rule_id="body.injection.homoglyph",
                    severity="high",
                    category=CATEGORY_PROMPT_INJECTION,
                    message=(
                        f"Spells a word with U+{ord(character):04X} ({name}), a "
                        "character from another script drawn to pass for a Latin "
                        "letter."
                    ),
                    file=filename,
                    line=number,
                    remediation=(
                        "Rewrite the word in plain ASCII. Mixing scripts inside "
                        "one word hides the word from a reviewer reading the "
                        "source while the model still receives it."
                    ),
                    snippet=word[:200],
                )
            )
            break  # one per line is enough to send the author to the line
    return findings


def _html_findings(body: str, filename: str) -> list[Finding]:
    """Flag CSS-hidden text and HTML comments that carry directives."""
    findings: list[Finding] = []
    for number, line in enumerate(body.splitlines(), start=1):
        if _HTML_HIDDEN_RE.search(line):
            findings.append(
                Finding(
                    rule_id="body.injection.hidden_html",
                    severity="high",
                    category=CATEGORY_PROMPT_INJECTION,
                    message="Contains text hidden by HTML/CSS from a human reader.",
                    file=filename,
                    line=number,
                    remediation=(
                        "Remove the hidden markup. The model reads the text a "
                        "reviewer cannot see, which is the whole point of the "
                        "technique."
                    ),
                    snippet=line[:200],
                )
            )

    offset = 0
    for match in _HTML_COMMENT_RE.finditer(body):
        comment = match.group(1)
        if not _COMMENT_IMPERATIVE_RE.search(comment):
            continue
        line = body.count("\n", 0, match.start()) + 1
        offset += 1
        findings.append(
            Finding(
                rule_id="body.injection.hidden_html",
                severity="high",
                category=CATEGORY_PROMPT_INJECTION,
                message=(
                    "Contains an HTML comment carrying an instruction. Comments "
                    "are invisible when the Markdown is rendered but are still "
                    "sent to the model."
                ),
                file=filename,
                line=line,
                remediation=(
                    "Move genuine notes-to-maintainers out of the skill body, "
                    "and delete anything addressed to the model — instructions "
                    "belong in the visible text."
                ),
                snippet=comment.strip()[:200],
            )
        )

    for match in _MARKDOWN_COMMENT_RE.finditer(body):
        comment = next(g for g in match.groups() if g is not None)
        if not _COMMENT_IMPERATIVE_RE.search(comment):
            continue
        findings.append(
            Finding(
                rule_id="body.injection.hidden_html",
                severity="high",
                category=CATEGORY_PROMPT_INJECTION,
                message=(
                    "Contains a Markdown link-label comment carrying an "
                    "instruction. It renders to nothing, exactly like an HTML "
                    "comment, but is still sent to the model."
                ),
                file=filename,
                line=body.count("\n", 0, match.start()) + 1,
                remediation=(
                    "Move genuine notes-to-maintainers out of the skill body, "
                    "and delete anything addressed to the model — instructions "
                    "belong in the visible text."
                ),
                snippet=comment.strip()[:200],
            )
        )
    return findings


def _encoded_payload_findings(body: str, filename: str) -> list[Finding]:
    """Flag long encoded literals — instructions the reviewer cannot read."""
    findings: list[Finding] = []
    for number, line in enumerate(body.splitlines(), start=1):
        match = _BASE64_RE.search(line)
        if match is None:
            continue
        findings.append(
            Finding(
                rule_id="body.injection.encoded_payload",
                severity="medium",
                category=CATEGORY_PROMPT_INJECTION,
                message=(
                    f"Contains a {len(match.group(0))}-character encoded blob "
                    "that a reviewer cannot read but the model receives."
                ),
                file=filename,
                line=number,
                remediation=(
                    "Put the content in the body as readable text, or ship it "
                    "as a separate data file the skill loads."
                ),
                snippet=match.group(0)[:120],
            )
        )
    return findings


def analyze_instructions(
    body: str, *, filename: str = SKILL_FILENAME
) -> tuple[Finding, ...]:
    """Scan an instruction body for prompt-injection patterns.

    Args:
        body: The Markdown body (or any model-facing text, e.g. a description).
        filename: Source label used in the findings' ``file`` field.

    Returns:
        Findings in source order; at most one per (rule, line) so a repeated
        phrase cannot bury the report under duplicates.
    """
    if not body:
        return ()

    quoted = _quoted_lines(body)
    findings: list[Finding] = []
    seen: set[tuple[str, int]] = set()

    for text, offsets in _logical_blocks(body):
        canonical, index_map = _canonical(text)
        for rule in INJECTION_RULES:
            for start, match in _matches(rule.pattern, canonical):
                if _PROHIBITION_RE.search(canonical[:start]):
                    # "NEVER dump the environment" forbids the behaviour.
                    continue
                span = match.end() - match.start()
                origin = index_map[start]
                number = _line_for_offset(offsets, origin)
                key = (rule.rule_id, number)
                if key in seen:
                    continue
                seen.add(key)

                severity = rule.severity
                note = ""
                if rule.downgradable and number in quoted:
                    severity = _downgrade(severity)
                    note = (
                        " Quoted or fenced, so it reads as documentation rather "
                        "than a directive — verify that is what it is."
                    )

                findings.append(
                    Finding(
                        rule_id=rule.rule_id,
                        severity=severity,
                        category=CATEGORY_PROMPT_INJECTION,
                        message=rule.message + note,
                        file=filename,
                        line=number,
                        remediation=rule.remediation,
                        # Sliced from the author's text, not the folded copy, so
                        # the report shows the characters actually shipped.
                        snippet=text[origin : index_map[start + span - 1] + 1].strip()[
                            :200
                        ],
                    )
                )

    findings.extend(_hidden_unicode_findings(body, filename))
    findings.extend(_confusable_findings(body, filename))
    findings.extend(_html_findings(body, filename))
    findings.extend(_encoded_payload_findings(body, filename))

    return tuple(sorted(findings, key=lambda f: (f.line, f.rule_id)))
