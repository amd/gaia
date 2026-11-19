# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tools that get a skill's CLI installed and signed in, instead of giving up.

A skill that needs GitHub declares ``shell:execute:gh`` and GAIA gates every
``gh`` command it runs. That works right up until ``gh`` is not on the machine,
or is on it but logged out — at which point the agent's only move was to tell
the user to go and fix it themselves. This mixin removes that dead end: the
agent can find out exactly what is wrong, offer to install the CLI, and walk the
user through signing in.

Three tools, matching the three things that can be true:

* ``check_cli_setup`` — read-only, no prompt. Runs the CLI's own status command
  and reports one of five states. Always the first call: it is what tells the
  model which of the other two to reach for, and reaching for one in the wrong
  state costs the user an approval prompt for a no-op.
* ``install_cli`` — installs it, **after the user approves the exact command**.
* ``sign_in_cli`` — starts the browser sign-in and hands the user the one-time
  code, then confirms it worked.

**The model picks which CLI, never what runs.** Both mutating tools take a
*binary name* that must be a key in ``BINARY_POLICIES``; the command itself is
read from that table. There is no argument through which a command, a flag, or
a URL can be injected — the ``command`` argument exists only so the exact text
appears in the approval prompt, and a value that does not match the table's
refuses the call rather than running.

Why a tool rather than widening ``run_shell_command``: adding ``winget`` /
``brew`` / ``apt`` to the shell allowlist would let the model install *anything*
it could name, gated by a prompt whose fatigue is the known failure mode. A tool
that can install exactly the CLIs GAIA has policies for is a far smaller grant
than a package manager, and it is the same size no matter how many skills use it.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from gaia import program_lookup
from gaia.agents.base.call_risk import NEEDS_PERSON_ARG
from gaia.agents.base.denied_effects import UNATTENDED_REFUSAL
from gaia.agents.base.file_snapshots import note_untracked
from gaia.agents.base.person_approval import a_person_can_answer, preapproved
from gaia.logger import get_logger
from gaia.skills.binaries import BINARY_POLICIES, BinaryPolicy
from gaia.skills.binary_setup import (
    ENV_TOKEN,
    MISSING,
    SetupError,
    SetupStatus,
    detect,
    install_plan,
    run_install,
    start_device_login,
)

logger = get_logger(__name__)

#: Ceiling on one status read, matching ``binary_setup._run``'s own.
_DETECT_TIMEOUT_S = 30.0

#: Ceiling on an install. Package managers are slow and a first ``winget`` run
#: refreshes its source index before it does anything.
_INSTALL_TIMEOUT_S = 600.0

#: Ceiling on the browser sign-in window itself, mirroring
#: ``BinarySetup.auth_login_timeout_s`` for the budget arithmetic below.
_SIGN_IN_WINDOW_S = 600.0

#: Longest the CLI may take to print its device code (``binary_setup._CODE_WAIT_S``).
_CODE_WAIT_S = 45.0

# Every gated tool below must finish before the agent's tool watchdog gives up
# on it. That watchdog runs the body in a daemon thread it cannot kill, so a
# body it abandons keeps running — and for the sign-in that means a CLI child
# left polling GitHub with nobody to reap it. So each ceiling is the worst-case
# body (every bounded step at its own limit) plus slack, never a round number
# chosen to look generous.

#: ``detect`` + ``run_install`` + ``detect``, plus slack.
_INSTALL_TOOL_TIMEOUT_S = _INSTALL_TIMEOUT_S + 2 * _DETECT_TIMEOUT_S + 120.0

#: ``detect`` + code capture + the sign-in window + ``detect`` + reaping the
#: child, plus slack.
_SIGN_IN_TOOL_TIMEOUT_S = (
    _SIGN_IN_WINDOW_S + _CODE_WAIT_S + 2 * _DETECT_TIMEOUT_S + 10.0 + 120.0
)


