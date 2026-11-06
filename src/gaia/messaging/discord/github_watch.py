# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tell the paired user, right away, when someone else acts on a watched repo.

A deterministic poller, not a prompt: every few minutes the bridge asks GitHub
what was opened or commented on since the last check. Anything from an account
the user named as their own, or from a bot, is left for their summary schedule.
Everything else (a third-party developer's new PR, issue or comment) is posted
to the DM immediately, with its link.

No model is involved, so nothing is invented, missed or repeated: each item is
keyed by its GitHub id and reported once. A repo's first check only records
where to start; it never floods the DM with history.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from gaia.logger import get_logger
from gaia.messaging.discord import state

log = get_logger(__name__)

API = "https://api.github.com"
#: With a token GitHub allows 5000 requests an hour; each check costs 3.
POLL_SECONDS_AUTHENTICATED = 120.0
#: Without one it allows 60 an hour; at 3 requests a check, 10 minutes per
#: watched repo stays well under that however many repos are watched.
POLL_SECONDS_ANONYMOUS_PER_REPO = 600.0
#: Consecutive failed checks before the user is told the watch is not working.
FAILURES_BEFORE_ALERT = 3
MAX_WATCHES = 5
MAX_ITEMS_PER_POST = 10
_SEEN_KEEP = 2000


class WatchError(ValueError):
    """A watch request the user can fix; the message says how."""


@dataclass
class Watch:
    repo: str
    own_accounts: List[str] = field(default_factory=list)
    #: ISO time of the last check; GitHub's ``since`` filter starts here.
    since: str = ""
    seen: List[str] = field(default_factory=list)
    last_checked: float = 0.0
    #: Consecutive failed checks, and why the last one failed.
    failures: int = 0
    last_error: str = ""


@dataclass
class Item:
    key: str
    kind: str
    number: int
    title: str
    author: str
    url: str
    excerpt: str = ""


def path():
    return state.discord_dir() / "watches.json"


def load() -> List[Watch]:
    data = state.read_json_or_set_aside(path(), "GitHub watches")
    if data is None:
        return []
    try:
        return [Watch(**w) for w in data.get("watches", [])]
    except TypeError as e:
        raise state.BridgeStateError(
            f"{path()} is not usable: {e}. Delete it to clear your watches."
        ) from e


def save(items: List[Watch]) -> None:
    state.write_json(path(), {"watches": [asdict(w) for w in items]})


def normalize_repo(repo: str) -> str:
    repo = repo.strip().removeprefix("https://").removeprefix("github.com/")
    repo = repo.strip("/")
    parts = repo.split("/")
    if len(parts) != 2 or not all(parts):
        raise WatchError(
            f"{repo!r} is not a GitHub repository. Use owner/name, like amd/gaia."
        )
    return repo.lower()


def _locked():
    from gaia.utils.file_lock import file_lock

    return file_lock(path())


def check_repo(repo: str, token: Optional[str]) -> None:
    """Refuse a repository GitHub will not show, so a typo is not a silent watch.

    Raises:
        WatchError: GitHub has no such repository, or it is private and this
            PC's GitHub login cannot see it.
        RuntimeError: GitHub could not be reached.
    """
    import httpx

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "GAIA-discord-watch",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(f"{API}/repos/{repo}", headers=headers, timeout=30)
    except httpx.HTTPError as e:
        raise RuntimeError(
            f"Could not reach GitHub to check {repo}: {e}. Try again when online."
        ) from e
    if response.status_code == 404:
        hint = (
            ""
            if token
            else ", or sign in to the GitHub CLI on this PC if it is private"
        )
        raise WatchError(f"GitHub shows no repository {repo}. Check the name{hint}.")
    response.raise_for_status()


def add(repo: str, own_accounts: List[str]) -> Watch:
    """Watch *repo*, or update which accounts count as the user's own."""
    repo = normalize_repo(repo)
    own = sorted({a.strip().lstrip("@").lower() for a in own_accounts if a.strip()})
    with _locked():
        return _add(repo, own)


def _add(repo: str, own: List[str]) -> Watch:
    items = load()
    for existing in items:
        if existing.repo == repo:
            existing.own_accounts = own
            save(items)
            return existing
    if len(items) >= MAX_WATCHES:
        raise WatchError(
            f"Already watching {MAX_WATCHES} repositories. Stop one first."
        )
    watch = Watch(repo=repo, own_accounts=own)
    items.append(watch)
    save(items)
    return watch


def remove(repo: str) -> bool:
    repo = normalize_repo(repo)
    with _locked():
        items = load()
        kept = [w for w in items if w.repo != repo]
        save(kept)
    return len(kept) != len(items)


def github_token() -> Optional[str]:
    """``GITHUB_TOKEN``, else the gh CLI's login, else ``None`` (anonymous)."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv
            [gh, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,  # a nonzero exit means "not logged in", handled below
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("Could not ask gh for its token, watching anonymously: %s", e)
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def is_bot(user: Dict[str, Any]) -> bool:
    login = str(user.get("login") or "")
    return user.get("type") == "Bot" or login.endswith("[bot]")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text: str) -> float:
    return (
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def _excerpt(body: Optional[str]) -> str:
    text = " ".join((body or "").split())
    return text if len(text) <= 180 else text[:180] + "…"


def third_party_items(
    watch: Watch,
    issues: List[Dict[str, Any]],
    comments: List[Dict[str, Any]],
    review_comments: List[Dict[str, Any]],
) -> List[Item]:
    """What someone other than the user or a bot did since the last check."""
    since = _parse_iso(watch.since)
    own = set(watch.own_accounts)
    seen = set(watch.seen)
    titles = {int(i["number"]): str(i.get("title") or "") for i in issues}
    out: List[Item] = []

    def keep(user: Dict[str, Any], created: str, key: str) -> bool:
        login = str(user.get("login") or "").lower()
        return (
            key not in seen
            and not is_bot(user)
            and login not in own
            and _parse_iso(created) > since
        )

    for issue in issues:
        key = f"issue:{issue['id']}"
        if keep(issue.get("user") or {}, issue["created_at"], key):
            out.append(
                Item(
                    key,
                    # GitHub marks a PR with this key; its value can be empty.
                    "PR" if "pull_request" in issue else "issue",
                    int(issue["number"]),
                    str(issue.get("title") or ""),
                    issue["user"]["login"],
                    issue["html_url"],
                )
            )
    for comment, kind in [(c, "comment") for c in comments] + [
        (c, "review comment") for c in review_comments
    ]:
        key = f"{kind}:{comment['id']}"
        if not keep(comment.get("user") or {}, comment["created_at"], key):
            continue
        url = str(comment.get("issue_url") or comment.get("pull_request_url") or "")
        number = int(url.rstrip("/").rsplit("/", 1)[-1]) if url else 0
        out.append(
            Item(
                key,
                kind,
                number,
                titles.get(number, ""),
                comment["user"]["login"],
                comment["html_url"],
                _excerpt(comment.get("body")),
            )
        )
    return out


_MARKDOWN = re.compile(r"([\\`*_~|\[\]()<>#-])")


def escape_markdown(text: str) -> str:
    """Show GitHub text literally: a title must not render as a masked link."""
    return _MARKDOWN.sub(r"\\\1", text)


def format_items(repo: str, items: List[Item]) -> str:
    lines = [f"👤 **Third-party activity on {repo}**"]
    for item in items[:MAX_ITEMS_PER_POST]:
        what = (
            f"opened {item.kind} #{item.number}"
            if item.kind in ("PR", "issue")
            else f"left a {item.kind} on #{item.number}"
        )
        title = f" — {escape_markdown(item.title)}" if item.title else ""
        author = escape_markdown(item.author)
        lines.append(f"• **{author}** {what}{title}\n  <{item.url}>")
        if item.excerpt:
            lines.append(f"  > {escape_markdown(item.excerpt)}")
    if len(items) > MAX_ITEMS_PER_POST:
        lines.append(f"-# …and {len(items) - MAX_ITEMS_PER_POST} more on GitHub.")
    return "\n".join(lines)


class Poller:
    """Checks each watched repo on its own interval and reports news."""

    def __init__(
        self,
        post: Callable[[str], None],
        *,
        fetch: Optional[
            Callable[[str, Dict[str, str], Optional[str]], List[Any]]
        ] = None,
        token: Callable[[], Optional[str]] = github_token,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._post = post
        self._fetch = fetch or _fetch
        self._token_source = token
        self._token: Optional[str] = None
        self._token_resolved = False
        self._clock = clock

    def interval(self, watch_count: int = 1) -> float:
        if self._token:
            return POLL_SECONDS_AUTHENTICATED
        return POLL_SECONDS_ANONYMOUS_PER_REPO * max(1, watch_count)

    def poll(self) -> None:
        """Check every watch that is due; report news and lasting failures.

        The network work runs without the file lock; the results are merged back
        under it into whatever the file holds by then, so a watch added or
        removed meanwhile (by the agent running ``gaia discord``) survives.
        """
        watches = load()
        if watches and not self._token_resolved:
            # Looked up when there is first something to watch, not when the
            # bridge starts: a watch is usually added after that.
            self._token = self._token_source()
            self._token_resolved = True
        now = self._clock()
        interval = self.interval(len(watches))
        results: Dict[str, Watch] = {}
        for watch in watches:
            if now - watch.last_checked < interval:
                continue
            results[watch.repo] = self._check_one(watch, now)
        if not results:
            return
        with _locked():
            current = load()
            for i, watch in enumerate(current):
                done = results.get(watch.repo)
                if done is not None:
                    done.own_accounts = watch.own_accounts
                    current[i] = done
            save(current)

    def _check_one(self, watch: Watch, now: float) -> Watch:
        watch.last_checked = now
        if not watch.since:
            # First look: start from now rather than replay the backlog.
            watch.since = _iso(now)
            return watch
        try:
            items = self._check(watch)
        except Exception as e:  # noqa: BLE001 - reported to the user after repeats
            watch.failures += 1
            watch.last_error = str(e)
            log.warning(
                "GitHub check of %s failed (%d): %s", watch.repo, watch.failures, e
            )
            if watch.failures == FAILURES_BEFORE_ALERT:
                self._post(
                    f"⚠️ I couldn't check **{watch.repo}** the last "
                    f"{FAILURES_BEFORE_ALERT} times, so you may be missing alerts: "
                    f"{escape_markdown(watch.last_error)}\n-# I'll keep trying and "
                    "tell you when it works again."
                )
            return watch
        if watch.failures >= FAILURES_BEFORE_ALERT:
            self._post(f"✅ Checking **{watch.repo}** again.")
        watch.failures = 0
        watch.last_error = ""
        if items:
            self._post(format_items(watch.repo, items))
            watch.seen = (watch.seen + [i.key for i in items])[-_SEEN_KEEP:]
        # Overlap by a minute: GitHub's ``since`` is inclusive and its clocks
        # drift; ``seen`` stops the overlap from repeating anything.
        watch.since = _iso(now - 60)
        return watch

    def _check(self, watch: Watch) -> List[Item]:
        params = {"since": watch.since, "per_page": "100"}
        base = f"{API}/repos/{watch.repo}"
        issues = self._fetch(
            f"{base}/issues", {**params, "state": "all", "sort": "updated"}, self._token
        )
        comments = self._fetch(f"{base}/issues/comments", params, self._token)
        reviews = self._fetch(f"{base}/pulls/comments", params, self._token)
        return third_party_items(watch, issues, comments, reviews)


#: A run that ended these ways says nothing about the branch's health.
_NOT_A_VERDICT = frozenset(
    {"cancelled", "skipped", "neutral", "stale", "action_required"}
)


def ci_failures(repo: str, branch: str, token: Optional[str]) -> List[str]:
    """Workflows whose latest finished run on *branch* failed, sorted.

    Only pushes to the branch in *repo* itself: a fork's PR from its own
    ``main`` is not this repo's main. Runs are grouped by workflow, not by run
    name, which a workflow can set per run. Cancelled and skipped runs are not
    a verdict, so a push that cancels a run never reads as a break or a fix.
    """
    import httpx

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "GAIA-discord-watch",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = httpx.get(
        f"{API}/repos/{normalize_repo(repo)}/actions/runs",
        params={
            "branch": branch,
            "event": "push",
            "status": "completed",
            "per_page": "100",
        },
        headers=headers,
        timeout=30,
    )
    if response.status_code in (403, 429):
        raise RuntimeError(
            f"GitHub rate-limited the CI check (HTTP {response.status_code}). Sign "
            "in to the gh CLI so GAIA checks with your account's higher limit."
        )
    response.raise_for_status()
    full_name = normalize_repo(repo).lower()
    latest: Dict[Any, Dict[str, Any]] = {}
    for run in response.json().get("workflow_runs", []):
        if run.get("conclusion") in _NOT_A_VERDICT:
            continue
        head = (run.get("head_repository") or {}).get("full_name") or full_name
        if str(head).lower() != full_name:
            continue
        key = run.get("workflow_id") or run.get("path") or run.get("name")
        if key not in latest or run.get("created_at", "") > latest[key].get(
            "created_at", ""
        ):
            latest[key] = run
    return sorted(
        {
            str(run.get("name") or run.get("path") or "workflow")
            for run in latest.values()
            if run.get("conclusion") != "success"
        }
    )


def _fetch(url: str, params: Dict[str, str], token: Optional[str]) -> List[Any]:
    import httpx

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "GAIA-discord-watch",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = httpx.get(url, params=params, headers=headers, timeout=30)
    if response.status_code in (403, 429):
        raise RuntimeError(
            f"GitHub rate-limited the check (HTTP {response.status_code}); it "
            "retries on the next interval."
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise RuntimeError(f"GitHub answered {url} with something other than a list")
    return data
