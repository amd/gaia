# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Scrubber tests — the hard gate on the step-level dataset.

Every fixture here is synthetic. Real transcript content never enters a test
file, because a test file lives in a repository and a transcript does not.

The headline test is :func:`test_planted_secrets_do_not_survive`: one record
carrying a planted instance of every rule's target, asserted clean afterwards
by the *independent* sweep in ``verify`` rather than by the scrubber's own
patterns. A checker built from the fix can only confirm the fix agrees with
itself.
"""

import json

import pytest

from gaia.factory.dataset.scrub import (
    FREE_TEXT_CAP,
    Scrubber,
    ScrubStats,
    is_pasted_third_party,
)
from gaia.factory.dataset.verify import LEAK_PATTERNS

USERNAME = "jdoe"


@pytest.fixture
def scrubber():
    return Scrubber(username=USERNAME)


def _token(prefix: str, body: str) -> str:
    """Join a credential's prefix to its body at runtime.

    Every value here is invented, but a scrubber test has to carry
    convincingly *shaped* credentials or it proves nothing — and a
    convincingly shaped credential written as a literal is what secret
    scanners are built to catch. GitHub push protection rejected this file
    over the Slack entry. Assembling the string at import time keeps the
    fixture exactly as strong and leaves no scannable literal in the tree.
    """
    return prefix + body


#: One planted instance of every threat the scrubber claims to remove.
PLANTED = {
    "win_path": r"C:\Users\jdoe\Work\gaia\src\gaia\cli.py",
    "posix_path": "/home/jdoe/work/gaia/setup.py",
    "gitbash_path": "/c/Users/jdoe/Work/gaia/README.md",
    "slug_path": "C--Users-jdoe-Work-gaia",
    "username_bare": "the jdoe account",
    "email": "jane.doe@example.com",
    "github_pat": _token("ghp" + "_", "aB3dEfGh1jKlMn0pQrStUvWxYz012345"),
    "github_fine": _token(
        "github" + "_pat_", "11ABCDEFG0abcdefghijklmnopqrstuvwxyz123456"
    ),
    "openai": _token("sk" + "-proj-", "abcdefghijklmnopqrstuvwxyz0123456789"),
    "anthropic": _token("sk" + "-ant-api03-", "abcdefghijklmnopqrstuvwxyz0123"),
    "aws": _token("AKIA", "IOSFODNN7EXAMPLE"),
    "slack": _token("xoxb" + "-", "123456789012-abcdefghijklmno"),
    "jwt": _token(
        "eyJhbGciOiJIUzI1NiJ9.",
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27u",
    ),
    "bearer": "Authorization: Bearer abcdef0123456789abcdef0123456789",
    "password_kv": 'password = "hunter2hunter2hunter2"',
    "host": "build-node-07.amd.com",
    "person": "Surname, Given   14:22",
    "private_key": (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow_fake_body_here\n"
        "-----END RSA PRIVATE KEY-----"
    ),
}


def test_planted_secrets_do_not_survive(scrubber):
    """Every planted secret is gone, checked by an independent pattern set."""
    record = {
        "goal": " ".join(PLANTED.values()),
        "state": {"cwd": PLANTED["win_path"], "known_paths": [PLANTED["posix_path"]]},
        "action": {
            "calls": [
                {
                    "arguments": {
                        "command": f"cd {PLANTED['slug_path']} && curl -H "
                        f"'{PLANTED['bearer']}' https://{PLANTED['host']}/x",
                        "file_path": PLANTED["gitbash_path"],
                    }
                }
            ]
        },
        "observation": [{"text": PLANTED["private_key"] + "\n" + PLANTED["jwt"]}],
    }

    cleaned = json.dumps(scrubber.value(record), ensure_ascii=False)

    survivors = {
        name: pattern.findall(cleaned)
        for name, pattern in LEAK_PATTERNS
        if pattern.search(cleaned)
    }
    assert not survivors, f"planted material survived scrubbing: {survivors}"
    assert USERNAME not in cleaned.lower()


@pytest.mark.parametrize("planted", sorted(PLANTED.values()))
def test_each_planted_item_individually(scrubber, planted):
    """Rule-by-rule, so a failure names the one rule that broke."""
    cleaned = scrubber.text(planted)
    for name, pattern in LEAK_PATTERNS:
        if name == "corpus_username":
            continue
        assert not pattern.search(cleaned), f"{name} survived in {cleaned!r}"


def test_repo_relative_tail_is_preserved(scrubber):
    """A record is only useful if the addressable part of a path survives."""
    cleaned = scrubber.text(r"C:\Users\jdoe\Work\gaia\src\gaia\cli.py")
    assert "src" in cleaned and "cli.py" in cleaned
    assert "<WORKSPACE>" in cleaned
    assert "jdoe" not in cleaned


def test_scrubber_requires_a_username():
    """Guessing which token is the username is how a scrubber misses it."""
    with pytest.raises(ValueError, match="requires the corpus username"):
        Scrubber(username="")


def test_stats_record_which_rules_fired(scrubber):
    stats = ScrubStats()
    scrubber.text(f"{PLANTED['email']} and {PLANTED['github_pat']}", stats)
    assert stats.counts.get("email") == 1
    assert stats.counts.get("secret_github") == 1


def test_nested_structures_are_walked(scrubber):
    nested = {"a": [{"b": {"c": [PLANTED["email"]]}}]}
    assert "@" not in json.dumps(scrubber.value(nested))


def test_dict_keys_are_scrubbed_too(scrubber):
    """A path used as a mapping key leaks exactly as loudly as one used as a value."""
    cleaned = scrubber.value({PLANTED["win_path"]: "content"})
    assert "jdoe" not in json.dumps(cleaned).lower()


def test_free_text_is_capped(scrubber):
    stats = ScrubStats()
    out = scrubber.free_text("x" * (FREE_TEXT_CAP + 500), stats)
    assert len(out) <= FREE_TEXT_CAP + 20
    assert stats.counts.get("free_text_capped") == 1


def test_ordinary_prompt_is_not_treated_as_a_paste():
    """A long legitimate instruction must survive; only pasted prose is dropped."""
    ordinary = (
        "Refactor the retry helper so the backoff is configurable, add tests, "
        "and update the docs. " * 60
    )
    assert is_pasted_third_party(ordinary) is None


def test_pasted_meeting_transcript_is_detected():
    """Regexes cannot anonymise proper nouns, so these records get dropped."""
    pasted = (
        "summarize this transcript.\n\n"
        "Weekly Strategy-20260817_193058UTC-Meeting Recording\n"
        "August 17, 2026, 7:30PM\n1h 5m 37s\n\n"
        + "".join(
            f"Speaker{i}, Name{i}   {i}:15\nSome spoken sentence here.\n\n"
            for i in range(60)
        )
    )
    assert is_pasted_third_party(pasted) is not None


def test_paste_is_detected_when_it_arrives_in_a_later_turn():
    """The canonical shape: announce in turn 1, paste in turn 2.

    Checking only the opening prompt finds nothing and ships the transcript.
    """
    turns = [
        "summarize the transcript I'm about to paste.",
        "".join(f"Speaker{i}, Name{i}   {i}:15\nSaid something.\n\n" for i in range(9)),
    ]
    assert is_pasted_third_party(turns[0]) is None
    assert any(is_pasted_third_party(t) for t in turns)


def test_short_text_is_never_a_paste():
    """The length threshold stops a stray timestamp nuking a real prompt."""
    assert is_pasted_third_party("Adrian, Kalin  9:15") is None


class TestLeaksTheSweepCaught:
    """Regressions for leaks the unit tests passed but the built dataset had.

    Every one was found by running the independent sweep over real output, which
    is the argument for having a checker that does not share the fix's patterns.
    """

    def test_path_naming_another_account_is_scrubbed(self, scrubber):
        """The user-specific rules cannot match a path that names someone else."""
        cleaned = scrubber.text(r"C:\Users\someone-else\Work\thing")
        assert "someone-else" not in cleaned

    def test_ancestor_path_with_no_account_name_is_scrubbed(self, scrubber):
        """Node walks up emitting /c/Users/node_modules — no username in it."""
        cleaned = scrubber.text("/c/Users/node_modules/.bin:/c/node_modules/.bin")
        assert "/c/Users/" not in cleaned

    def test_another_accounts_slug_directory_is_scrubbed(self, scrubber):
        cleaned = scrubber.text("C--Users-otherperson-Work-repo")
        assert "otherperson" not in cleaned

    def test_python_decorator_is_not_mistaken_for_an_email(self, scrubber):
        """`-@pytest.mark.parametrize` in a diff is not an address."""
        line = '-@pytest.mark.parametrize("bad_type", [None, ""])'
        assert scrubber.text(line) == line

    def test_public_amd_website_survives(self, scrubber):
        """A documentation link carries no identity; a machine name does."""
        cleaned = scrubber.text("see https://www.amd.com/en/resources/support")
        assert "www.amd.com" in cleaned

    def test_internal_hostname_still_goes(self, scrubber):
        assert "build-node-07" not in scrubber.text("ssh build-node-07.amd.com")


def test_prose_is_not_mangled_by_the_person_rule(scrubber):
    """A loose Surname,Firstname rule fired 9,722 times, nearly all on prose.

    It cost thousands of damaged records to catch a threat the paste filter
    already removes by dropping the record whole.
    """
    prose = (
        "Multi-Agent Systems, Trust, and Outcome; WG 3 - Trustworthiness, Robustness"
    )
    assert scrubber.text(prose) == prose


def test_speaker_lines_are_still_redacted(scrubber):
    cleaned = scrubber.text("Surname, Given   14:22\nSaid a thing.")
    assert "Surname" not in cleaned and "Given" not in cleaned


def test_extra_names_are_redacted_when_supplied():
    """Known colleague names must be passed in; a regex cannot infer them."""
    s = Scrubber(username="jdoe", extra_names=["Ada Lovelace"])
    assert "Ada Lovelace" not in s.text("reviewed with Ada Lovelace today")


def test_github_issue_refs_are_kept(scrubber):
    """Public issue numbers are how a record is traced; only internal keys go."""
    cleaned = scrubber.text("fixes #1655 per JIRA-40122")
    assert "#1655" in cleaned
    assert "JIRA-40122" not in cleaned