def _resolve_policy(binary: str) -> tuple[Optional[BinaryPolicy], Optional[Dict]]:
    """The policy for *binary*, or the error to return to the model.

    Deny by default, the same rule the invocation table uses: a CLI with no
    entry cannot be gated, so it is not one GAIA offers to install either.
    """
    name = (binary or "").strip().lower()
    policy = BINARY_POLICIES.get(name)
    if policy is None:
        return None, {
            "status": "error",
            "error": (
                f"GAIA has no setup policy for {binary!r}, so it will not "
                "install or sign in to it. Tools GAIA can set up: "
                f"{', '.join(sorted(BINARY_POLICIES))}."
            ),
            "has_errors": True,
        }
    return policy, None


def _status_payload(status: SetupStatus, policy: BinaryPolicy) -> Dict[str, Any]:
    """A status plus the exact commands that would act on it.

    The commands are returned so the model can echo one back into
    ``install_cli`` / ``sign_in_cli`` and have it show up verbatim in the
    approval prompt. They are advisory: the table decides what runs.
    """
    payload = status.as_dict()
    payload["status"] = "success"
    plan = install_plan(policy) if policy.setup is not None else None
    payload["install_command"] = plan.command if plan is not None else ""
    payload["sign_in_command"] = _sign_in_command(policy)
    payload["can_install_here"] = plan is not None
    if status.needs_install and plan is None:
        docs = policy.setup.install_docs_url if policy.setup else ""
        payload["detail"] = (
            f"{status.detail} GAIA has no packaged install for this operating "
            f"system, so it cannot install it for you"
            + (f" — install it from {docs}." if docs else ".")
        )
    return payload


def _sign_in_command(policy: BinaryPolicy) -> str:
    """The sign-in command as one line, for the approval prompt."""
    setup = policy.setup
    if setup is None or not setup.auth_login_argv:
        return ""
    return " ".join([policy.binary, *setup.auth_login_argv])


def _install_command(policy: BinaryPolicy) -> str:
    """The install command as one line, or "" where GAIA has none for this OS."""
    if policy.setup is None:
        return ""
    plan = install_plan(policy)
    return plan.command if plan is not None else ""


def _command_mismatch(expected: str, supplied: str) -> bool:
    """True when the echoed command is not the one the table would run.

    The approval prompt shows the arguments the model passed, so the command it
    passes has to be the command that runs or the prompt is a lie. Rather than
    trusting the echo, the table's own command is executed and a mismatch is
    refused — which makes the argument a display field that cannot be turned
    into an injection point.
    """
    return " ".join((supplied or "").split()) != " ".join(expected.split())


