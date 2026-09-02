# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Getting a skill's CLI onto the machine — install it, then log it in.

:mod:`gaia.skills.binaries` answers "may this command run?". This module answers
the question that comes *before* it: the command cannot run at all, because the
CLI is absent or logged out. Telling the user to go fix that by hand is a dead
end an agent is perfectly able to walk itself, once someone says yes.

Three moving parts, in the order they are used:

* :func:`detect` — which of five states the CLI is actually in. The states are
  kept apart because each needs a *different* remedy, and conflating two of
  them produces confident advice that cannot work: telling someone to run
  ``gh auth login`` when their token comes from ``$GH_TOKEN`` is telling them
  to run a command gh will refuse.
* :func:`install_plan` — the exact argv for this platform, or an honest "not on
  this OS, here is the URL". Never a shell string, never interpolated.
* :func:`start_device_login` / :class:`DeviceLogin` — drives the browser device
  flow far enough to hand the user their one-time code, then waits for them.

**What this module does not do is decide.** Installing software and logging in
to an account are the user's calls, and the confirmation gate that asks them
lives in ``Agent._execute_tool``, reached through
:mod:`gaia.agents.tools.cli_setup_tools`. Nothing here prompts, and nothing here
should ever be called without having asked first.

Adding self-setup for a second CLI is a :class:`~gaia.skills.binaries.BinarySetup`
entry in the policy table and nothing else — no new branch below. That is the
same acceptance test the invocation table sets for itself, and it is why the
login driver talks about "the one-time code" rather than about gh.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - argv comes from the policy table, never a caller
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence

from gaia.logger import get_logger
from gaia.skills.binaries import BinaryPolicy, BinarySetup

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# The five states
# ---------------------------------------------------------------------------
#
# Strings rather than an enum so a state survives a JSON round-trip to a UI (and
# into a tool result the model reads) unchanged.

#: Not on PATH. Remedy: install it.
MISSING = "missing"
#: Installed, no credentials at all. Remedy: log in.
UNAUTHENTICATED = "unauthenticated"
#: Installed and authenticated, but from an environment variable rather than the
#: CLI's own credential store. Remedy: NOT a login — see :attr:`SetupStatus.detail`.
ENV_TOKEN = "env_token"
#: Authenticated, but the token is missing a permission the skill needs.
#: Remedy: log in again, requesting the scopes.
INSUFFICIENT_SCOPES = "insufficient_scopes"
#: Installed, authenticated, scopes present. Nothing to do.
READY = "ready"

#: Every state whose remedy is a login. ``ENV_TOKEN`` is deliberately absent:
#: the CLI already works, and logging in is not what fixes the shortfall.
_LOGIN_FIXES = frozenset({UNAUTHENTICATED, INSUFFICIENT_SCOPES})


#: Token shapes to strip from anything captured off a setup subprocess before it
#: is logged or returned to the model. Belt and braces — none of the commands in
#: the policy table print a credential (``gh auth token`` is refused outright,
#: and ``auth status`` masks by default) — but "no command we run prints one" is
#: a property of today's table, and this is the one code path where a leak would
#: be both silent and permanent, because the model's context is the transcript.
_SECRET_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b"
)

#: gh prints ``! First copy your one-time code: ABCD-1234`` to stderr. Matched
#: loosely on the label so a wording change costs a clearer error rather than a
#: silent hang on a code we captured but did not recognise.
_DEVICE_CODE_RE = re.compile(r"one-time code:\s*([A-Za-z0-9]{4,8}-[A-Za-z0-9]{4,8})")

#: The URL on the line that follows it.
_DEVICE_URL_RE = re.compile(r"(https://[^\s'\"]+)")

#: Everything after a one-time-code label, whatever shape the code itself takes.
#: Used where the code could not be parsed: the label is still recognisable, so
#: the value beside it is redacted on the assumption it IS the code.
_CODE_LABEL_RE = re.compile(r"(one-time code:?)(.*)", re.IGNORECASE)

#: How long to wait for the CLI to print its device code before concluding the
#: flow is not going to start. Generous for a cold process on a slow box, short
#: enough that a user is not left watching a spinner over a dead subprocess.
_CODE_WAIT_S = 45.0


def scrub_secrets(text: str) -> str:
    """*text* with anything shaped like an access token replaced.

    Applied to every byte this module captures from a subprocess, on the way to
    both the log and the model.
    """
    return _SECRET_RE.sub("[redacted]", text)


