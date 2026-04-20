# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``OSSReuseMixin`` — license-aware reuse of external code (§5.3, §5.4).

The four tools in §15.2 of ``docs/plans/coder-agent.mdx`` form a small DAG:

* :func:`gh_search_code` / :func:`gh_search_repos` — discovery against
  GitHub's code-search and repo-search APIs, with a **hard** license filter
  that drops GPL/AGPL/LGPL/SSPL/proprietary before the LLM ever sees the
  results.
* :func:`vet_license` — sync SPDX check against a given repo; returns a
  structured :class:`LicenseReport` the loop can cite.
* :func:`import_with_attribution` — the only tool that writes to the repo.
  Four guarantees, all baked in at the tool layer (§5.4):

  1. The source license is compatible (else :class:`LicenseIncompatibleError`).
  2. The source is pinned to a commit SHA, never a branch.
  3. The imported file gets a verbatim header
     ``# Adapted from <repo> @ <sha> — <license>``.
  4. ``THIRD_PARTY_NOTICES.md`` at repo root gets an append-only entry with
     URL, SHA, license, and ISO-8601 date.

Everything downstream — Pass 5 prose linter, the EM audit trail, the
``coder`` → ``main`` integration PR — depends on those four facts holding.
The tool refuses to proceed if any is missing, because a silent degradation
would violate principle #3 (fail-loudly).

Network boundary
----------------
All network I/O is funnelled through :func:`_gh_api` (for GitHub API calls)
and :func:`_fetch_raw` (for ``raw.githubusercontent.com``). Tests mock those
two functions and never hit the wire. This matches the task directive
"Perform real ``gh api`` calls in tests — mock the subprocess boundary."
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, TypedDict

from gaia.agents.base.tools import tool
from gaia.coder.tools.github import _run_gh

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SPDX allowlist / denylist
# ---------------------------------------------------------------------------

#: Permissive licenses the coder may vendor or fork-and-modify. Pinned to
#: SPDX identifiers so comparisons are exact.
PERMISSIVE_LICENSES: frozenset[str] = frozenset(
    {
        "MIT",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
        "ISC",
        "Unlicense",
        "0BSD",
    }
)

#: Copyleft / proprietary licenses that always fail the check.
BLOCKED_LICENSES: frozenset[str] = frozenset(
    {
        "GPL-2.0",
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "AGPL-3.0",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "LGPL-2.1",
        "LGPL-2.1-only",
        "LGPL-2.1-or-later",
        "LGPL-3.0",
        "LGPL-3.0-only",
        "LGPL-3.0-or-later",
        "SSPL-1.0",
        "BUSL-1.1",
    }
)


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


class CodeHit(TypedDict):
    """One result from :func:`gh_search_code`."""

    repository: str  # "owner/name"
    path: str
    url: str
    license: Optional[str]  # SPDX id if GitHub knows it


class RepoHit(TypedDict):
    """One result from :func:`gh_search_repos`."""

    repository: str
    description: str
    stars: int
    url: str
    license: Optional[str]


@dataclass
class LicenseReport:
    """Outcome of :func:`vet_license` — what the loop cites in plans / PRs."""

    repository: str
    license: Optional[str]
    compatible: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "repository": self.repository,
            "license": self.license,
            "compatible": self.compatible,
            "reason": self.reason,
        }


class ImportResult(TypedDict):
    """Result of :func:`import_with_attribution`."""

    dest_path: str
    license: str
    source_url: str
    commit_sha: str
    notices_entry_added: bool


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LicenseIncompatibleError(RuntimeError):
    """Raised when a source's license is not in :data:`PERMISSIVE_LICENSES`.

    The exception carries the SPDX id so the caller can route the failure
    (open an issue for human review per §5.4, log to the audit trail, etc.).
    """

    def __init__(self, repository: str, license_id: Optional[str]) -> None:
        self.repository = repository
        self.license_id = license_id
        super().__init__(
            f"license incompatible for {repository!r}: "
            f"{license_id or '<unknown>'} is not in PERMISSIVE_LICENSES"
        )


class AttributionError(RuntimeError):
    """Raised when :func:`import_with_attribution` cannot uphold one of the
    four §5.4 guarantees (missing SHA, missing notices file, etc.)."""


# ---------------------------------------------------------------------------
# Network boundary — mockable in tests
# ---------------------------------------------------------------------------


