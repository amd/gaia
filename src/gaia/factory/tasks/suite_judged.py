# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The use cases a command cannot fully decide — covered anyway, labelled clearly.

Three use cases were previously excluded for having no mechanical notion of
correct. Leaving them out meant the suite silently claimed that work the agent
is genuinely asked to do did not count, so they are covered here instead. Two
turned out to be mostly mechanical after all once the workspace carried the
right evidence:

* ``pr_triage`` — needs a live forge only if you insist on talking to one. The
  decision itself ("is this mergeable, and what blocks it") is checkable when
  the diff, the review thread and the CI log are in the workspace.
* ``web_research`` — genuinely needs the network, and no verifier can pin a
  fact that may change tomorrow. Judged.
* ``slide_deck`` — genuinely has no correct answer. Judged, with a structural
  floor so an empty file cannot pass.

``eval_benchmark`` stays out: running an eval inside an eval measures the inner
harness, not the model.

**Judged tasks are never mixed into the verified pass rate.** Their `verify`
command is a floor — the artefact exists and is substantial — and the real
assessment is the rubric. The report gives them their own column.
"""

from __future__ import annotations

from typing import Sequence

from .suite import Task

JUDGED: Sequence[Task] = (
    Task(
        key="pr_triage_blocking",
        use_case="pr_triage",
        track="operate",
        prompt=(
            "Review the pull request in this folder — the diff, the review "
            "thread and the CI log are all here. Decide whether it can merge, "
            "and write your decision plus every blocking issue to triage.md."
        ),
        setup={
            "pr.diff": """\
diff --git a/src/billing/invoice.py b/src/billing/invoice.py
index 1a2b3c4..5d6e7f8 100644
--- a/src/billing/invoice.py
+++ b/src/billing/invoice.py
@@ -12,7 +12,10 @@ def total(items):
-    return sum(i.price for i in items)
+    subtotal = sum(i.price for i in items)
+    # Apply the new regional tax
+    tax = subtotal * TAX_RATE
+    return subtotal + tax

@@ -30,4 +33,4 @@ def format_invoice(inv):
-    return f"Invoice {inv.id}: {total(inv.items):.2f}"
+    return f"Invoice {inv.id}: {total(inv.items)}"
""",
            "review_thread.md": """\
**dana** (requested changes, 2 days ago)
`TAX_RATE` is used in `total()` but I can't find where it's defined or
imported. Is this going to NameError at runtime?

**author** (1 day ago)
Good catch, will fix.

**priya** (comment, 1 day ago)
Separately — you dropped the `:.2f` from format_invoice, so invoices will now
print full float precision. That's a user-visible regression.

**author** (1 day ago)
Pushed a fix for the tax rate.

**dana** (comment, 4 hours ago)
Still not seeing TAX_RATE defined anywhere in the diff. Re-requesting changes.
""",
            "ci.log": """\
Run pytest -q
tests/test_invoice.py .F..                                              [ 80%]
=================================== FAILURES ===================================
________________________________ test_total ___________________________________
>       assert total([Item(10.0), Item(5.0)]) == 15.0
E       NameError: name 'TAX_RATE' is not defined
tests/test_invoice.py:14: NameError
=========================== short test summary info ============================
FAILED tests/test_invoice.py::test_total - NameError
1 failed, 4 passed in 1.02s
""",
        },
        # Decidable: the PR cannot merge, and there are exactly two blockers
        # evidenced in the workspace. A reviewer who says "looks good" or finds
        # only the loud one is wrong, and that is checkable.
        verify=(
            'python -c "'
            "t=open('triage.md',encoding='utf-8').read().lower();"
            "assert 'tax_rate' in t, 'missed the undefined TAX_RATE blocker';"
            "assert ('.2f' in t or 'precision' in t or 'format' in t), "
            "'missed the formatting regression priya raised';"
            "blocked=any(w in t for w in "
            "('do not merge','cannot merge','not ready','block','request changes','changes requested'));"
            "assert blocked, 'did not conclude the PR is blocked'"
            '"'
        ),
        rubric=(
            "Did it find both blockers and correctly refuse the merge? Did it "
            "notice the author claimed to have fixed the tax rate but the diff "
            "still does not define it? Any invented blockers?"
        ),
        expect_touched=("triage.md",),
    ),
    Task(
        key="slide_deck_release",
        use_case="slide_deck",
        track="content",
        scoring="judged",
        prompt=(
            "Turn the release notes in RELEASE.md into a short slide deck for a "
            "team meeting — write it to deck.md, one slide per `##` heading. "
            "Lead with what matters to users."
        ),
        setup={
            "RELEASE.md": """\
