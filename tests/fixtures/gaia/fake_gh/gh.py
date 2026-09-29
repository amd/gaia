# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fake ``gh`` CLI for the gaia-agent eval suite — canned data, never live.

Follows ``tests/fixtures/email/fake_gmail.py``'s philosophy: deterministic
recorded responses in the same wire shape the real tool produces, so the agent
under eval cannot tell it is not talking to GitHub. Scenario setup prepends
this directory to PATH; see README.md.

Served: the ALLOW-tier reads the github-triage skill uses (``--version``,
``auth status``, ``issue list``, ``issue view``, ``api notifications``) plus
the CONFIRM-tier ``issue comment`` write with a canned success — under
``GAIA_AUTO_APPROVE_TOOLS=1`` the eval approves it and scenarios assert the
outcome. Refuse-tier commands (``auth token``, ``alias``,
``extension``, ``api -X POST`` …) are deliberately NOT faked: GAIA's binary
policy (``gaia.skills.binaries``) refuses them before any shell runs, so if
one reaches this shim the permission gate leaked — the shim exits nonzero with
a message that says exactly that. Unknown commands never return empty success
(no silent fallbacks).

Auth state: github.com is signed in by default. Hosts listed in
``tiers_resilience/fake_gh_auth.json`` are signed out (commands fail with gh's
own exit 4), and ``FAKE_GH_AUTH=logged_out`` signs out every host.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

#: The one repository this fixture has recordings for
#: (contract: eval/scenarios/GAIA_FIXTURE_VALUES.md).
FIXTURE_REPO = "acme-labs/widgetworks"

DEFAULT_HOST = "github.com"

#: Hosts the resilience scenarios need signed out. A sibling fixture dir so the
#: staged copy (~/gaia-eval/fake_gh -> ~/gaia-eval/tiers_resilience) finds it too.
AUTH_STATE_FILE = (
    Path(__file__).resolve().parent.parent / "tiers_resilience" / "fake_gh_auth.json"
)

#: ``FAKE_GH_AUTH=logged_out`` signs out every host — a local repro of #4428.
AUTH_ENV = "FAKE_GH_AUTH"

#: First tokens of gh commands GAIA's policy REFUSES outright. Reaching this
#: shim with one of them means the permission gate did not do its job.
_REFUSE_TIER = {
    ("auth", "token"),
    ("alias",),
    ("extension",),
    ("config",),
    ("codespace",),
    ("pr", "merge"),
    ("issue", "close"),
    ("label", "delete"),
    ("repo", "delete"),
}


def _fail(message: str, code: int = 2) -> int:
    print(f"fake gh: {message}", file=sys.stderr)
    return code