def _gh_api(path: str, *, method: str = "GET") -> dict:
    """Invoke ``gh api`` and parse the JSON response.

    Centralising the shell-out here means tests replace exactly one
    function. Non-zero exit from ``gh api`` surfaces through
    :class:`gaia.coder.tools.github.GitHubCLIError` without retyping.
    """
    argv = ["api", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
    raw = _run_gh(argv, timeout_s=60)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"gh api returned non-JSON ({path}): {e.msg}") from e


def _fetch_raw(url: str) -> str:
    """Fetch a ``raw.githubusercontent.com`` URL, returning its text body.

    Uses the :mod:`urllib` stdlib so there's no third-party HTTP dep. Tests
    mock this function rather than :mod:`urllib`.
    """
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "gaia-coder (amd/gaia)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class OSSReuseMixin:
    """Mixin providing the four OSS-reuse tools from §15.2 of the coder plan.

    Default license filter is the full :data:`PERMISSIVE_LICENSES` set. A
    caller can pass a narrower list but **cannot** widen it — attempts to
    add blocked licenses raise :class:`LicenseIncompatibleError` at
    parse-time, before any network call.
    """

    def register_oss_reuse_tools(self) -> None:
        """Register the four tools in the agent tool registry."""

        @tool
        def gh_search_code(
            query: str,
            language: Optional[str] = None,
            license_filter: Optional[List[str]] = None,
            limit: int = 20,
        ) -> List[CodeHit]:
            """Search GitHub code for ``query`` with a hard license filter.

            The filter defaults to :data:`PERMISSIVE_LICENSES`. Results
            whose repository license is absent or not in the filter are
            dropped *before* returning — the LLM never sees a GPL hit.
            """
            allowed = _validate_license_filter(license_filter)
            # GitHub's code-search API does not support license:= qualifiers,
            # so we filter client-side after fetching repo metadata.
            q_parts = [query]
            if language:
                q_parts.append(f"language:{language}")
            q = " ".join(q_parts)
            params = f"/search/code?q={_urlencode(q)}&per_page={limit}"
            payload = _gh_api(params)
            hits: List[CodeHit] = []
            for item in payload.get("items", []):
                repo_full = item["repository"]["full_name"]
                lic = _lookup_repo_license(repo_full)
                if lic not in allowed:
                    continue
                hits.append(
                    CodeHit(
                        repository=repo_full,
                        path=item["path"],
                        url=item["html_url"],
                        license=lic,
                    )
                )
            return hits

        @tool
        def gh_search_repos(
            query: str,
            license_filter: Optional[List[str]] = None,
            min_stars: int = 0,
            limit: int = 20,
        ) -> List[RepoHit]:
            """Search GitHub repositories with a hard license filter.

            Unlike code-search, the repos endpoint *does* support a
            ``license:<key>`` qualifier, so the filter is applied
            server-side *and* double-checked client-side.
            """
            allowed = _validate_license_filter(license_filter)
            q_parts = [query]
            for spdx in allowed:
                q_parts.append(f"license:{_spdx_to_gh_key(spdx)}")
            if min_stars > 0:
                q_parts.append(f"stars:>={min_stars}")
            q = " ".join(q_parts)
            params = f"/search/repositories?q={_urlencode(q)}&per_page={limit}"
            payload = _gh_api(params)
            hits: List[RepoHit] = []
            for item in payload.get("items", []):
                lic_obj = item.get("license") or {}
                lic = lic_obj.get("spdx_id") if lic_obj else None
                # Double-check client-side — defence in depth.
                if lic not in allowed:
                    continue
                hits.append(
                    RepoHit(
                        repository=item["full_name"],
                        description=item.get("description") or "",
                        stars=item.get("stargazers_count", 0),
                        url=item["html_url"],
                        license=lic,
                    )
                )
            return hits

        @tool
        def vet_license(repo: str) -> LicenseReport:
            """Sync SPDX check for ``repo`` (``owner/name``).

            Returns a structured :class:`LicenseReport` regardless of
            outcome — the loop uses it in plan docs and PR bodies. The
            caller decides whether a ``compatible=False`` result is a hard
            block (`import_with_attribution`) or an advisory (search).
            """
            spdx = _lookup_repo_license(repo)
            if spdx is None:
                return LicenseReport(
                    repository=repo,
                    license=None,
                    compatible=False,
                    reason="no LICENSE file recognised by GitHub",
                )
            if spdx in BLOCKED_LICENSES:
                return LicenseReport(
                    repository=repo,
                    license=spdx,
                    compatible=False,
                    reason=f"{spdx} is copyleft/proprietary; blocked by policy (§5.4)",
                )
            if spdx in PERMISSIVE_LICENSES:
                return LicenseReport(
                    repository=repo,
                    license=spdx,
                    compatible=True,
                    reason=f"{spdx} is on the permissive allowlist",
                )
            return LicenseReport(
                repository=repo,
                license=spdx,
                compatible=False,
                reason=(
                    f"{spdx} is neither permissive nor explicitly blocked; "
                    "human review required"
                ),
            )

        @tool
        def import_with_attribution(
            source_url: str,
            commit_sha: str,
            dest_path: str,
            attribution_note: str = "",
            repo_root: Optional[str] = None,
        ) -> ImportResult:
            """Vendor one file from ``source_url`` into ``dest_path`` with attribution.

            Applies the four §5.4 guarantees. Raises on any failure to
            meet them (fail-loudly) — there is no silent fallback.

            Args:
                source_url: GitHub URL to the source file. Must match one of
                    ``https://github.com/OWNER/NAME/blob/SHA/PATH`` or
                    ``https://raw.githubusercontent.com/OWNER/NAME/SHA/PATH``.
                commit_sha: The SHA to pin against. Must match the SHA in
                    ``source_url``. Pinning to a branch name raises.
                dest_path: Destination path inside the repo, relative to
                    ``repo_root``.
                attribution_note: Optional extra sentence appended to the
                    header and the notices entry.
                repo_root: Repo root (default CWD). Tests pass ``tmp_path``.
            """
            owner_name, branch_or_sha, src_path = _parse_source_url(source_url)
            if branch_or_sha != commit_sha:
                raise AttributionError(
                    f"source_url references {branch_or_sha!r} but commit_sha is "
                    f"{commit_sha!r}; provenance must match (§5.4 rule 3)"
                )
            if not _looks_like_sha(commit_sha):
                raise AttributionError(
                    f"commit_sha {commit_sha!r} does not look like a 40-char "
                    "(or 7+) hex SHA; branch pins are forbidden (§5.4 rule 3)"
                )
            lic = _lookup_repo_license(owner_name)
            if lic is None or lic not in PERMISSIVE_LICENSES:
                raise LicenseIncompatibleError(owner_name, lic)
            root = Path(repo_root) if repo_root else Path.cwd()
            dest = root / dest_path
            dest.parent.mkdir(parents=True, exist_ok=True)

            raw_url = _to_raw_url(owner_name, commit_sha, src_path)
            body = _fetch_raw(raw_url)
            header = _attribution_header(
                repo=owner_name,
                sha=commit_sha,
                license_id=lic,
                note=attribution_note,
                path=dest_path,
            )
            dest.write_text(header + body, encoding="utf-8")

            notices_added = _append_notices_entry(
                root=root,
                repo=owner_name,
                sha=commit_sha,
                license_id=lic,
                source_url=source_url,
                dest_path=dest_path,
                note=attribution_note,
            )
            return ImportResult(
                dest_path=dest.as_posix(),
                license=lic,
                source_url=source_url,
                commit_sha=commit_sha,
                notices_entry_added=notices_added,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_license_filter(
    license_filter: Optional[Sequence[str]],
) -> frozenset[str]:
    """Coerce the user-supplied filter to a frozenset; reject any blocked entry."""
    if license_filter is None:
        return PERMISSIVE_LICENSES
    requested = frozenset(license_filter)
    bad = requested & BLOCKED_LICENSES
    if bad:
        raise LicenseIncompatibleError("<filter>", ",".join(sorted(bad)))
    # Silently drop entries that aren't permissive — we never widen.
    return requested & PERMISSIVE_LICENSES


def _urlencode(text: str) -> str:
    """URL-encode a query string for ``gh api /search/…``."""
    import urllib.parse

    return urllib.parse.quote(text)


def _spdx_to_gh_key(spdx: str) -> str:
    """Translate an SPDX id to the lowercase key ``license:<key>`` expects.

    GitHub's search API uses its own ``license_keys`` table (e.g. ``apache-2.0``),
    which is mostly SPDX-lowercased with a few exceptions. We do the exact
    mapping for the seven allowed ids rather than a blanket ``lower()`` to
    protect against future SPDX additions that don't round-trip.
    """
    table = {
        "MIT": "mit",
        "BSD-2-Clause": "bsd-2-clause",
        "BSD-3-Clause": "bsd-3-clause",
        "Apache-2.0": "apache-2.0",
        "ISC": "isc",
        "Unlicense": "unlicense",
        "0BSD": "0bsd",
    }
    if spdx not in table:
        raise KeyError(f"no GitHub search key for SPDX id {spdx!r}")
    return table[spdx]


def _lookup_repo_license(repo: str) -> Optional[str]:
    """Query ``gh api /repos/<repo>`` and return the SPDX id (or ``None``).

    GitHub's ``/repos/{owner}/{repo}`` endpoint returns
    ``license.spdx_id``; a missing or ``"NOASSERTION"`` value becomes
    ``None``.
    """
    payload = _gh_api(f"/repos/{repo}")
    lic = payload.get("license") or {}
    spdx = lic.get("spdx_id")
    if not spdx or spdx == "NOASSERTION":
        return None
    return spdx


def _parse_source_url(url: str) -> tuple[str, str, str]:
    """Return ``(owner/name, branch_or_sha, path)`` from a GitHub file URL.

    Accepts both ``github.com/OWNER/NAME/blob/SHA/PATH`` and
    ``raw.githubusercontent.com/OWNER/NAME/SHA/PATH``. Any other shape
    raises :class:`AttributionError`.
    """
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc
    segs = parsed.path.lstrip("/").split("/")
    if host == "github.com" and len(segs) >= 5 and segs[2] == "blob":
        owner, name = segs[0], segs[1]
        sha_or_branch = segs[3]
        path = "/".join(segs[4:])
        return f"{owner}/{name}", sha_or_branch, path
    if host == "raw.githubusercontent.com" and len(segs) >= 4:
        owner, name, sha_or_branch = segs[0], segs[1], segs[2]
        path = "/".join(segs[3:])
        return f"{owner}/{name}", sha_or_branch, path
    raise AttributionError(
        f"cannot parse GitHub source URL: {url!r} "
        "(expected github.com/<owner>/<name>/blob/<sha>/<path> or "
        "raw.githubusercontent.com/<owner>/<name>/<sha>/<path>)"
    )


def _to_raw_url(repo: str, sha: str, path: str) -> str:
    """Build the ``raw.githubusercontent.com`` URL we'll actually fetch."""
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{path}"


def _looks_like_sha(candidate: str) -> bool:
    """Cheap heuristic: 7+ hex chars, no slashes, no word chars outside hex."""
    if len(candidate) < 7:
        return False
    allowed = set("0123456789abcdefABCDEF")
    return all(ch in allowed for ch in candidate)


def _attribution_header(
    *, repo: str, sha: str, license_id: str, note: str, path: str
) -> str:
    """Return the verbatim §5.4 header for a vendored file.

    Python-style ``#`` comments — we vendor into ``src/gaia/`` which is
    Python-only. Future multi-language support will branch on suffix.
    """
    del path  # reserved for future non-Python headers
    lines = [
        f"# Adapted from {repo} @ {sha} — {license_id}",
    ]
    if note:
        lines.append(f"# {note}")
    lines.append("")  # blank line before the original file body
    return "\n".join(lines) + "\n"


def _append_notices_entry(
    *,
    root: Path,
    repo: str,
    sha: str,
    license_id: str,
    source_url: str,
    dest_path: str,
    note: str,
) -> bool:
    """Append an entry to ``THIRD_PARTY_NOTICES.md``.

    Creates the file with a short header if it is missing. Returns
    ``True`` if the entry was actually written (always true on success;
    the return value exists so callers don't have to re-read the file to
    confirm).
    """
    notices = root / "THIRD_PARTY_NOTICES.md"
    if not notices.exists():
        notices.write_text(
            "# Third-Party Notices\n\n"
            "Every entry below was vendored by `gaia-coder` with "
            "`OSSReuseMixin.import_with_attribution` (§5.4).\n\n",
            encoding="utf-8",
        )
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    block_lines = [
        f"## `{dest_path}`",
        "",
        f"- Source: {source_url}",
        f"- Repository: `{repo}`",
        f"- Commit: `{sha}`",
        f"- License: `{license_id}`",
        f"- Imported: {date}",
    ]
    if note:
        block_lines.append(f"- Note: {note}")
    block_lines.append("")  # trailing blank
    with notices.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(block_lines) + "\n")
    return True


__all__ = [
    "AttributionError",
    "BLOCKED_LICENSES",
    "CodeHit",
    "ImportResult",
    "LicenseIncompatibleError",
    "LicenseReport",
    "OSSReuseMixin",
    "PERMISSIVE_LICENSES",
    "RepoHit",
]
