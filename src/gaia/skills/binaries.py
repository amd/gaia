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
        allowed_flags: For :attr:`BinaryPolicy.positional` rules only — the
            valueless flags this grant accepts. Positional-mode flag checking
            is an ALLOWLIST (unlike the subcommand mode's denylist above):
            a binary that takes no subcommand executes the caller's own
            arguments far more directly, so an unreviewed flag is refused
            rather than passed through.
        flag_value_prefixes: For positional rules — ``{flag: allowed lowercase
            value prefixes}``. Like ``flag_values`` but for a flag whose safe
            values share a prefix rather than an exact set (``-p no:...``).
        denied_flag_reasons: ``{flag: one-line reason}`` shown in the refusal.
            Falls back to a generic message when absent. Used by both rule
            shapes — a denied flag has to say *why* it is denied or the model
            retries it verbatim.
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

    def __post_init__(self) -> None:
        overlap = self.actions & self.confirm_actions
        if overlap:
            raise ValueError(
                "Subcommand actions and confirm_actions must be disjoint; "
                f"{sorted(overlap)} is in both. An action in both tiers reads "
                "as read-only and runs unprompted — the failure this table "
                "exists to prevent."
            )


@dataclass(frozen=True)
class BinarySetup:
    """How GAIA installs and authenticates one CLI **on the user's behalf**.

    Data, not code. The setup engine (:mod:`gaia.skills.binary_setup`) reads
    this and runs it; adding self-setup for a second CLI is another entry here,
    never a new branch in the engine — the same acceptance test the invocation
    table holds itself to.

    Every command is a fixed ``argv`` list, never a shell string, and never
    interpolates anything the model said. The model chooses *which binary* to
    set up; it cannot choose what runs. That is the whole security story for
    the install tier: there is no place for it to inject a command.

    Attributes:
        install_commands: ``{sys.platform prefix: argv}`` — the exact command
            for each OS GAIA can install on. A platform absent from this map
            has no automated install and is told so, with the manual URL.
        install_docs_url: Where to install by hand. Named whenever the
            automated path is unavailable or fails.
        auth_status_argv: Argv (after the binary) that reports auth state as
            JSON on stdout. Empty for a CLI that needs no authentication.
        auth_login_argv: Argv that starts the browser device flow. Must be
            runnable with **stdin closed** — an agent's child process has no
            terminal, so a login that insists on one cannot be driven and is
            not supported here.
        auth_login_timeout_s: How long to wait for the user to finish in the
            browser before giving up and saying so. Bounded on purpose: a
            device code expires, so waiting forever only hides that it did.
        required_scopes: The permissions the skills using this CLI actually
            need. Requested at login and verified after — never widened
            "just in case", because every extra scope is one the user's token
            carries for everything else it does too.
    """

    install_commands: Mapping[str, Sequence[str]] = field(default_factory=dict)
    install_docs_url: str = ""
    auth_status_argv: Sequence[str] = ()
    auth_login_argv: Sequence[str] = ()
    auth_login_timeout_s: float = 600.0
    required_scopes: frozenset[str] = frozenset()


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
        setup: How GAIA can install and authenticate this CLI for the user.
            ``None`` means it cannot, and the user is told to do it by hand
            rather than offered something that will not work.
    """

    binary: str
    summary: str
    install_hint: str
    subcommands: Mapping[str, Subcommand] = field(default_factory=dict)
    bare_flags: frozenset[str] = frozenset({"--version", "--help", "-h"})
    positional: Subcommand | None = None
    setup: BinarySetup | None = None

    def __post_init__(self) -> None:
        if bool(self.subcommands) == bool(self.positional is not None):
            raise ValueError(
                f"BinaryPolicy({self.binary!r}) must set exactly one of "
                "'subcommands' (a gh-shaped CLI) or 'positional' (a "
                "no-subcommand CLI like pytest), never both or neither."
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


BINARY_POLICIES: dict[str, BinaryPolicy] = {
    "gh": BinaryPolicy(
        binary="gh",
        summary=(
            "GitHub CLI — reads issues, pull requests, releases, and runs; "
            "comments, files, and labels only with your per-command approval."
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
            # `comment` only. `merge` lands code and `close` kills a
            # contribution — neither is triage, and both are irreversible in
            # ways a one-line prompt understates.
            "pr": _gh({"list", "view", "diff", "checks", "status"}, {"comment"}),
            "repo": _gh({"list", "view"}),
            "release": _gh({"list", "view"}),
            "run": _gh({"list", "view"}),
            # `delete` is absent: deleting a label strips it from every issue
            # that carries it, which no per-call prompt makes visible.
            "label": _gh({"list"}, {"create", "edit"}),
            "search": _gh({"issues", "prs", "repos", "code", "commits"}),
            # `status` only. `gh auth token` prints the credential — and so
            # does `gh auth status --show-token`, which is why the flag is
            # denied here rather than left to the action allowlist. Denying
            # the subcommand's write verbs but not this flag would leave the
            # credential reachable through the one auth action that IS allowed,
            # at the tier that runs without asking.
            "auth": Subcommand(
                actions=frozenset({"status"}),
                value_flags=_GH_COMMON_VALUE_FLAGS,
                denied_flags=frozenset({"-t", "--show-token"}),
                denied_flag_reasons={
                    "-t": "it prints the account's access token, which is the "
                    "credential itself rather than a fact about it. Use "
                    "'gh auth status' on its own to see who is signed in",
                    "--show-token": "it prints the account's access token, "
                    "which is the credential itself rather than a fact about "
                    "it. Use 'gh auth status' on its own to see who is signed in",
                },
            ),
            "api": _GH_API,
        },
        setup=BinarySetup(
            install_commands={
                # Non-interactive flags are required, not tidiness: an agent's
                # child has no terminal, so winget's agreement prompts would
                # block forever rather than ask anyone.
                "win32": (
                    "winget",
                    "install",
                    "--id",
                    "GitHub.cli",
                    "--exact",
                    "--source",
                    "winget",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ),
                "darwin": ("brew", "install", "gh"),
                # Linux is deliberately absent. gh is not in the default Debian
                # or Fedora repositories, so `apt install gh` installs nothing
                # on a stock box, and the real path adds GitHub's apt/dnf repo
                # — a root-level trust change that belongs to the user, not to
                # an agent acting for them. Linux users get the URL instead.
            },
            install_docs_url="https://cli.github.com",
            # --json makes gh report state as data. The text form is prose that
            # changes between releases; parsing it is how a logged-in user gets
            # told they are logged out.
            auth_status_argv=("auth", "status", "--json", "hosts"),
            # Verified to run with stdin closed: gh prints the one-time code and
            # the device URL to stderr, then polls until the user finishes in a
            # browser. --hostname is required non-interactively, and
            # --git-protocol keeps it from asking a question nobody can answer.
            auth_login_argv=(
                "auth",
                "login",
                "--web",
                "--hostname",
                "github.com",
                "--git-protocol",
                "https",
                "--scopes",
                "repo,read:org",
            ),
            # `repo` covers reading and commenting on issues and PRs, including
            # private repositories; `read:org` is what org-scoped reads need.
            # Not `workflow`, not `gist`, not `delete_repo` — nothing the triage
            # skill's own command table can reach. gh adds its own floor on top;
            # verification checks that these are present, never that nothing
            # else is, since the floor is gh's call and not a widening by GAIA.
            required_scopes=frozenset({"repo", "read:org"}),
        ),
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
                    # Report writers. This policy has no notion of "inside
                    # the project" to confine the destination to — deny
                    # outright rather than guess at containment.
                    "--junitxml",
                    "--result-log",
                    "--basetemp",
                    # Point pytest at a config/rootdir outside the checkout,
                    # changing what actually runs. Same "no containment
                    # notion" reasoning as the writers above.
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
            # Still a refusal — a half-loaded skill produces confident answers
            # from no data. What changed is that it is no longer a dead end:
            # the agent can install the CLI itself, so the message names that
            # instead of handing the user a command and walking away.
            remedy = (
                f"Ask GAIA to set up {policy.binary} and it will install it for "
                "you, after showing you the exact command and asking. "
                if policy.setup is not None
                else ""
            )
            raise SkillPermissionError(
                f"Skill '{skill_name}' needs the '{policy.binary}' command, which "
                f"is not on PATH. {policy.summary} {remedy}{policy.install_hint} "
                "The skill is refused rather than loaded without the tool it "
                "documents."
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


def _split_flag(token: str) -> tuple[str, str | None]:
    """Split one flag token into ``(name, attached value)``.

    Mirrors how Go's ``pflag`` — what ``gh`` uses — actually parses, including
    the attached short form. ``-XDELETE``, ``-X=DELETE``, and ``--method=DELETE``
    all carry their value in the token; missing that is how a method rule gets
    bypassed by deleting one space.
    """
    if token.startswith("--"):
        name, _, attached = token.partition("=")
        return name, attached if attached else None
    name, rest = token[:2], token[2:]
    return name, rest.lstrip("=") or None


def _flag_name(token: str) -> str:
    """The flag's name, without any value attached to the same token."""
    return _split_flag(token)[0]


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
        # A positional binary (pytest) has no write tier — its rule is entirely
        # about invocation shape, so every verdict is allow or refuse.
        error = _validate_positional_invocation(policy, tokens)
        return _ALLOWED if error is None else _refuse(error)

    allowed_subcommands = ", ".join(sorted(policy.subcommands))

    # Leading flags may only be the valueless ones (`gh --version`); anything
    # else before the subcommand could carry a value and shift the parse.
    index = 0
    while index < len(tokens) and tokens[index].startswith("-"):
        if _flag_name(tokens[index]) not in policy.bare_flags:
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
            f"{tiers}Anything that installs, opens a shell, or prints a "
            "credential is refused outright and cannot be approved."
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
    if not rule.free_form and rest and rest[0].startswith("-") and rest[0] != "-":
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
            if action is None:
                action = token
            continue

        name, inline = _split_flag(token)
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

        takes_value = name in rule.flag_values or name in rule.value_flags
        value = inline
        if takes_value and inline is None:
            value = rest[cursor] if cursor < len(rest) else None
            cursor += 1  # the value is never the action positional

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

    if not rule.free_form:
        if action is None:
            return _refuse(
                f"'{policy.binary} {subcommand}' needs an action: "
                f"{_spell_actions(rule)}."
            )
        if action.lower() in rule.confirm_actions:
            return InvocationDecision(
                CONFIRM,
                f"'{policy.binary} {subcommand} {action.lower()}' writes to "
                "GitHub, so it runs only after you approve this exact command.",
            )
        if action.lower() not in rule.actions:
            return _refuse(
                f"'{policy.binary} {subcommand} {action}' is not allowed. "
                f"Allowed {subcommand} actions: {_spell_actions(rule)}."
            )

    return _ALLOWED


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
    absolute path, a Windows drive letter, or a ``..`` path component. This is
    the only path check a ``path_operands`` binary gets: the shell tool's own
    path-traversal scan (``shell_tools.py``) skips every segment whose binary
    is skill-granted, on the assumption a granted CLI's operands are remote
    ids, not local paths — true for ``gh``, false for ``pytest``.
    """
    if token.startswith(("/", "\\")):
        return True
    if _UNSAFE_OPERAND_RE.match(token):
        return True
    return ".." in re.split(r"[\\/]", token)


def _validate_positional_invocation(
    policy: BinaryPolicy, tokens: Sequence[str]
) -> str | None:
    """``validate_invocation`` for a :attr:`BinaryPolicy.positional` binary.

    No subcommand step: every token is either a path operand or a flag, and
    flags are checked against an ALLOWLIST (``rule.allowed_flags`` /
    ``value_flags`` / ``flag_values`` / ``flag_value_prefixes``), not the
    subcommand path's denylist — see the "Flags are an ALLOWLIST here"
    comment on the ``pytest`` entry for why.
    """
    rule = policy.positional
    known_flags = (
        rule.allowed_flags
        | policy.bare_flags
        | frozenset(rule.flag_values)
        | frozenset(rule.value_flags)
        | frozenset(rule.flag_value_prefixes)
    )

    cursor = 0
    while cursor < len(tokens):
        token = tokens[cursor]
        cursor += 1

        if not token.startswith("-") or token == "-":
            if rule.path_operands and _unsafe_operand(token):
                return (
                    f"'{policy.binary} {token}' is not allowed: operand paths "
                    "must stay inside the project checkout — no leading '/', "
                    "drive letter, or '..' component."
                )
            continue

        name, inline = _split_flag(token)

        if name in rule.denied_flags:
            reason = rule.denied_flag_reasons.get(
                name, "it is outside this grant's read-only flag set"
            )
            return f"'{policy.binary} {name}' is not allowed: {reason}."

        if name in rule.allowed_flags or name in policy.bare_flags:
            if inline is not None:
                # A bundled/'='-attached form on a flag we only ever validated
                # as bare (`-xvs`, `--quiet=1`) could smuggle an unreviewed
                # flag or value past this allowlist unexamined.
                return (
                    f"'{policy.binary} {token}' is not allowed: {name} takes "
                    "no value — pass it on its own, e.g. "
                    f"'{policy.binary} {name} ...' not bundled or '='-attached."
                )
            continue

        takes_value = (
            name in rule.flag_values
            or name in rule.value_flags
            or name in rule.flag_value_prefixes
        )
        if not takes_value:
            return (
                f"'{policy.binary} {name}' is not allowed. This skill's grant "
                f"covers a fixed set of read-only flags: "
                f"{', '.join(sorted(known_flags))}."
            )

        value = inline
        if inline is None:
            value = tokens[cursor] if cursor < len(tokens) else None
            cursor += 1  # the value is never mistaken for a path operand

        if name in rule.flag_values:
            allowed = rule.flag_values[name]
            if value is None or value.lower() not in allowed:
                spelled = " ".join(filter(None, (name, value)))
                return (
                    f"'{policy.binary} {spelled}' is not allowed: {name} may "
                    f"only be {', '.join(sorted(allowed))}."
                )
        elif name in rule.flag_value_prefixes:
            prefixes = rule.flag_value_prefixes[name]
            if value is None or not any(
                value.lower().startswith(prefix) for prefix in prefixes
            ):
                spelled = " ".join(filter(None, (name, value)))
                return (
                    f"'{policy.binary} {spelled}' is not allowed: {name} only "
                    f"accepts a value starting with "
                    f"{', '.join(sorted(prefixes))!r}."
                )
        # else: a bare `value_flags` entry (e.g. -k, -m, --maxfail) — any
        # value is accepted, matching gh's `value_flags` semantics.

    return None
