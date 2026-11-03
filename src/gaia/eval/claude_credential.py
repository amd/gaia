# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Which Claude credential the evals use, and saying so.

One order for every eval that calls Claude — the judges, the agent-eval driver,
``gaia eval tasks``:

1. The Claude Code subscription: ``CLAUDE_CODE_OAUTH_TOKEN``, else the
   ``claude`` CLI's own login. Either needs the CLI on PATH.
2. ``ANTHROPIC_API_KEY`` from the environment.
3. ``ANTHROPIC_API_KEY`` from a ``.env`` file.

The credential in use is printed to stderr, never any part of its value.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

from gaia.env import dotenv_credential_file, working_dir_credential
from gaia.eval.process_tree import NEW_GROUP, kill_tree
from gaia.logger import get_logger

log = get_logger(__name__)

API_KEY_VAR = "ANTHROPIC_API_KEY"
OAUTH_TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"

OAUTH_TOKEN = "oauth_token"
CLI_LOGIN = "cli_login"
ENV_KEY = "env_key"
DOTENV_KEY = "dotenv_key"

#: ``claude auth status`` reads local state only; this bounds a wedged CLI.
AUTH_STATUS_TIMEOUT = 30.0

#: API-key sources ``claude auth status`` reports that are not a stored login:
#: the env var is step 2 of the order, and settings-file helpers are not loaded
#: by the judge (``--setting-sources ""``).
_NOT_A_LOGIN = frozenset({API_KEY_VAR, "apiKeyHelper"})

MISSING_CREDENTIAL_ERROR = (
    "No Claude credential for the eval: no Claude Code login, no "
    "CLAUDE_CODE_OAUTH_TOKEN, and no ANTHROPIC_API_KEY in the environment or a "
    ".env file.\n\n"
    "The judged evals (agent, drafting, action-item, briefing, sessions, tasks, "
    "corpus generation) need Claude and cannot produce a result without it. "
    "They use the first of:\n"
    "  1. SUBSCRIPTION (recommended if you have Claude Code Max): install the\n"
    "     CLI (`npm install -g @anthropic-ai/claude-code`) and log in (`claude`,\n"
    "     then `/login`). Where no interactive login is possible (CI), run\n"
    "     `claude setup-token` and export CLAUDE_CODE_OAUTH_TOKEN=<token>.\n"
    "  2. API KEY (billed to your Anthropic console):\n"
    "       export ANTHROPIC_API_KEY=sk-ant-...\n"
    "  3. The same ANTHROPIC_API_KEY=... line in a .env file in the working\n"
    "     directory.\n"
    "In CI, add the matching repository secret — see the judge-credential "
    "preflight in .github/workflows/test_email_agent_eval.yml.\n"
)

TOKEN_WITHOUT_CLI_ERROR = (
    "CLAUDE_CODE_OAUTH_TOKEN is set, but the subscription runs through the "
    "`claude` CLI, which is not on PATH (the token alone cannot drive it), and "
    "no ANTHROPIC_API_KEY is available.\n"
    "Install it with `npm install -g @anthropic-ai/claude-code`, or set "
    "ANTHROPIC_API_KEY instead to use the Messages API.\n"
    "See src/gaia/eval/claude_credential.py for the order credentials are tried."
)

#: Fixed text per source, for messages that must not carry a credential value.
_SOURCE_NAMES = {
    OAUTH_TOKEN: "the Claude Code subscription (CLAUDE_CODE_OAUTH_TOKEN)",
    CLI_LOGIN: "the Claude Code CLI login",
    ENV_KEY: "ANTHROPIC_API_KEY from the environment",
    DOTENV_KEY: "ANTHROPIC_API_KEY from a .env file",
}

_announced: set = set()
_login_cache: Dict[tuple, bool] = {}


@dataclass(frozen=True)
class ClaudeCredential:
    """The credential an eval will use. ``label`` is safe to print."""

    source: str
    dotenv_path: Optional[str] = None
    api_key: Optional[str] = field(default=None, repr=False)

    @property
    def label(self) -> str:
        """Where the credential came from; never any part of its value."""
        if self.source == DOTENV_KEY and self.dotenv_path:
            return f"{API_KEY_VAR} from {self.dotenv_path}"
        return _SOURCE_NAMES[self.source]

    @property
    def is_subscription(self) -> bool:
        return self.source in (OAUTH_TOKEN, CLI_LOGIN)

    def cli_flags(self) -> list:
        """Flags for ``claude -p``: ``--bare`` restricts auth to the API key."""
        return [] if self.is_subscription else ["--bare"]

    def cli_env(self, base: Mapping[str, str]) -> Dict[str, str]:
        """*base* adjusted so ``claude -p`` authenticates with this credential.

        The CLI prefers an inherited API key to OAuth, so the subscription path
        drops it; the key path carries a ``.env`` key the environment lacks.
        """
        env = dict(base)
        if self.is_subscription:
            env.pop(API_KEY_VAR, None)
        else:
            env[API_KEY_VAR] = self.api_key or ""
        return env


