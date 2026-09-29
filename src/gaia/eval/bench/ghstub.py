# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An offline stand-in for the GitHub CLI, first on every agent's PATH.

Every task gets it, whichever harness runs, so no agent ever reaches GitHub
through someone's personal ``gh`` login. It serves the fixture repositories in
``eval/tasks/github/`` from a per-task copy, logs every call, and refuses
writes the way GitHub refuses a token without write scope. A task can grant
label writes (``"gh": {"writes": ["label"]}``); they change the task's copy
and nothing else.

A task's ``gh`` block can also simulate trouble:

- ``{"mode": "rate_limit", "window_s": 60}``: GitHub's real rate-limit errors
  until the window, counted from the first network call, has passed.
- ``{"mode": "transient"}``: one HTTP 502, then normal service.

:func:`install` writes a ``gh`` launcher that runs this file directly. It
uses the standard library only, so a call costs an interpreter start, not an
import of GAIA.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, NoReturn, Optional, Tuple

STATE_ENV = "GAIA_BENCH_GH_STATE"
FIXTURES = Path(__file__).resolve().parents[4] / "eval" / "tasks" / "github"
MODES = ("offline", "rate_limit", "transient")
WRITE_GRANTS = ("label",)
VERSION = "gh version 2.60.0 (offline stand-in for GAIA benchmarks)"

#: Subcommands that change something on GitHub.
WRITES = {
    "issue": {
        "close",
        "comment",
        "edit",
        "delete",
        "create",
        "reopen",
        "lock",
        "unlock",
        "transfer",
        "pin",
        "unpin",
        "develop",
    },
    "pr": {
        "close",
        "comment",
        "edit",
        "merge",
        "create",
        "review",
        "ready",
        "reopen",
        "lock",
    },
    "label": {"create", "edit", "delete", "clone"},
    "repo": {"edit", "delete", "rename", "archive", "create", "fork", "sync"},
    "release": {"create", "edit", "delete", "upload"},
}
ISSUE_FIELDS = (
    "assignees",
    "author",
    "body",
    "closed",
    "comments",
    "createdAt",
    "id",
    "labels",
    "milestone",
    "number",
    "state",
    "title",
    "updatedAt",
    "url",
)
FORBIDDEN = (
    "HTTP 403: Resource not accessible by personal access token "
    "(https://api.github.com/graphql)"
)
#: A made-up account, so the simulated error names nobody real.
RATE_LIMITED_USER = 1000001