class _PersonMustApprove:
    """Pre-flight for a setup tool: a person approves it, or it does not run.

    Pre-approval (Full access, ``GAIA_AUTO_APPROVE_TOOLS``, ``gaia -p
    --dangerously-skip-permissions``) answers tool prompts on the user's behalf.
    Installing software, or signing in to an account, is not theirs to answer:
    where a person can be asked they are, and where nobody can it is refused
    with the command to run by hand.
    """

    def __init__(
        self,
        host: Any,
        action: str,
        command_of: Callable[[BinaryPolicy], str],
        refuse_first: Optional[
            Callable[[BinaryPolicy], Optional[Dict[str, Any]]]
        ] = None,
    ):
        self._host = host
        self._action = action
        self._command_of = command_of
        self._refuse_first = refuse_first

    def _preapproved(self) -> bool:
        return preapproved(getattr(self._host, "console", None))

    def __call__(self, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        policy, unknown = _resolve_policy(str(args.get("binary") or ""))
        if unknown is not None:
            return unknown
        # Before the prompt: nobody should approve a run that will be refused.
        repeat = self._refuse_first(policy) if self._refuse_first else None
        if repeat is not None:
            return repeat
        console = getattr(self._host, "console", None)
        if not self._preapproved() or a_person_can_answer(console):
            return None
        # The table's own command, never the text the model passed in.
        command = self._command_of(policy)
        by_hand = f" To do it yourself, run: {command}" if command else ""
        return {
            "status": "error",
            "error": (
                f"Not run: {self._action} '{policy.binary}' needs a person's "
                f"approval, and nobody can be asked in this run.{by_hand}"
            ),
            "has_errors": True,
            "hint": (
                "Tell the user what is missing and give them that command. Do "
                "not reach the same result another way."
            ),
            UNATTENDED_REFUSAL: True,
        }

    def prompt_args_of(
        self, args: Dict[str, Any]  # pylint: disable=unused-argument
    ) -> Dict[str, Any]:
        """Mark the prompt so pre-approval does not answer it."""
        return {NEEDS_PERSON_ARG: True} if self._preapproved() else {}


class CliSetupToolsMixin:
    """Tools for installing and signing in to the CLIs skills depend on.

    Composed by name as ``cli_setup`` (see ``KNOWN_TOOLS``). Registers nothing
    on its own — call :meth:`register_cli_setup_tools` from the agent's tool
    registration, next to ``register_shell_tools``.

    Deliberately does **not** implement ``policy_refusal_for_call`` or
    ``skill_grant_covers_call``. Both are duck-typed single names resolved
    through the MRO, and ``ShellToolsMixin`` already owns them on every agent
    composing both — it precedes this mixin in ``ChatAgent``'s MRO, so a
    same-named method here would never be called at all. Silently dead code in
    a permission path is worse than none.
    """

    def _installs_left_unreachable(self) -> set:
        """Programs installed during this request that GAIA still cannot find."""
        # The agent makes a new denial ledger for each request.
        request = getattr(self, "_denied_effects", None)
        kept = getattr(self, "_unreachable_installs", None)
        if kept is None or kept[0] is not request:
            kept = (request, set())
            self._unreachable_installs = kept
        return kept[1]

    def _repeat_install_refusal(self, policy: BinaryPolicy) -> Optional[Dict[str, Any]]:
        """The block for installing again what an install here left unfound."""
        if policy.binary not in self._installs_left_unreachable():
            return None
        return {
            "status": "error",
            "error": (
                f"Not run: the install of '{policy.binary}' already finished "
                "during this request and GAIA still cannot find it, so running "
                "it again would change nothing. Tell the user that, and that "
                f"'{policy.binary} --version' in a new terminal window shows "
                "whether it is there."
            ),
            "has_errors": True,
            "installed": False,
        }

    def register_cli_setup_tools(self) -> None:
        """Register the CLI setup tools."""
        from gaia.agents.base.tools import tool

        @tool(atomic=True)
        def check_cli_setup(binary: str) -> Dict[str, Any]:
            """Check whether a skill's CLI is installed and signed in.

            Call this BEFORE install_cli, sign_in_cli, or telling the user
            anything is missing. State is ready, missing (not installed),
            unauthenticated, insufficient_scopes (signed in, lacking a
            permission), or env_token (signed in via an env var, which
            signing in will NOT fix). Read-only.

            Args:
                binary: The CLI to check, e.g. 'gh'.
            """
            policy, error = _resolve_policy(binary)
            if error is not None:
                return error
            try:
                status = detect(policy)
            except SetupError as exc:
                return {"status": "error", "error": str(exc), "has_errors": True}
            logger.info("CLI setup check: %s is %s", policy.binary, status.state)
            return _status_payload(status, policy)

        @tool(
            atomic=True,
            timeout=_INSTALL_TOOL_TIMEOUT_S,
            preflight=_PersonMustApprove(
                self, "installing", _install_command, self._repeat_install_refusal
            ),
        )
        def install_cli(binary: str, command: str) -> Dict[str, Any]:
            """Install a skill's CLI using this machine's package manager.

            Only call this after check_cli_setup reports state 'missing'. The
            user must approve the exact command before anything runs. Copy
            check_cli_setup's install_command verbatim into command: it is
            shown in the approval prompt, and a mismatch is refused.

            Args:
                binary: The CLI to install, e.g. 'gh'.
                command: The exact install_command from check_cli_setup.
            """
            policy, error = _resolve_policy(binary)
            if error is not None:
                return error

            try:
                plan = install_plan(policy)
            except SetupError as exc:
                return {"status": "error", "error": str(exc), "has_errors": True}
            if plan is None:
                docs = policy.setup.install_docs_url if policy.setup else ""
                return {
                    "status": "error",
                    "error": (
                        f"GAIA has no packaged install for '{policy.binary}' on "
                        "this operating system. Ask the user to install it from "
                        f"{docs or policy.install_hint}."
                    ),
                    "has_errors": True,
                }

            # Before anything state-dependent: what the user approved has to be
            # what would run. Checking this after a branch that can return early
            # would leave the integrity check reachable only on some machines,
            # which is the same as not having one.
            if _command_mismatch(plan.command, command):
                return {
                    "status": "error",
                    "error": (
                        "Refusing to install: the command shown to the user for "
                        f"approval was {command!r}, which is not the command "
                        f"GAIA would run ({plan.command!r}). Call "
                        "check_cli_setup and copy 'install_command' exactly."
                    ),
                    "has_errors": True,
                }

            try:
                status = detect(policy)
            except SetupError as exc:
                return {"status": "error", "error": str(exc), "has_errors": True}
            # Approval was for installing something absent. Installing over a
            # working copy is a different action than the one described.
            if status.state != MISSING:
                return {
                    **_status_payload(status, policy),
                    "installed": False,
                    "detail": (
                        f"'{policy.binary}' is already installed — nothing to "
                        f"do. {status.detail}"
                    ),
                }

            repeat = self._repeat_install_refusal(policy)
            if repeat is not None:
                return repeat

            logger.info("Installing '%s' with: %s", policy.binary, plan.command)
            note_untracked("install_cli", str(plan.command))
            try:
                output = run_install(plan, timeout=_INSTALL_TIMEOUT_S)
            except SetupError as exc:
                return {"status": "error", "error": str(exc), "has_errors": True}

            # The installer added its folder to PATH for new processes only.
            program_lookup.refresh_path(after_install=True)
            try:
                after = detect(policy)
            except SetupError as exc:
                return {"status": "error", "error": str(exc), "has_errors": True}
            if after.state == MISSING:
                # The package manager said it worked and the CLI is still not
                # there. Say that, rather than reporting a success the next
                # command will contradict.
                self._installs_left_unreachable().add(policy.binary)
                return {
                    "status": "error",
                    "error": (
                        f"'{plan.command}' reported success, but GAIA still "
                        f"cannot find '{policy.binary}'. Do not install it "
                        "again. Tell the user the "
                        "install finished but the program is not reachable, "
                        f"and that '{policy.binary} --version' in a new "
                        "terminal window shows whether it is there."
                    ),
                    "has_errors": True,
                    "command_output": output,
                }
            return {
                **_status_payload(after, policy),
                "installed": True,
                "detail": f"Installed '{policy.binary}'. {after.detail}",
                "command_output": output,
            }

        @tool(
            atomic=True,
            timeout=_SIGN_IN_TOOL_TIMEOUT_S,
            preflight=_PersonMustApprove(self, "signing in to", _sign_in_command),
        )
        def sign_in_cli(binary: str, command: str) -> Dict[str, Any]:
            """Sign a CLI in to the user's account through their browser.

            Only call this after check_cli_setup reports 'unauthenticated' or
            'insufficient_scopes'; it is NOT the fix for 'env_token'. GAIA
            shows the user a one-time code to enter in their browser and
            waits. The user must approve the command first. Copy
            check_cli_setup's sign_in_command verbatim into command; a
            mismatch is refused.

            Args:
                binary: The CLI to sign in, e.g. 'gh'.
                command: The exact sign_in_command from check_cli_setup.
            """
            policy, error = _resolve_policy(binary)
            if error is not None:
                return error

            expected = _sign_in_command(policy)
            if not expected:
                return {
                    "status": "error",
                    "error": (
                        f"GAIA cannot sign in to '{policy.binary}' for you: it "
                        f"has no sign-in command. {policy.install_hint}"
                    ),
                    "has_errors": True,
                }
            # Integrity of the approved command first, state second — see the
            # matching note in install_cli.
            if _command_mismatch(expected, command):
                return {
                    "status": "error",
                    "error": (
                        "Refusing to sign in: the command shown to the user for "
                        f"approval was {command!r}, which is not the command "
                        f"GAIA would run ({expected!r}). Call check_cli_setup "
                        "and copy 'sign_in_command' exactly."
                    ),
                    "has_errors": True,
                }

            try:
                status = detect(policy)
            except SetupError as exc:
                return {"status": "error", "error": str(exc), "has_errors": True}

            if status.state == MISSING:
                return {
                    "status": "error",
                    "error": (
                        f"'{policy.binary}' is not installed, so there is "
                        "nothing to sign in. Install it first."
                    ),
                    "has_errors": True,
                }
            # The one state a sign-in cannot fix, and the one most likely to be
            # mistaken for a sign-in problem: the CLI works, but its credential
            # comes from the environment and it will refuse to store another.
            if status.state == ENV_TOKEN:
                return {
                    "status": "error",
                    "error": status.detail,
                    "has_errors": True,
                    **status.as_dict(),
                }
            if status.ready:
                return {
                    **_status_payload(status, policy),
                    "signed_in": True,
                    "detail": f"Already signed in — nothing to do. {status.detail}",
                }

            try:
                login = start_device_login(policy)
            except SetupError as exc:
                return {"status": "error", "error": str(exc), "has_errors": True}

            try:
                return self._hand_off_sign_in(policy, login)
            finally:
                # Whatever happened — approval denied, timeout, or the agent's
                # own watchdog giving up on this tool — the child that is
                # polling GitHub gets reaped here rather than left behind.
                login.cancel()

    def _hand_off_sign_in(self, policy: BinaryPolicy, login) -> Dict[str, Any]:
        """Give the user their one-time code, wait for them, verify it worked.

        The code cannot be typed by the agent — that is what a one-time code is
        for — so this is a handoff, not automation.

        The wait is on the **CLI's own exit**, not on a second approval prompt.
        Consent was taken before the flow started; asking again would be asking
        the user to *report* their progress, and every confirmation channel is
        the wrong shape for that. The Agent UI expires a prompt after
        ``TOOL_CONFIRM_TIMEOUT_SECONDS`` — a fixed bound, against a flow that
        needs a browser, a password and 2FA — and the terminal's is a bare ``input()``
        that never returns if the user walks away, stranding the polling child
        past the tool watchdog that cannot kill this thread. Watching the child
        avoids both, and is the better signal anyway: it observes GitHub
        actually completing the grant rather than the user saying it did.
        """
        # Not logged — the code is a live credential for as long as it lasts.
        self.console.print_warning(
            f"To finish signing in to {policy.binary}: open {login.url} and "
            f"enter the one-time code {login.code}. Waiting for you to "
            "authorise it in your browser…"
        )

        try:
            login.wait()
        except SetupError as exc:
            return {"status": "error", "error": str(exc), "has_errors": True}

        # Trust the check, not the exit code: a sign-in that "succeeded" without
        # granting the scopes the skill needs is still a skill that cannot run,
        # and finding that out now beats finding out on the first command.
        try:
            after = detect(policy)
        except SetupError as exc:
            return {"status": "error", "error": str(exc), "has_errors": True}
        if not after.ready:
            return {
                "status": "error",
                "error": (
                    f"Signing in to '{policy.binary}' finished, but it is still "
                    f"not usable: {after.detail}"
                ),
                "has_errors": True,
                **after.as_dict(),
            }
        logger.info("'%s' sign-in complete for %s", policy.binary, after.account)
        return {
            **_status_payload(after, policy),
            "signed_in": True,
            "detail": f"Signed in to '{policy.binary}'. {after.detail}",
        }