# widget 1.3.0

## Changes

- Added support for gzip-compressed inputs. Files up to 2GB now import in
  about a third of the time; previously they had to be decompressed by hand
  first, which was the single most common support request.
- Fixed timestamps losing their timezone offset. Any export produced between
  1.1.0 and 1.2.3 has timestamps that are silently wrong by the local UTC
  offset; re-exporting fixes them.
- Dropped support for Python 3.10, which reached end of life. 3.11 or later is
  now required.
- Internal: replaced the retry decorator with a shared helper. No behaviour
  change.
- Internal: bumped six pinned dev dependencies.
""",
        },
        # A floor only. Whether the deck is any *good* — whether it leads with
        # the data-corruption fix rather than the dependency bump — is exactly
        # what no regex can decide, and is left to the rubric.
        verify=(
            'python -c "'
            "t=open('deck.md',encoding='utf-8').read();"
            "slides=[l for l in t.splitlines() if l.strip().startswith('##')];"
            "assert len(slides)>=3, f'only {len(slides)} slides';"
            "assert len(t)>300, 'deck is too thin to present'"
            '"'
        ),
        rubric=(
            "**This is the whole assessment — no command can decide it.** Does "
            "the deck lead with the timezone bug, which silently corrupted user "
            "data, rather than with gzip or the dependency bump? Is the 3.10 "
            "drop presented as the action it requires? Are the two 'internal' "
            "items correctly left off or demoted? Would this deck actually work "
            "in a meeting, or is it the release notes with headings changed?"
        ),
        expect_touched=("deck.md",),
    ),
    Task(
        key="web_research_compare",
        use_case="web_research",
        track="decide",
        scoring="judged",
        prompt=(
            "We need to pick a Python library for parsing TOML on Python 3.11+. "
            "Research the current options, then write findings.md with a "
            "recommendation and the reasoning behind it."
        ),
        setup={
            "NOTES.md": (
                "# Decision needed\n\n"
                "We parse TOML in three places and currently vendor our own "
                "parser. We want to drop it.\n\n"
                "Constraint: Python 3.11+ only. No hard requirement on write "
                "support, but say whether the option has it.\n"
            ),
        },
        # Judged: any fact a verifier could pin here may be false next month,
        # and a suite that hard-codes today's ecosystem rots into a test of how
        # stale the benchmark is. The floor only checks that research happened
        # and produced a recommendation.
        verify=(
            'python -c "'
            "t=open('findings.md',encoding='utf-8').read();"
            "low=t.lower();"
            "assert len(t)>400, 'too short to contain research';"
            "assert 'recommend' in low or 'suggest' in low or 'use ' in low, "
            "'no recommendation made'"
            '"'
        ),
        rubric=(
            "**This is the whole assessment — no command can decide it.** Did "
            "the agent actually look anything up, or write from memory? A "
            "correct answer notes that Python 3.11 added `tomllib` to the "
            "standard library for reading, so no dependency is needed unless "
            "writing is required — an answer recommending a third-party reader "
            "without mentioning `tomllib` has missed the point. Is the "
            "write-support question answered? Are any claimed facts wrong?"
        ),
        expect_touched=("findings.md",),
    ),
)