# ---------------------------------------------------------------------------
# Installing it for one task
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GhSandbox:
    """Where one task's stand-in lives, and the environment that selects it."""

    bin_dir: Path
    state: Path
    log: Path
    config_dir: Path

    @property
    def env(self) -> Dict[str, str]:
        # GH_CONFIG_DIR points at an empty directory, so even a real gh reached
        # by absolute path finds no login.
        return {
            STATE_ENV: str(self.state),
            "GH_CONFIG_DIR": str(self.config_dir),
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
        }

    def calls(self) -> List[Dict[str, Any]]:
        if not self.log.is_file():
            return []
        return [
            json.loads(line)
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def validate(gh: Mapping[str, Any], where: str) -> None:
    unknown = set(gh) - {"mode", "window_s", "writes"}
    if unknown:
        raise ValueError(f"{where}: unknown gh keys {sorted(unknown)}")
    if gh.get("mode", "offline") not in MODES:
        raise ValueError(f"{where}: gh mode must be one of {MODES}")
    bad = set(gh.get("writes") or ()) - set(WRITE_GRANTS)
    if bad:
        raise ValueError(f"{where}: gh writes can only grant {WRITE_GRANTS}")


def _launchers(bin_dir: Path) -> None:
    script, python = Path(__file__).resolve(), sys.executable
    sh = bin_dir / "gh"
    sh.write_text(f'#!/bin/sh\nexec "{python}" -I "{script}" "$@"\n', encoding="utf-8")
    sh.chmod(0o755)
    (bin_dir / "gh.cmd").write_text(
        f'@echo off\r\n"{python}" -I "{script}" %*\r\n', encoding="utf-8"
    )


def install(
    harness_dir: Path, gh: Optional[Mapping[str, Any]] = None, fixtures: Path = FIXTURES
) -> GhSandbox:
    """Give one task its own stand-in, state and copy of every fixture repo."""
    gh = dict(gh or {})
    root = harness_dir / "gh"
    sandbox = GhSandbox(
        bin_dir=root / "bin",
        state=root / "state.json",
        log=root / "calls.jsonl",
        config_dir=root / "config",
    )
    for directory in (sandbox.bin_dir, sandbox.config_dir, root / "repos"):
        directory.mkdir(parents=True, exist_ok=True)
    repos = {}
    for fixture in sorted(fixtures.glob("*.json")):
        name = json.loads(fixture.read_text(encoding="utf-8"))["repo"]
        copy = root / "repos" / fixture.name
        shutil.copyfile(fixture, copy)
        repos[name.lower()] = str(copy)
    if not repos:
        # Serving nothing would tell the agent the repository does not exist,
        # and a GitHub task would fail as if the model had.
        raise FileNotFoundError(
            f"No gh fixture repositories in {fixtures}. `gaia eval tasks` runs "
            "from a checkout of the repository, where eval/tasks/github/ holds "
            "them."
        )
    sandbox.log.write_text("", encoding="utf-8")
    sandbox.state.write_text(
        json.dumps(
            {
                "mode": gh.get("mode", "offline"),
                "window_s": float(gh.get("window_s", 60)),
                "writes": list(gh.get("writes") or ()),
                "log": str(sandbox.log),
                "repos": repos,
                "started": None,
                "tripped": False,
            }
        ),
        encoding="utf-8",
    )
    _launchers(sandbox.bin_dir)
    return sandbox


# ---------------------------------------------------------------------------
# The CLI itself
# ---------------------------------------------------------------------------


class _Exit(Exception):
    def __init__(self, code: int, action: str):
        super().__init__(code)
        self.code, self.action = code, action


class Stub:
    """One ``gh`` invocation against the task's state."""

    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.state = json.loads(state_path.read_text(encoding="utf-8"))

    # -- plumbing ----------------------------------------------------------

    def _save_state(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def log(self, argv: List[str], action: str, rc: int) -> None:
        with open(self.state["log"], "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"t": time.time(), "argv": argv, "action": action, "rc": rc})
                + "\n"
            )

    def fail(self, message: str, action: str = "failed", rc: int = 1) -> NoReturn:
        sys.stderr.write(message.rstrip("\n") + "\n")
        raise _Exit(rc, action)

    def repo_path(self, name: Optional[str]) -> Path:
        if not name:
            self.fail(
                "could not determine the repository: this directory has no GitHub "
                "remote. Pass --repo OWNER/REPO."
            )
        path = self.state["repos"].get(name.lower().removeprefix("https://github.com/"))
        if not path:
            self.fail(
                f"GraphQL: Could not resolve to a Repository with the name '{name}'. "
                "(repository)"
            )
        return Path(path)

    def load(self, name: Optional[str]) -> Dict[str, Any]:
        return json.loads(self.repo_path(name).read_text(encoding="utf-8"))

    def store(self, name: str, repo: Dict[str, Any]) -> None:
        path = self.repo_path(name)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(repo, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def issue(self, repo: Dict[str, Any], number: str) -> Dict[str, Any]:
        for issue in repo["issues"]:
            if str(issue["number"]) == str(number).lstrip("#"):
                return issue
        self.fail(
            "GraphQL: Could not resolve to an issue or pull request with the number "
            f"of {number}. (repository.issue)"
        )

    # -- conditions --------------------------------------------------------

    def network_conditions(self, args: List[str]) -> None:
        mode, now = self.state.get("mode", "offline"), time.time()
        if mode == "rate_limit":
            if self.state.get("started") is None:
                self.state["started"] = now
                self._save_state()
            until = self.state["started"] + float(self.state.get("window_s", 60))
            if now < until:
                if args[:2] in (["api", "rate_limit"], ["api", "/rate_limit"]):
                    bucket = {
                        "limit": 5000,
                        "used": 5000,
                        "remaining": 0,
                        "reset": int(until),
                    }
                    print(
                        json.dumps(
                            {
                                "resources": {"core": bucket, "graphql": bucket},
                                "rate": bucket,
                            }
                        )
                    )
                    raise _Exit(0, "rate_limit_status")
                stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now))
                if args and args[0] == "api":
                    self.fail(
                        "HTTP 403: API rate limit exceeded for user ID "
                        f"{RATE_LIMITED_USER}. If you reach out to GitHub Support "
                        "for help, please include the request ID "
                        f"C2A4:3B1F:9E0D71:A1C3F2:66EC1F0A and timestamp {stamp}. "
                        "(https://api.github.com/rate_limit)",
                        "rate_limited",
                    )
                self.fail(
                    "GraphQL: API rate limit already exceeded for user ID "
                    f"{RATE_LIMITED_USER}.",
                    "rate_limited",
                )
        if mode == "transient" and not self.state.get("tripped"):
            self.state["tripped"] = True
            self._save_state()
            self.fail(
                "HTTP 502: Bad Gateway (https://api.github.com/graphql)", "failed_502"
            )

    # -- output ------------------------------------------------------------

    def emit(self, data: Any, fields: Optional[str], jq: Optional[str]) -> None:
        if fields is None:
            self.fail("cannot use `--jq` without specifying `--json`")
        if fields is not None:
            if not fields:
                listed = "\n".join(f"  {f}" for f in ISSUE_FIELDS)
                self.fail(
                    f"Specify one or more comma-separated fields for `--json`:\n{listed}"
                )
            wanted = [f.strip() for f in fields.split(",") if f.strip()]
            bad = [f for f in wanted if f not in ISSUE_FIELDS]
            if bad:
                self.fail(f'Unknown JSON field: "{bad[0]}"')
            pick = lambda item: {f: item[f] for f in wanted}  # noqa: E731
            data = [pick(d) for d in data] if isinstance(data, list) else pick(data)
        if jq is not None:
            self.jq(data, jq)
            return
        print(json.dumps(data, indent=2))

    def jq(self, data: Any, expression: str) -> None:
        jq = shutil.which("jq")
        if not jq:
            self.fail(
                "gh stand-in: --jq needs the jq program on PATH. Use --json and "
                "read the JSON instead."
            )
        proc = subprocess.run(
            [jq, "-r", expression],
            input=json.dumps(data),
            capture_output=True,
            text=True,
            check=False,
        )
        sys.stdout.write(proc.stdout)
        if proc.returncode:
            self.fail(proc.stderr.strip() or f"jq exited {proc.returncode}")

    # -- representations ---------------------------------------------------

    @staticmethod
    def cli_issue(repo: Dict[str, Any], issue: Dict[str, Any]) -> Dict[str, Any]:
        known = {label["name"]: label for label in repo["labels"]}
        return {
            "assignees": [],
            "author": {"login": issue["author"]},
            "body": issue["body"],
            "closed": issue["state"] == "CLOSED",
            "comments": [
                {
                    "author": {"login": c["author"]},
                    "body": c["body"],
                    "createdAt": c["createdAt"],
                }
                for c in issue["comments"]
            ],
            "createdAt": issue["createdAt"],
            "id": f"I_stub{issue['number']}",
            "labels": [
                {
                    "name": name,
                    "color": known.get(name, {}).get("color", ""),
                    "description": known.get(name, {}).get("description", ""),
                }
                for name in issue["labels"]
            ],
            "milestone": None,
            "number": issue["number"],
            "state": issue["state"],
            "title": issue["title"],
            "updatedAt": issue["updatedAt"],
            "url": f"https://github.com/{repo['repo']}/issues/{issue['number']}",
        }

    @staticmethod
    def rest_issue(repo: Dict[str, Any], issue: Dict[str, Any]) -> Dict[str, Any]:
        known = {label["name"]: label for label in repo["labels"]}
        url = f"https://api.github.com/repos/{repo['repo']}/issues/{issue['number']}"
        return {
            "url": url,
            "html_url": f"https://github.com/{repo['repo']}/issues/{issue['number']}",
            "number": issue["number"],
            "title": issue["title"],
            "state": issue["state"].lower(),
            "user": {"login": issue["author"]},
            "labels": [
                {
                    "name": n,
                    **{k: v for k, v in known.get(n, {}).items() if k != "name"},
                }
                for n in issue["labels"]
            ],
            "comments": len(issue["comments"]),
            "created_at": issue["createdAt"],
            "updated_at": issue["updatedAt"],
            "body": issue["body"],
        }

    # -- commands ----------------------------------------------------------

    def run(self, args: List[str]) -> str:
        if not args or args[0] in ("help", "--help", "-h"):
            print(
                "Offline stand-in for the GitHub CLI.\n\n"
                "  gh issue list|view|edit    gh label list    gh repo view\n"
                "  gh api repos/OWNER/REPO/...    gh auth status    gh --version"
            )
            return "local"
        if args[0] in ("--version", "version"):
            print(VERSION)
            return "local"
        if args[0] == "auth":
            if args[1:2] == ["status"]:
                sys.stderr.write(
                    "github.com\n  - Logged in to github.com account bench-user "
                    "(offline stand-in)\n  - Token scopes: 'repo:read'\n"
                )
                return "local"
            self.fail(
                "gh stand-in: authentication is not available in a benchmark task.",
                "refused_auth",
            )
        handler = {
            "issue": self.cmd_issue,
            "label": self.cmd_label,
            "repo": self.cmd_repo,
            "pr": self.cmd_pr,
            "api": self.cmd_api,
        }.get(args[0])
        if handler is None:
            self.fail(
                f"gh stand-in: `gh {args[0]}` is not available offline. Supported: "
                "issue, label, repo view, pr list, api.",
                "unsupported",
            )
        if self.is_write(args) and not self.write_granted(args):
            self.fail(FORBIDDEN, "blocked_write")
        self.network_conditions(args)
        return handler(args[1:])

    def is_write(self, args: List[str]) -> bool:
        if len(args) >= 2 and args[1] in WRITES.get(args[0], ()):
            return True
        if args and args[0] == "api":
            return _api_method(args[1:]) != "GET"
        return False

    def write_granted(self, args: List[str]) -> bool:
        if "label" not in self.state.get("writes", ()):
            return False
        if args[:2] == ["label", "create"]:
            return True
        if args[:2] == ["issue", "edit"]:
            _, opts = _parse(
                args[2:], {"-R", "--repo", "--add-label", "--remove-label"}, set()
            )
            return not opts.get("_unknown")
        if args[:1] == ["api"]:
            positional, _ = _parse(args[1:], _API_VALUE_FLAGS, _API_BOOL_FLAGS)
            return bool(positional) and "/labels" in positional[0]
        return False

    def cmd_issue(self, args: List[str]) -> str:
        sub, rest = (args[0], args[1:]) if args else ("", [])
        value_flags = {
            "-R",
            "--repo",
            "-s",
            "--state",
            "-l",
            "--label",
            "-L",
            "--limit",
            "--json",
            "-q",
            "--jq",
            "-S",
            "--search",
            "-A",
            "--author",
            "--add-label",
            "--remove-label",
            "-t",
            "--template",
        }
        positional, opts = _parse(rest, value_flags, {"-c", "--comments", "--web"})
        if opts.get("-t") or opts.get("--template"):
            self.fail("gh stand-in: --template is not supported; use --json or --jq.")
        name = opts.get("-R") or opts.get("--repo")
        fields = _json_flag(opts)
        jq = opts.get("-q") or opts.get("--jq")
        repo = self.load(name)
        if sub == "list":
            state = (opts.get("-s") or opts.get("--state") or "open").upper()
            label = opts.get("-l") or opts.get("--label")
            search = (opts.get("-S") or opts.get("--search") or "").lower().split()
            limit = int(opts.get("-L") or opts.get("--limit") or 30)
            found = [
                i
                for i in sorted(repo["issues"], key=lambda i: -i["number"])
                if (state == "ALL" or i["state"] == state)
                and (not label or label in i["labels"])
                and all(w in (i["title"] + " " + i["body"]).lower() for w in search)
            ][:limit]
            if fields is not None or jq is not None:
                self.emit([self.cli_issue(repo, i) for i in found], fields, jq)
                return "read"
            if not found:
                sys.stderr.write(f"no open issues in {repo['repo']}\n")
            for i in found:
                print(
                    f"{i['number']}\t{i['state']}\t{i['title']}\t"
                    f"{', '.join(i['labels'])}\t{i['updatedAt']}"
                )
            return "read"
        if sub == "view":
            if not positional:
                self.fail("issue number or url required as argument")
            issue = self.issue(repo, positional[0].rsplit("/", 1)[-1])
            if fields is not None or jq is not None:
                self.emit(self.cli_issue(repo, issue), fields, jq)
                return "read"
            print(
                f"title:\t{issue['title']}\nstate:\t{issue['state']}\n"
                f"author:\t{issue['author']}\nlabels:\t{', '.join(issue['labels'])}\n"
                f"comments:\t{len(issue['comments'])}\nassignees:\t\nprojects:\t\n"
                f"milestone:\t\nnumber:\t{issue['number']}\n--\n{issue['body']}"
            )
            if "-c" in opts or "--comments" in opts:
                for c in issue["comments"]:
                    print(f"author:\t{c['author']}\n--\n{c['body']}\n--")
            return "read"
        if sub == "edit":
            if not positional:
                self.fail("issue number or url required as argument")
            issue = self.issue(repo, positional[0].rsplit("/", 1)[-1])
            known = {label["name"] for label in repo["labels"]}
            add = _names(opts.get("--add-label"))
            missing = [n for n in add if n not in known]
            if missing:
                self.fail(f"'{missing[0]}' not found")
            remove = set(_names(opts.get("--remove-label")))
            issue["labels"] = [n for n in issue["labels"] if n not in remove]
            issue["labels"] += [n for n in add if n not in issue["labels"]]
            self.store(repo["repo"], repo)
            print(f"https://github.com/{repo['repo']}/issues/{issue['number']}")
            return "applied_write"
        self.fail(
            f"gh stand-in: `gh issue {sub}` is not available offline.", "unsupported"
        )

    def cmd_label(self, args: List[str]) -> str:
        sub, rest = (args[0], args[1:]) if args else ("", [])
        positional, opts = _parse(
            rest,
            {
                "-R",
                "--repo",
                "--json",
                "-q",
                "--jq",
                "-c",
                "--color",
                "-d",
                "--description",
                "-L",
                "--limit",
            },
            {"-f", "--force"},
        )
        name = opts.get("-R") or opts.get("--repo")
        repo = self.load(name)
        if sub == "list":
            fields = _json_flag(opts)
            jq = opts.get("-q") or opts.get("--jq")
            if fields is not None or jq is not None:
                labels = repo["labels"]
                if fields:
                    wanted = fields.split(",")
                    labels = [
                        {k: v for k, v in lb.items() if k in wanted} for lb in labels
                    ]
                if jq is not None:
                    self.jq(labels, jq)
                else:
                    print(json.dumps(labels, indent=2))
                return "read"
            for label in repo["labels"]:
                print(f"{label['name']}\t{label['description']}\t#{label['color']}")
            return "read"
        if sub == "create":
            if not positional:
                self.fail("cannot create label: name argument required")
            if any(lb["name"] == positional[0] for lb in repo["labels"]):
                self.fail(
                    f'label with name "{positional[0]}" already exists; use `--force` '
                    "to update its color and description"
                )
            repo["labels"].append(
                {
                    "name": positional[0],
                    "color": (opts.get("-c") or opts.get("--color") or "ededed"),
                    "description": opts.get("-d") or opts.get("--description") or "",
                }
            )
            self.store(repo["repo"], repo)
            return "applied_write"
        self.fail(
            f"gh stand-in: `gh label {sub}` is not available offline.", "unsupported"
        )

    def cmd_repo(self, args: List[str]) -> str:
        if args[:1] != ["view"]:
            self.fail("gh stand-in: only `gh repo view` is available offline.")
        positional, opts = _parse(
            args[1:], {"-R", "--repo", "--json", "-q", "--jq", "-b", "--branch"}, set()
        )
        repo = self.load(positional[0] if positional else opts.get("-R"))
        info = {
            "name": repo["repo"].split("/")[1],
            "owner": {"login": repo["repo"].split("/")[0]},
            "nameWithOwner": repo["repo"],
            "description": repo["description"],
            "defaultBranchRef": {"name": repo["default_branch"]},
            "url": f"https://github.com/{repo['repo']}",
        }
        fields = _json_flag(opts)
        jq = opts.get("-q") or opts.get("--jq")
        if fields is not None or jq is not None:
            if fields:
                info = {k: v for k, v in info.items() if k in fields.split(",")}
            if jq is not None:
                self.jq(info, jq)
            else:
                print(json.dumps(info, indent=2))
            return "read"
        print(f"name:\t{repo['repo']}\ndescription:\t{repo['description']}\n--")
        return "read"

    def cmd_pr(self, args: List[str]) -> str:
        _, opts = _parse(args[1:], {"-R", "--repo", "--json", "-q", "--jq"}, set())
        repo = self.load(opts.get("-R") or opts.get("--repo"))
        if args[:1] == ["list"]:
            if _json_flag(opts) is not None:
                print("[]")
            else:
                sys.stderr.write(f"no open pull requests in {repo['repo']}\n")
            return "read"
        self.fail(f"no pull requests found in {repo['repo']}")

    def cmd_api(self, args: List[str]) -> str:
        positional, opts = _parse(args, _API_VALUE_FLAGS, _API_BOOL_FLAGS)
        if not positional:
            self.fail("gh stand-in: `gh api` needs an endpoint")
        endpoint = positional[0]
        if endpoint.lstrip("/") == "graphql":
            self.fail(
                "gh stand-in: GraphQL is not available offline; use the REST "
                "endpoints or gh issue list/view.",
                "unsupported",
            )
        path, _, query = endpoint.lstrip("/").partition("?")
        params = dict(p.partition("=")[::2] for p in query.split("&") if p)
        jq = opts.get("-q") or opts.get("--jq")
        method = _api_method(args)
        if path == "rate_limit":
            bucket = {"limit": 5000, "used": 0, "remaining": 5000, "reset": 0}
            self._out(
                {"resources": {"core": bucket, "graphql": bucket}, "rate": bucket}, jq
            )
            return "read"
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "repos":
            self.fail('gh: Not Found (HTTP 404)\n{"message":"Not Found"}')
        name = f"{parts[1]}/{parts[2]}"
        repo = self.load(name)
        rest = parts[3:]
        if method != "GET":
            return self._api_write(repo, rest, method, opts)
        if not rest:
            self._out(
                {
                    "full_name": repo["repo"],
                    "name": parts[2],
                    "owner": {"login": parts[1]},
                    "description": repo["description"],
                    "default_branch": repo["default_branch"],
                    "open_issues_count": sum(
                        1 for i in repo["issues"] if i["state"] == "OPEN"
                    ),
                    "html_url": f"https://github.com/{repo['repo']}",
                },
                jq,
            )
        elif rest == ["labels"]:
            self._out(repo["labels"], jq)
        elif rest == ["issues"]:
            state = params.get("state", "open").upper()
            found = [
                i
                for i in sorted(repo["issues"], key=lambda i: -i["number"])
                if state == "ALL" or i["state"] == state
            ]
            self._out([self.rest_issue(repo, i) for i in found], jq)
        elif len(rest) == 2 and rest[0] == "issues":
            self._out(self.rest_issue(repo, self.issue(repo, rest[1])), jq)
        elif len(rest) == 3 and rest[0] == "issues" and rest[2] == "comments":
            issue = self.issue(repo, rest[1])
            self._out(
                [
                    {
                        "user": {"login": c["author"]},
                        "body": c["body"],
                        "created_at": c["createdAt"],
                    }
                    for c in issue["comments"]
                ],
                jq,
            )
        else:
            self.fail(
                f"gh stand-in: `{endpoint}` is not available offline (HTTP 404).",
                "unsupported",
            )
        return "read"

    def _api_write(
        self, repo: Dict[str, Any], rest: List[str], method: str, opts: Dict[str, Any]
    ) -> str:
        if len(rest) >= 3 and rest[0] == "issues" and rest[2] == "labels":
            issue = self.issue(repo, rest[1])
            known = {label["name"] for label in repo["labels"]}
            if method == "POST" and len(rest) == 3:
                names = [
                    f.partition("=")[2]
                    for f in opts.get("_fields", [])
                    if f.startswith("labels")
                ]
                missing = [n for n in names if n not in known]
                if missing:
                    self.fail(f"gh: Label does not exist: {missing[0]} (HTTP 422)")
                issue["labels"] += [n for n in names if n not in issue["labels"]]
            elif method == "DELETE" and len(rest) == 4:
                issue["labels"] = [n for n in issue["labels"] if n != rest[3]]
            else:
                self.fail(FORBIDDEN, "blocked_write")
            self.store(repo["repo"], repo)
            self._out([{"name": n} for n in issue["labels"]], None)
            return "applied_write"
        self.fail(FORBIDDEN, "blocked_write")

    def _out(self, data: Any, jq: Optional[str]) -> None:
        if jq is not None:
            self.jq(data, jq)
        else:
            print(json.dumps(data))


_API_VALUE_FLAGS = {
    "-X",
    "--method",
    "-f",
    "-F",
    "--field",
    "--raw-field",
    "-H",
    "--header",
    "-q",
    "--jq",
    "--input",
    "-t",
    "--template",
    "--cache",
    "-p",
    "--preview",
    "--hostname",
}
_API_BOOL_FLAGS = {"--paginate", "-i", "--include", "--silent", "--verbose", "--slurp"}


def _parse(
    args: List[str], value_flags: set, bool_flags: set
) -> Tuple[List[str], Dict[str, Any]]:
    """Positionals, and flag values (the last one wins; ``-f`` fields accumulate)."""
    positional: List[str] = []
    opts: Dict[str, Any] = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("-") and arg != "-":
            flag, eq, inline = arg.partition("=")
            if flag in value_flags:
                following = args[i + 1] if i + 1 < len(args) else ""
                takes_next = not eq and not (
                    following.startswith("-") and following != "-"
                )
                value = inline if eq else (following if takes_next else "")
                i += 1 if takes_next else 0
                if flag in ("-f", "-F", "--field", "--raw-field"):
                    opts.setdefault("_fields", []).append(value)
                opts[flag] = value
            elif flag in bool_flags:
                opts[flag] = True
            else:
                opts.setdefault("_unknown", []).append(flag)
        else:
            positional.append(arg)
        i += 1
    return positional, opts


def _json_flag(opts: Dict[str, Any]) -> Optional[str]:
    """``--json`` as given: ``None`` when absent, ``""`` when it has no fields."""
    if "--json" not in opts:
        return None
    value = opts["--json"]
    return "" if value.startswith("-") else value


def _names(value: Optional[str]) -> List[str]:
    return [n.strip() for n in (value or "").split(",") if n.strip()]


def _api_method(args: List[str]) -> str:
    _, opts = _parse(args, _API_VALUE_FLAGS, _API_BOOL_FLAGS)
    method = opts.get("-X") or opts.get("--method")
    if method:
        return str(method).upper()
    return "POST" if opts.get("_fields") or opts.get("--input") else "GET"


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    state = os.environ.get(STATE_ENV)
    if not state or not Path(state).is_file():
        sys.stderr.write(
            "gh stand-in: no task state (GAIA_BENCH_GH_STATE is unset or missing). "
            "It only runs inside a benchmark task.\n"
        )
        return 1
    stub = Stub(Path(state))
    try:
        action, rc = stub.run(args), 0
    except _Exit as exc:
        action, rc = exc.action, exc.code
    stub.log(args, action, rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