def _redact_code_lines(text: str) -> str:
    """*text* with whatever follows a one-time-code label removed."""
    return _CODE_LABEL_RE.sub(r"\1 [one-time code]", text)


@dataclass(frozen=True)
class SetupStatus:
    """What state a CLI is in, and what would move it forward.

    Attributes:
        binary: The CLI this describes.
        state: One of the five module-level constants.
        detail: One human sentence naming what is wrong and what fixes it.
            Written for the user, and read by the model to decide what to
            offer — so it must never say "install it" when the remedy is a
            login, or the model will offer the wrong one.
        account: The logged-in account, when there is one.
        scopes: Permissions the current credential carries.
        missing_scopes: Required permissions it does not.
        token_source: Where the credential came from (``keyring``, ``GH_TOKEN``).
    """

    binary: str
    state: str
    detail: str
    account: str = ""
    scopes: frozenset[str] = frozenset()
    missing_scopes: frozenset[str] = frozenset()
    token_source: str = ""

    @property
    def ready(self) -> bool:
        """True when the CLI can be used as-is."""
        return self.state == READY

    @property
    def needs_install(self) -> bool:
        """True when the remedy is installing the CLI."""
        return self.state == MISSING

    @property
    def needs_login(self) -> bool:
        """True when the remedy is a browser login.

        False for :data:`ENV_TOKEN` — that credential works, and a login would
        be refused by the CLI rather than fix anything.
        """
        return self.state in _LOGIN_FIXES

    def as_dict(self) -> dict:
        """The tool-result view. Sorted lists so the shape is stable."""
        return {
            "binary": self.binary,
            "state": self.state,
            "detail": self.detail,
            "account": self.account,
            "scopes": sorted(self.scopes),
            "missing_scopes": sorted(self.missing_scopes),
            "token_source": self.token_source,
        }


class SetupError(RuntimeError):
    """A setup step failed in a way the caller must surface, not paper over."""


# ---------------------------------------------------------------------------
# Running a setup subprocess
# ---------------------------------------------------------------------------


def _run(
    argv: Sequence[str], *, timeout: float, env: Optional[Mapping[str, str]] = None
) -> subprocess.CompletedProcess:
    """Run *argv* with no shell and no stdin, returning the completed process.

    ``shell=False`` always — argv comes from the policy table, but a setup
    command's whole job is to mutate the system, so it is the last place to
    hand a string to a shell. ``stdin`` is closed for the same reason
    ``run_shell_command`` closes it: a child that asks a question here blocks on
    a pipe no human is holding, and hangs until the timeout instead of failing.
    """
    return subprocess.run(  # nosec B603 - fixed argv from BINARY_POLICIES, shell=False
        list(argv),
        capture_output=True,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=dict(env) if env is not None else None,
    )