def _auth_status(claude_bin: str, env: Mapping[str, str]) -> dict:
    """``claude auth status --json``, run without the key or token in its env."""
    child = {
        k: v for k, v in env.items() if k.upper() not in (API_KEY_VAR, OAUTH_TOKEN_VAR)
    }
    try:
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            [claude_bin, "auth", "status", "--json"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child,
            **NEW_GROUP,
        )
    except OSError as e:
        raise _status_error(claude_bin, f"it could not start ({e})") from e
    try:
        stdout, stderr = proc.communicate(timeout=AUTH_STATUS_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        # On Windows `claude` is a .cmd wrapper: killing only it leaves node
        # running with the pipes open.
        kill_tree(proc)
        raise _status_error(
            claude_bin, f"it did not answer within {AUTH_STATUS_TIMEOUT:.0f}s"
        ) from e
    try:
        status = json.loads(stdout or "")
    except json.JSONDecodeError as e:
        detail = (stderr or stdout or "(no output)").strip()[:300]
        raise RuntimeError(
            f"`claude auth status --json` (from {claude_bin}) exited "
            f"{proc.returncode} without JSON, so its login is unknown: {detail}\n"
            "Update the CLI (`npm install -g @anthropic-ai/claude-code`), or take "
            "it off PATH to use ANTHROPIC_API_KEY."
        ) from e
    if not isinstance(status, dict):
        raise RuntimeError(
            f"`claude auth status --json` (from {claude_bin}) returned "
            f"{type(status).__name__}, not an object. Update the CLI."
        )
    return status


def _status_error(claude_bin: str, why: str) -> RuntimeError:
    return RuntimeError(
        f"Could not ask the Claude Code CLI ({claude_bin}) whether it is logged "
        f"in: `claude auth status` failed, {why}. Update the CLI "
        "(`npm install -g @anthropic-ai/claude-code`), or take it off PATH to "
        "use ANTHROPIC_API_KEY."
    )


def cli_logged_in(claude_bin: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """True when the CLI holds a login of its own (OAuth or a ``/login`` key).

    Cached per CLI and config dir: the check spawns the CLI, and a run asks once
    per scenario.
    """
    env = os.environ if env is None else env
    cache_key = (claude_bin, env.get("CLAUDE_CONFIG_DIR", ""))
    if cache_key not in _login_cache:
        status = _auth_status(claude_bin, env)
        _login_cache[cache_key] = bool(status.get("loggedIn")) and (
            status.get("apiKeySource") not in _NOT_A_LOGIN
        )
    return _login_cache[cache_key]


def _claude_bin() -> Optional[str]:
    return shutil.which("claude")


def resolve_claude_credential(
    env: Optional[Mapping[str, str]] = None,
) -> ClaudeCredential:
    """The first usable credential in the order above; raises when there is none."""
    env = os.environ if env is None else env
    claude_bin = _claude_bin()
    token = (env.get(OAUTH_TOKEN_VAR) or "").strip()

    if claude_bin and token:
        return ClaudeCredential(OAUTH_TOKEN)
    if claude_bin and cli_logged_in(claude_bin, env):
        return ClaudeCredential(CLI_LOGIN)

    found = api_key_credential(env)
    if found is not None:
        if token:
            log.warning(
                "CLAUDE_CODE_OAUTH_TOKEN is set but the `claude` CLI is not on "
                "PATH, so it cannot be used; using %s instead.",
                _SOURCE_NAMES[found.source],
            )
        return found
    if token:
        raise RuntimeError(TOKEN_WITHOUT_CLI_ERROR)
    raise ValueError(MISSING_CREDENTIAL_ERROR)


def api_key_credential(
    env: Optional[Mapping[str, str]] = None,
) -> Optional[ClaudeCredential]:
    """``ANTHROPIC_API_KEY`` from the environment, else a ``.env``; None if neither."""
    env = os.environ if env is None else env
    value = (env.get(API_KEY_VAR) or "").strip()
    if not value:
        value = (working_dir_credential(API_KEY_VAR) or "").strip()
    if not value:
        return None
    path = dotenv_credential_file(API_KEY_VAR, value)
    if path:
        return ClaudeCredential(DOTENV_KEY, dotenv_path=path, api_key=value)
    return ClaudeCredential(ENV_KEY, api_key=value)


def credential_present(env: Optional[Mapping[str, str]] = None) -> bool:
    """True when :func:`resolve_claude_credential` would return a credential.

    False only when nothing is set. A credential that is set but unusable (a
    token with no CLI, a CLI whose login cannot be read) raises its
    ``RuntimeError``, so the caller fails loudly with the fix instead of the
    generic "nothing set" message.
    """
    try:
        resolve_claude_credential(env)
    except ValueError:
        return False
    return True


def announce(chosen: ClaudeCredential, role: str = "Claude judge") -> None:
    """Print which credential *role* uses, once per process.

    stderr, not the logger: the eval report scripts and lane runners never set
    up console logging, and this line must show in every run. Only the fixed
    source name and, for a ``.env``, the file's path are printed.
    """
    line = f"{role}: using {chosen.label}"
    if line in _announced:
        return
    _announced.add(line)
    print(line, file=sys.stderr, flush=True)