def _signed_out_hosts() -> set[str] | None:
    """Hosts with no credentials; ``None`` means every host is signed out."""
    mode = os.environ.get(AUTH_ENV, "")
    if mode == "logged_out":
        return None
    if mode not in ("", "logged_in"):
        raise SystemExit(
            _fail(f"{AUTH_ENV}={mode!r} is not a mode; use logged_in or logged_out")
        )
    if not AUTH_STATE_FILE.is_file():
        return set()
    try:
        state = json.loads(AUTH_STATE_FILE.read_text(encoding="utf-8"))
        return set(state["signed_out_hosts"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SystemExit(
            _fail(f"auth state file {AUTH_STATE_FILE} is malformed ({exc})")
        ) from exc


def _is_signed_out(host: str) -> bool:
    hosts = _signed_out_hosts()
    return hosts is None or host in hosts


def _target_host(argv: list[str]) -> str:
    """The host a command talks to: --hostname, a HOST/OWNER/REPO repo, GH_HOST."""
    for i, token in enumerate(argv):
        for flag in ("--hostname", "--repo", "-R"):
            value = None
            if token == flag and i + 1 < len(argv):
                value = argv[i + 1]
            elif token.startswith(flag + "="):
                value = token.partition("=")[2]
            if value is None:
                continue
            if flag == "--hostname":
                return value
            if value.count("/") >= 2:
                return value.split("/", 1)[0]
    return os.environ.get("GH_HOST") or DEFAULT_HOST


def _auth_required(host: str) -> int:
    """Real gh's wording and exit code 4 for a command with no credentials."""
    login = "gh auth login" + ("" if host == DEFAULT_HOST else f" --hostname {host}")
    token_var = "GH_TOKEN" if host == DEFAULT_HOST else "GH_ENTERPRISE_TOKEN"
    print(
        f"To get started with GitHub CLI, please run:  {login}\n"
        f"Alternatively, populate the {token_var} environment variable with a "
        "GitHub API authentication token.",
        file=sys.stderr,
    )
    return 4


def _auth_status(args: list[str]) -> int:
    flags, _ = _parse_flags(args, {"--hostname", "--json"})
    host = flags.get("--hostname")
    if "--json" in flags:
        # The shape GAIA's check_cli_setup parses; signed-out hosts are absent.
        hosts = {}
        if (host in (None, DEFAULT_HOST)) and not _is_signed_out(DEFAULT_HOST):
            hosts[DEFAULT_HOST] = [
                {
                    "state": "success",
                    "active": True,
                    "host": DEFAULT_HOST,
                    "login": "fixture-bot",
                    "tokenSource": "keyring",
                    "scopes": "repo, read:org",
                    "gitProtocol": "https",
                }
            ]
        print(json.dumps({"hosts": hosts}, indent=2))
        return 0
    if isinstance(host, str) and _is_signed_out(host):
        print(f"You are not logged into any accounts on {host}", file=sys.stderr)
        return 1
    if _is_signed_out(DEFAULT_HOST):
        print(
            "You are not logged into any GitHub hosts. To log in, run: gh auth login",
            file=sys.stderr,
        )
        return 1
    # ASCII only: Windows consoles decode cp1252 and choke on check marks.
    print("github.com")
    print("  - Logged in to github.com account fixture-bot (keyring)")
    print("  - Active account: true")
    print("  - Token scopes: 'repo', 'read:org'")
    return 0


def _load(name: str):
    path = DATA_DIR / name
    if not path.is_file():
        raise SystemExit(
            _fail(f"canned data file missing: {path} — the fixture is broken")
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_flags(args: list[str], value_flags: set[str]) -> tuple[dict, list[str]]:
    """Split *args* into ``{flag: value-or-True}`` and positionals.

    Repeated flags keep the last value (matches gh). Unknown flags are the
    caller's problem — it decides which are supported and fails on the rest.
    """
    flags: dict[str, object] = {}
    positional: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("-"):
            if "=" in token:
                name, _, value = token.partition("=")
                flags[name] = value
            elif token in value_flags:
                if i + 1 >= len(args):
                    raise SystemExit(_fail(f"flag {token} expects a value"))
                flags[token] = args[i + 1]
                i += 1
            else:
                flags[token] = True
        else:
            positional.append(token)
        i += 1
    return flags, positional


def _require_repo(flags: dict) -> str:
    repo = flags.get("--repo") or flags.get("-R")
    if not isinstance(repo, str):
        raise SystemExit(
            _fail(
                "--repo is required. This fixture only records "
                f"'{FIXTURE_REPO}' — point the scenario at it."
            )
        )
    if repo != FIXTURE_REPO:
        raise SystemExit(
            _fail(
                f"no recording for repository '{repo}'. This fixture serves "
                f"only '{FIXTURE_REPO}'; it never contacts live GitHub."
            )
        )
    return repo


def _select_fields(record: dict, json_spec: object, *, valid: set[str]) -> dict:
    if not isinstance(json_spec, str) or not json_spec:
        raise SystemExit(
            _fail("--json requires a comma-separated field list (matches real gh)")
        )
    fields = [f.strip() for f in json_spec.split(",") if f.strip()]
    unknown = [f for f in fields if f not in valid]
    if unknown:
        raise SystemExit(
            _fail(
                f"Unknown JSON field: {', '.join(unknown)} "
                f"(valid: {', '.join(sorted(valid))})"
            )
        )
    return {f: record.get(f) for f in fields}


_ISSUE_FIELDS = {
    "number",
    "title",
    "body",
    "state",
    "labels",
    "createdAt",
    "updatedAt",
    "author",
    "comments",
    "url",
}


def _issue_list(args: list[str]) -> int:
    flags, positional = _parse_flags(
        args,
        {
            "--repo",
            "-R",
            "--limit",
            "-L",
            "--json",
            "--label",
            "-l",
            "--state",
            "-s",
            "--search",
            "-S",
        },
    )
    if positional:
        return _fail(f"unexpected arguments to 'issue list': {positional}")
    _require_repo(flags)

    issues = _load("issues.json")

    label = flags.get("--label") or flags.get("-l")
    if isinstance(label, str):
        wanted = {piece.strip().lower() for piece in label.split(",")}
        issues = [
            i
            for i in issues
            if wanted & {lab["name"].lower() for lab in i.get("labels", [])}
        ]

    state = flags.get("--state") or flags.get("-s") or "open"
    if state != "all":
        issues = [i for i in issues if i.get("state", "open").lower() == state]

    search = flags.get("--search") or flags.get("-S")
    if isinstance(search, str):
        needle = search.lower()
        issues = [
            i
            for i in issues
            if needle in i["title"].lower() or needle in i.get("body", "").lower()
        ]

    limit = flags.get("--limit") or flags.get("-L") or "30"
    try:
        issues = issues[: int(limit)]
    except ValueError:
        return _fail(f"--limit expects a number, got {limit!r}")

    json_spec = flags.get("--json")
    if json_spec is None:
        # gh without --json prints a human table; the skill always passes
        # --json, so keep the fixture honest instead of inventing a layout.
        return _fail("'issue list' without --json is not recorded; pass --json")
    out = [_select_fields(i, json_spec, valid=_ISSUE_FIELDS) for i in issues]
    print(json.dumps(out, indent=2))
    return 0


def _issue_view(args: list[str]) -> int:
    flags, positional = _parse_flags(args, {"--repo", "-R", "--json"})
    if len(positional) != 1:
        return _fail(f"'issue view' expects one issue number, got {positional}")
    _require_repo(flags)
    try:
        number = int(positional[0].lstrip("#"))
    except ValueError:
        return _fail(f"not an issue number: {positional[0]!r}")

    for issue in _load("issues.json"):
        if issue["number"] == number:
            json_spec = flags.get("--json")
            record = (
                _select_fields(issue, json_spec, valid=_ISSUE_FIELDS)
                if json_spec is not None
                else issue
            )
            print(json.dumps(record, indent=2))
            return 0
    return _fail(
        f"no issue #{number} recorded for {FIXTURE_REPO} "
        "(GraphQL: Could not resolve to an issue)",
        code=1,
    )


#: Flags GAIA's policy refuses on any gh write. Refused BEFORE the shell runs;
#: reaching the shim with one means the gate leaked.
_WRITE_DENIED_FLAGS = ("-F", "--body-file", "-e", "--editor", "-w", "--web")


def _issue_comment(args: list[str]) -> int:
    """CONFIRM-tier write: canned success (contract's eval-transport section).

    Under GAIA_AUTO_APPROVE_TOOLS=1 the eval auto-approves CONFIRM commands, so
    this write executes; scenarios assert the outcome against this response.
    """
    flags, positional = _parse_flags(args, {"--repo", "-R", "--body", "-b"})
    leaked = [f for f in _WRITE_DENIED_FLAGS if f in flags]
    if leaked:
        return _fail(
            f"REFUSE-tier flag(s) reached the shell: {', '.join(leaked)} are "
            "refused by GAIA's binary policy and are never faked. The "
            "permission gate leaked — treat this eval run as failed."
        )
    if len(positional) != 1:
        return _fail(f"'issue comment' expects one issue number, got {positional}")
    _require_repo(flags)
    try:
        number = int(positional[0].lstrip("#"))
    except ValueError:
        return _fail(f"not an issue number: {positional[0]!r}")
    body = flags.get("--body") or flags.get("-b")
    if not isinstance(body, str) or not body.strip():
        return _fail("'issue comment' requires --body with the comment text")

    if not any(issue["number"] == number for issue in _load("issues.json")):
        return _fail(
            f"no issue #{number} recorded for {FIXTURE_REPO} "
            "(GraphQL: Could not resolve to an issue)",
            code=1,
        )
    # Real gh prints the new comment's URL. Deterministic id: issue number
    # + comment length, so re-runs and judges see a stable, checkable value.
    print(
        f"https://github.com/{FIXTURE_REPO}/issues/{number}"
        f"#issuecomment-90{number}{len(body):04d}"
    )
    return 0


def _api(args: list[str]) -> int:
    flags, positional = _parse_flags(args, {"--jq", "-q", "-X", "--method"})
    method = flags.get("-X") or flags.get("--method")
    writes = method not in (None, "GET") or any(
        f in flags for f in ("-f", "--field", "-F", "--raw-field")
    )
    if writes:
        return _fail(
            "REFUSE-tier command reached the shell: 'gh api' writes are "
            "refused by GAIA's binary policy and are never faked. The "
            "permission gate leaked — treat this eval run as failed."
        )
    if len(positional) != 1 or not positional[0].split("?")[0].strip("/").startswith(
        "notifications"
    ):
        return _fail(
            f"no recording for 'gh api {' '.join(positional)}'; only the "
            "notifications feed is canned"
        )

    notifications = _load("notifications.json")
    jq = flags.get("--jq") or flags.get("-q")
    if isinstance(jq, str):
        # Not a jq engine: any --jq on the notifications feed yields the TSV
        # the github-triage SKILL.md documents (reason, repo, type, date, title).
        for n in notifications:
            print(
                "\t".join(
                    [
                        n["reason"],
                        n["repository"]["full_name"],
                        n["subject"]["type"],
                        n["updated_at"][:10],
                        n["subject"]["title"],
                    ]
                )
            )
        return 0
    print(json.dumps(notifications, indent=2))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        return _fail(
            "no command. This fake serves: --version, auth status, "
            "issue list, issue view, api notifications"
        )

    head = tuple(argv[:2])
    for refused in _REFUSE_TIER:
        if head[: len(refused)] == refused:
            return _fail(
                f"REFUSE-tier command reached the shell: 'gh {' '.join(refused)}' "
                "is refused by GAIA's binary policy and is never faked. The "
                "permission gate leaked — treat this eval run as failed."
            )

    if argv[0] == "--version":
        print("gh version 2.62.0 (2026-01-15) [gaia eval fixture — canned data]")
        return 0
    if head == ("auth", "status"):
        return _auth_status(argv[2:])
    host = _target_host(argv)
    if _is_signed_out(host):
        return _auth_required(host)
    if head == ("issue", "list"):
        return _issue_list(argv[2:])
    if head == ("issue", "view"):
        return _issue_view(argv[2:])
    if head == ("issue", "comment"):
        return _issue_comment(argv[2:])
    if argv[0] == "api":
        return _api(argv[1:])

    return _fail(
        f"unrecognized command: gh {' '.join(argv)}. This fixture serves only "
        "the commands github-triage uses (--version, auth status, issue "
        "list/view/comment, api notifications). It never invents a response "
        "for anything else."
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