def _require_setup(policy: BinaryPolicy) -> BinarySetup:
    if policy.setup is None:
        raise SetupError(
            f"GAIA cannot set up '{policy.binary}' for you: it has no setup "
            f"entry in BINARY_POLICIES. {policy.install_hint}"
        )
    return policy.setup


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect(
    policy: BinaryPolicy,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess] = _run,
) -> SetupStatus:
    """Which of the five states *policy*'s CLI is in, right now.

    Read-only and side-effect free: it runs the CLI's own status command and
    nothing else, so it is safe to call unprompted and is what every other
    entry point here checks first.

    Raises:
        SetupError: the status command could not be run or did not return the
            JSON this policy expects — usually a CLI too old to have the flag.
            Loud on purpose: a status read that failed is not a logged-out user,
            and reporting it as one sends them to fix the wrong thing.
    """
    if which(policy.binary) is None:
        return SetupStatus(
            binary=policy.binary,
            state=MISSING,
            detail=(
                f"'{policy.binary}' is not installed on this machine "
                f"(nothing by that name on PATH). {policy.summary}"
            ),
        )

    setup = policy.setup
    if setup is None or not setup.auth_status_argv:
        return SetupStatus(
            binary=policy.binary,
            state=READY,
            detail=f"'{policy.binary}' is installed and needs no sign-in.",
        )

    argv = [policy.binary, *setup.auth_status_argv]
    try:
        result = runner(argv, timeout=30.0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError(
            f"Could not read '{policy.binary}' sign-in state: running "
            f"'{' '.join(argv)}' failed with {exc}. Run that command yourself "
            "to see what it says."
        ) from exc

    return _classify_auth_status(policy, setup, result)


def _classify_auth_status(
    policy: BinaryPolicy, setup: BinarySetup, result: subprocess.CompletedProcess
) -> SetupStatus:
    """Turn the status command's JSON into a :class:`SetupStatus`."""
    stdout = (result.stdout or "").strip()
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        # Not "logged out" — we did not learn anything. Say which, or the user
        # is sent to re-authenticate a session that was fine all along.
        stderr = scrub_secrets((result.stderr or "").strip())
        raise SetupError(
            f"Could not read '{policy.binary}' sign-in state: "
            f"'{' '.join([policy.binary, *setup.auth_status_argv])}' returned "
            f"output GAIA could not parse ({exc}). This usually means the "
            f"installed '{policy.binary}' is too old for the flag. "
            f"Upgrade it, then try again." + (f" It said: {stderr}" if stderr else "")
        ) from exc

    accounts = [
        account
        for host_accounts in (payload.get("hosts") or {}).values()
        for account in (host_accounts or [])
        if isinstance(account, dict)
    ]
    if not accounts:
        return SetupStatus(
            binary=policy.binary,
            state=UNAUTHENTICATED,
            detail=(
                f"'{policy.binary}' is installed but not signed in to any "
                "account, so every command it runs will be rejected."
            ),
        )

    # The active account is the one commands will actually use; a machine can
    # carry several. Judging the wrong one reports scopes that are not in play.
    account = next((a for a in accounts if a.get("active")), accounts[0])
    login = str(account.get("login") or "")
    token_source = str(account.get("tokenSource") or "")
    scopes = frozenset(
        part.strip()
        for part in str(account.get("scopes") or "").split(",")
        if part.strip()
    )

    if str(account.get("state") or "") != "success":
        return SetupStatus(
            binary=policy.binary,
            state=UNAUTHENTICATED,
            detail=(
                f"'{policy.binary}' has credentials for {login or 'an account'} "
                "but they are not working — the token was most likely revoked "
                "or has expired. Signing in again replaces it."
            ),
            account=login,
            token_source=token_source,
        )

    if _is_env_token(token_source):
        return SetupStatus(
            binary=policy.binary,
            state=ENV_TOKEN,
            detail=(
                f"'{policy.binary}' is signed in as {login or 'an account'} using "
                f"the {token_source} environment variable. That works, and GAIA "
                f"will not change it: while {token_source} is set, "
                f"'{policy.binary}' refuses to store its own credentials, so "
                "signing in would fail. To switch to a browser sign-in instead, "
                f"clear {token_source} from the environment and ask again."
            ),
            account=login,
            scopes=scopes,
            token_source=token_source,
        )

    missing = frozenset(setup.required_scopes) - scopes
    if missing:
        return SetupStatus(
            binary=policy.binary,
            state=INSUFFICIENT_SCOPES,
            detail=(
                f"'{policy.binary}' is signed in as {login or 'an account'}, but "
                "the account is missing the permission"
                f"{'s' if len(missing) > 1 else ''} "
                f"{', '.join(sorted(missing))}, which this skill needs. Signing "
                "in again asks GitHub for them."
            ),
            account=login,
            scopes=scopes,
            missing_scopes=missing,
            token_source=token_source,
        )

    return SetupStatus(
        binary=policy.binary,
        state=READY,
        detail=(
            f"'{policy.binary}' is installed and signed in as "
            f"{login or 'an account'}. Nothing to set up."
        ),
        account=login,
        scopes=scopes,
        token_source=token_source,
    )


def _is_env_token(token_source: str) -> bool:
    """True when the credential came from the environment, not a credential store.

    CLIs name the variable itself (``GH_TOKEN``) and their own stores in lower
    case (``keyring``, ``oauth_token``), so the shape is the test — an
    allowlist of variable names would go stale the first time one is added.
    """
    source = token_source.strip()
    return bool(source) and source.isupper()


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallPlan:
    """The exact command that would install a CLI, ready to show a human.

    Attributes:
        binary: The CLI it installs.
        argv: The command, already resolved for this platform.
        docs_url: Where to install by hand instead.
    """

    binary: str
    argv: tuple[str, ...]
    docs_url: str

    @property
    def command(self) -> str:
        """The command as one line — what the confirmation prompt shows.

        Display only. The install runs :attr:`argv`, so what the user approves
        and what executes cannot drift into different things via quoting.
        """
        return " ".join(self.argv)


def install_plan(
    policy: BinaryPolicy, *, platform: str = sys.platform
) -> Optional[InstallPlan]:
    """How this platform installs *policy*'s CLI, or ``None`` if it cannot.

    ``None`` is a real answer, not a failure: on a platform with no packaged
    install GAIA says so and hands over the URL, rather than guessing at a
    package manager and failing in a way that reads like the CLI is broken.
    """
    setup = _require_setup(policy)
    for key, argv in setup.install_commands.items():
        if platform.startswith(key):
            return InstallPlan(
                binary=policy.binary,
                argv=tuple(argv),
                docs_url=setup.install_docs_url,
            )
    return None


def run_install(plan: InstallPlan, *, timeout: float = 600.0) -> str:
    """Run an approved install, returning its combined output.

    Callers must have obtained the user's confirmation *before* calling this;
    nothing here asks. See :mod:`gaia.agents.tools.cli_setup_tools`.

    Raises:
        SetupError: the package manager is absent, or the install failed. The
            message carries the manual URL so a failure is still actionable.
    """
    try:
        result = _run(plan.argv, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SetupError(
            f"Installing '{plan.binary}' did not finish within {timeout:g}s. It "
            "may still be running in the background — check with "
            f"'{plan.binary} --version', or install by hand from {plan.docs_url}."
        ) from exc
    except OSError as exc:
        # Covers the missing package manager (FileNotFoundError) and the ones
        # that are present but unusable (PermissionError, ENOEXEC); all three
        # need the same manual fallback rather than a bare traceback.
        raise SetupError(
            f"Cannot install '{plan.binary}': could not run '{plan.argv[0]}' "
            f"on this machine ({exc}). Install {plan.binary} by hand from "
            f"{plan.docs_url}."
        ) from exc

    output = scrub_secrets(f"{result.stdout or ''}\n{result.stderr or ''}".strip())
    if result.returncode != 0:
        raise SetupError(
            f"Installing '{plan.binary}' failed (exit {result.returncode}) "
            f"running '{plan.command}'. Install by hand from {plan.docs_url}. "
            f"It said: {output or '(no output)'}"
        )
    return output


# ---------------------------------------------------------------------------
# Browser device login
# ---------------------------------------------------------------------------


@dataclass
class DeviceLogin:
    """A browser sign-in in flight, waiting on the person at the keyboard.

    The CLI has printed a one-time code and a URL, and is now polling GitHub
    until someone enters that code in a browser. GAIA cannot type it for them —
    that is the entire point of the code — so this object exists to carry the
    handoff: show :attr:`code` and :attr:`url`, then :meth:`wait`.

    :attr:`code` is shown to the user and never logged. Anyone holding it can
    complete this sign-in, so it is treated as the short-lived credential it is.
    """

    binary: str
    process: subprocess.Popen
    code: str
    url: str
    timeout_s: float
    _output: list = field(default_factory=list)
    _reader: Optional[threading.Thread] = None

    def wait(self) -> str:
        """Block until the CLI finishes the flow, returning its output.

        Raises:
            SetupError: the user did not finish before the deadline, or the CLI
                exited non-zero. Both kill the child rather than leave it
                polling, and both say what to run by hand.
        """
        try:
            self.process.wait(timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            self.cancel()
            raise SetupError(
                f"Signing in to '{self.binary}' timed out after "
                f"{self.timeout_s:g}s with no completed browser sign-in, and "
                "the one-time code has expired. Nothing was changed. To do it "
                f"by hand, run: {self.binary} auth login --web"
            ) from None

        if self._reader is not None:
            self._reader.join(timeout=5.0)
        output = scrub_secrets("".join(self._output).strip())
        if self.process.returncode != 0:
            raise SetupError(
                f"Signing in to '{self.binary}' failed (exit "
                f"{self.process.returncode}). To do it by hand, run: "
                f"{self.binary} auth login --web. It said: "
                f"{_without_code(output, self.code) or '(no output)'}"
            )
        return _without_code(output, self.code)

    def cancel(self) -> None:
        """Stop the flow and reap the child. Safe to call more than once."""
        _reap(self.process, self.binary)
        if self._reader is not None:
            self._reader.join(timeout=5.0)


def _reap(process: subprocess.Popen, binary: str) -> None:
    """Kill *process* if it is still running, and close the pipe we hold.

    Closing matters as much as killing: the reader thread is blocked on that
    pipe, and a killed child whose pipe stays open leaves the thread parked for
    the life of the process.
    """
    if process.poll() is None:
        process.kill()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            logger.warning("'%s' sign-in process did not exit after kill", binary)
    if process.stdout is not None and not process.stdout.closed:
        try:
            process.stdout.close()
        except OSError as exc:
            logger.warning("Closing '%s' sign-in output failed: %s", binary, exc)


def _without_code(text: str, code: str) -> str:
    """*text* with the one-time code removed.

    The code goes to the user through the confirmation prompt and stops there.
    Returning it in a tool result would put it in the model's context, and from
    there into the transcript — a live credential written down permanently.
    """
    return text.replace(code, "[one-time code]") if code else text


def start_device_login(
    policy: BinaryPolicy,
    *,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    code_wait_s: float = _CODE_WAIT_S,
) -> DeviceLogin:
    """Start the browser sign-in and capture the one-time code it prints.

    Returns once the CLI has emitted a code and URL — the point at which the
    user has something to act on. The caller shows them, then calls
    :meth:`DeviceLogin.wait`.

    Callers must have obtained the user's confirmation before calling this.

    Raises:
        SetupError: the CLI exited, or printed no code within *code_wait_s*.
            A login flow that never produced a code cannot be handed off, so
            this fails with the manual command rather than waiting on a process
            that is not going to say anything.
    """
    setup = _require_setup(policy)
    if not setup.auth_login_argv:
        raise SetupError(
            f"GAIA cannot sign in to '{policy.binary}' for you: it has no "
            f"sign-in command in BINARY_POLICIES. {policy.install_hint}"
        )

    argv = [policy.binary, *setup.auth_login_argv]
    logger.info("Starting '%s' browser sign-in", policy.binary)
    try:
        process = popen(  # nosec B603 - fixed argv from BINARY_POLICIES, shell=False
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            env=_login_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError(
            f"Could not start '{' '.join(argv)}': {exc}. Run it yourself to "
            "see what it says."
        ) from exc

    collected: list = []

    # A dedicated reader: the code arrives mid-stream while the process keeps
    # running, so it cannot be read with communicate(), and an unread pipe
    # eventually fills and blocks the child.
    def _drain() -> None:
        stream = process.stdout
        if stream is None:
            return
        for line in stream:
            collected.append(line)

    reader = threading.Thread(
        target=_drain, name=f"{policy.binary}-login-reader", daemon=True
    )
    reader.start()

    # Built only once there is a code and a URL, so a DeviceLogin never exists
    # in a state where waiting on it would mean waiting for a handoff that was
    # never made.
    deadline = time.monotonic() + code_wait_s
    while time.monotonic() < deadline:
        text = "".join(collected)
        code_match = _DEVICE_CODE_RE.search(text)
        if code_match:
            url_match = _DEVICE_URL_RE.search(text[code_match.end() :])
            if url_match is None:
                _reap(process, policy.binary)
                raise SetupError(
                    f"'{policy.binary}' printed a sign-in code but no URL to "
                    "enter it at, so GAIA cannot hand the sign-in over. Run "
                    f"'{policy.binary} auth login --web' yourself."
                )
            # Deliberately not logged with the code or URL.
            logger.info("'%s' sign-in code received; awaiting user", policy.binary)
            return DeviceLogin(
                binary=policy.binary,
                process=process,
                code=code_match.group(1),
                url=url_match.group(1),
                timeout_s=setup.auth_login_timeout_s,
                _output=collected,
                _reader=reader,
            )
        if process.poll() is not None:
            break
        time.sleep(0.2)

    _reap(process, policy.binary)
    reader.join(timeout=5.0)
    # This path fires precisely when the code regex did NOT match, so a code may
    # be sitting in the buffer unrecognised — hence redacting the label line as
    # well as the token shapes.
    captured = _redact_code_lines(scrub_secrets("".join(collected).strip()))
    raise SetupError(
        f"'{policy.binary}' did not produce a browser sign-in code within "
        f"{code_wait_s:g}s, so there is nothing to hand over and GAIA will not "
        f"pretend the sign-in started. Run '{policy.binary} auth login --web' "
        "in a terminal instead." + (f" It said: {captured}" if captured else "")
    )


def _login_env() -> dict:
    """The environment the sign-in child runs in.

    Inherited as-is but with the browser-launch hook cleared: ``BROWSER`` names
    a command the CLI will execute, and a value that arrived from somewhere
    else in the environment would run under the sign-in rather than open a
    page. The user is given the URL and opens it themselves, so nothing is lost.
    """
    env = os.environ.copy()
    env.pop("BROWSER", None)
    return env
