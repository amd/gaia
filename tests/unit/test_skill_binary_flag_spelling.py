# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The command gate reads a flag the way the CLI's own parser does.

Two spellings used to slip past it: a value-taking flag written ``--flag=``
with nothing after the ``=``, and git's abbreviated long options and clustered
short options.
"""

from __future__ import annotations

import shlex

import pytest

from gaia.skills.binaries import (
    ALLOW,
    BINARY_POLICIES,
    CONFIRM,
    REFUSE,
    BinaryPolicy,
    Subcommand,
    _split_flag,
    classify_invocation,
)


def _decide(command: str):
    argv = shlex.split(command)
    return classify_invocation(BINARY_POLICIES[argv[0]], argv)


# --- empty attached values ---------------------------------------------------


@pytest.mark.parametrize(
    ("token", "kwargs", "expected"),
    [
        ("--jq=", {}, ("--jq", "")),
        ("--jq=.x", {}, ("--jq", ".x")),
        ("--jq", {}, ("--jq", None)),
        ("-X=", {}, ("-X", "")),
        ("-X=GET", {}, ("-X", "GET")),
        ("-XGET", {}, ("-X", "GET")),
        ("-X", {}, ("-X", None)),
        ("-exec=", {"single_dash_long": True}, ("-exec", "")),
        ("-exec", {"single_dash_long": True}, ("-exec", None)),
    ],
)
def test_split_flag_keeps_an_empty_attached_value(token, kwargs, expected):
    assert _split_flag(token, **kwargs) == expected


@pytest.mark.parametrize(
    "command",
    [
        "gh api --jq= --input body.json repos/o/r/issues",
        "gh api --method= -XDELETE repos/o/r",
        "git commit -m= --amend",
        "git commit --message= --no-verify",
        "pytest -k= -p evil",
        "python -W= -c print(1)",
    ],
)
def test_an_empty_attached_value_is_refused(command):
    decision = _decide(command)
    assert decision.outcome == REFUSE
    assert "needs a value" in decision.message


@pytest.mark.parametrize(
    ("command", "outcome"),
    [
        ("gh api --jq .x repos/o/r/issues", ALLOW),
        ("gh api --jq=.x repos/o/r/issues", ALLOW),
        ("gh api --method=GET repos/o/r", ALLOW),
        # gh refuses every bundled short flag, attached value or not.
        ("gh api -XGET repos/o/r", REFUSE),
        ("gh api -X= DELETE repos/o/r", REFUSE),
        ("git commit -m fix", CONFIRM),
        ("git commit --message=fix", CONFIRM),
        ("pytest -k foo tests/unit", ALLOW),
    ],
)
def test_a_flag_with_its_value_still_runs(command, outcome):
    assert _decide(command).outcome == outcome


# --- git abbreviations and short clusters ------------------------------------


@pytest.mark.parametrize(
    ("command", "read_as"),
    [
        ("git diff --ext", "--ext-diff"),
        ("git diff --textc", "--textconv"),
        ("git log --outp=out.txt", "--output"),
        ("git commit --amen -m x", "--amend"),
        ("git commit --no-veri -m x", "--no-verify"),
        ("git checkout --forc main", "--force"),
        ("git branch --del topic", "--delete"),
        ("git commit -an -m x", "-n"),
        ("git checkout -qf main", "-f"),
        ("git branch -vD topic", "-D"),
        ("git grep -nO foo", "-O"),
        ("git add -Ap", "-p"),
    ],
)
def test_git_denied_flags_are_matched_in_every_spelling_git_accepts(command, read_as):
    decision = _decide(command)
    assert decision.outcome == REFUSE
    assert read_as in decision.message


@pytest.mark.parametrize(
    ("command", "outcome"),
    [
        ("git diff --text", ALLOW),
        ("git diff --exit-code", ALLOW),
        ("git diff --no-ext-diff", ALLOW),
        ("git log --filter=blob:none", ALLOW),
        ("git log --output-indicator-new=+", ALLOW),
        ("git log --oneline -n5", ALLOW),
        ("git grep -n foo", ALLOW),
        ("git branch -v", ALLOW),
        ("git commit -am fix", CONFIRM),
        # Letters after a value-taking flag are its value, not more flags.
        ("git commit -amnote", CONFIRM),
        ("git stash push -mfix", CONFIRM),
        ("git checkout main", CONFIRM),
        ("git checkout -b topic", CONFIRM),
        # A value attached to a value-taking short option is not more flags.
        ("git log -SOAuth", ALLOW),
        ("git log -S OAuth", ALLOW),
        ("git log -GOpen", ALLOW),
        ("git log -L:Open:f.py", ALLOW),
        ("git log -n5", ALLOW),
        ("git log -M50%", ALLOW),
        ("git diff -U5", ALLOW),
        ("git diff -IOld", ALLOW),
        ("git show -SOAuth HEAD", ALLOW),
        ("git grep -eOpen", ALLOW),
        ("git grep -e Open", ALLOW),
        ("git grep -A3 -eOpen", ALLOW),
        ("git blame -L1,5 f.py", ALLOW),
        ("git ls-files -xOld", ALLOW),
        ("git checkout -bfix", CONFIRM),
        ("git switch -cfeature", CONFIRM),
    ],
)
def test_ordinary_git_flags_still_run(command, outcome):
    assert _decide(command).outcome == outcome


@pytest.mark.parametrize(
    ("command", "read_as"),
    [
        # An optional value is attached only, so the next word is a flag again.
        ("git log -M --output=x", "--output"),
        ("git diff -U --outp=x", "--output"),
        ("git blame -C --textc f.py", "--textconv"),
        # A required value is the next word; what follows it is a flag again.
        ("git grep -e Open -O", "-O"),
        ("git log -S x --ext", "--ext-diff"),
    ],
)
def test_a_flag_after_a_value_taking_option_is_still_checked(command, read_as):
    decision = _decide(command)
    assert decision.outcome == REFUSE
    assert read_as in decision.message


def test_both_scan_paths_word_an_abbreviated_denial_the_same():
    """The positional path names the typed token and what the CLI reads it as."""
    policy = BinaryPolicy(
        binary="tool",
        summary="tool",
        install_hint="install tool",
        abbreviated_options=True,
        positional=Subcommand(
            value_flags=frozenset({"-k"}),
            denied_flags=frozenset({"--danger", "-x"}),
            denied_flag_reasons={"--danger": "it is dangerous", "-x": "it is bad"},
        ),
    )

    abbreviated = classify_invocation(policy, ["tool", "--dang"])
    clustered = classify_invocation(policy, ["tool", "-qx"])
    exact = classify_invocation(policy, ["tool", "--danger"])

    assert abbreviated.message == (
        "'tool --dang' is not allowed (tool reads it as --danger): it is dangerous."
    )
    assert clustered.message == (
        "'tool -qx' is not allowed (tool reads it as -x): it is bad."
    )
    assert exact.message == "'tool --danger' is not allowed: it is dangerous."
    assert _decide("git diff --ext").message == (
        "'git diff --ext' is not allowed (git reads it as --ext-diff): "
        + _decide("git diff --ext-diff").message.split(": ", 1)[1]
    )


def test_exact_denied_flag_message_is_unchanged():
    decision = _decide("git diff --ext-diff")
    assert decision.outcome == REFUSE
    assert decision.message.startswith("'git diff --ext-diff' is not allowed: ")


def test_prefix_matching_is_limited_to_git():
    """pflag (gh) has no abbreviations, so a prefix there is a different flag."""
    assert not BINARY_POLICIES["gh"].abbreviated_options
    assert BINARY_POLICIES["git"].abbreviated_options


# --- per-subcommand exact options ----------------------------------------------

#: The subcommands that define each exempted option, per git's own docs. Only
#: these may spell it; anywhere else git reads it as an abbreviation.
_GIT_EXACT_OPTIONS = {
    "diff": {"--text"},
    "log": {"--text", "--filter"},
    "show": {"--text"},
    "grep": {"--text"},
    "stash": {"--text"},
}


def test_git_exact_options_are_pinned_to_the_subcommands_that_define_them():
    policy = BINARY_POLICIES["git"]
    actual = {
        name: set(rule.exact_options)
        for name, rule in policy.subcommands.items()
        if rule.exact_options
    }
    assert actual == _GIT_EXACT_OPTIONS
    assert not hasattr(policy, "exact_options")


@pytest.mark.parametrize(
    "command",
    [
        "git diff --text",
        "git diff --text HEAD~1 -- f.bin",
        "git diff --text --stat",
        "git log -p --text",
        "git log --text -SOAuth",
        "git log --filter=blob:none",
        "git log --filter blob:none",
        "git show --text HEAD",
        "git grep --text foo",
        "git grep --text -eOpen x",
        "git stash show --text",
        "git stash list --text",
    ],
)
def test_a_subcommand_that_defines_the_option_may_spell_it(command):
    assert _decide(command).outcome == ALLOW


def _git_call(subcommand: str) -> str:
    """``git <subcommand>``, plus the action a rule with actions needs first."""
    return "git stash show" if subcommand == "stash" else f"git {subcommand}"


@pytest.mark.parametrize(
    "subcommand",
    sorted(set(BINARY_POLICIES["git"].subcommands) - set(_GIT_EXACT_OPTIONS)),
)
def test_text_is_refused_where_git_reads_it_as_textconv(subcommand):
    decision = _decide(f"{_git_call(subcommand)} --text")
    assert decision.outcome == REFUSE
    assert "--textconv" in decision.message


@pytest.mark.parametrize(
    "subcommand",
    sorted(set(BINARY_POLICIES["git"].subcommands) - {"log"}),
)
def test_filter_is_refused_where_git_may_read_it_as_filters(subcommand):
    decision = _decide(f"{_git_call(subcommand)} --filter=blob:none")
    assert decision.outcome == REFUSE
    assert "--filters" in decision.message


@pytest.mark.parametrize(
    ("command", "read_as"),
    [
        # git releases before `cat-file --filter` expand it to --filters.
        ("git cat-file --filter blob HEAD:f.txt", "--filters"),
        ("git cat-file --filter=blob:none --batch-all-objects", "--filters"),
        ("git cat-file --filt blob HEAD:f.txt", "--filters"),
        ("git cat-file --text HEAD:f.txt", "--textconv"),
        ("git cat-file --tex HEAD:f.txt", "--textconv"),
        ("git blame --text f.py", "--textconv"),
        ("git blame --text=1 f.py", "--textconv"),
        ("git blame -L1,5 --text f.py", "--textconv"),
        ("git blame -C --tex f.py", "--textconv"),
        ("git shortlog --text", "--textconv"),
        # A strict prefix of an exempt option is still an abbreviation, and
        # ambiguous ones are refused rather than guessed at.
        ("git diff --tex", "--textconv"),
        ("git diff --te", "--textconv"),
        ("git diff --textc", "--textconv"),
        ("git grep --textco foo", "--textconv"),
        ("git log --tex", "--textconv"),
        ("git log --filte=blob:none", "--filters"),
        ("git log --filt", "--filters"),
        ("git show --t HEAD", "--textconv"),
        # The exemption is the exact name, not anything that starts with it.
        ("git diff --textconv", "--textconv"),
        ("git log --filters", "--filters"),
    ],
)
def test_abbreviations_of_a_denied_option_stay_refused(command, read_as):
    decision = _decide(command)
    assert decision.outcome == REFUSE
    assert read_as in decision.message


@pytest.mark.parametrize(
    ("command", "outcome"),
    [
        # Short clusters: -a is --text, harmless; the letters after a
        # value-taking option are its value.
        ("git diff -aw", ALLOW),
        ("git grep -an foo", ALLOW),
        ("git log -pSfoo", ALLOW),
        ("git log -SOAuth", ALLOW),
        ("git grep -eOpen x", ALLOW),
        ("git log -L:Open:f.py", ALLOW),
        ("git log -n5 --text", ALLOW),
        ("git diff --no-textconv", ALLOW),
        ("git log --no-ext-diff --text", ALLOW),
        ("git grep -naO foo", REFUSE),
        ("git grep -aO foo", REFUSE),
        ("git log -pS x --textc", REFUSE),
    ],
)
def test_clusters_and_attached_values_around_the_exemption(command, outcome):
    assert _decide(command).outcome == outcome


def test_an_exact_option_must_shadow_a_denied_flag():
    Subcommand(
        denied_flags=frozenset({"--textconv"}), exact_options=frozenset({"--text"})
    )
    for idle in ("--text", "-t", "--textconv"):
        with pytest.raises(ValueError, match="exact_options"):
            Subcommand(
                denied_flags=frozenset({"--textconv"} if idle != "--text" else set()),
                exact_options=frozenset({idle}),
            )
