#!/usr/bin/env python3
"""Survey a GitHub repository before building its dashboard.

Answers the questions the build depends on: can this token read every source,
how big is each history, which workflow runs the tests and with what runner,
which events run CI on the default branch, which registry packages and other
install channels (Homebrew, Docker Hub, RubyGems, NuGet, conda-forge) exist,
and which release tags and files are published. Writes a JSON report that
scaffold.py turns into src/config.ts.

Auth: the GITHUB_TOKEN environment variable (`GITHUB_TOKEN="$(gh auth token)"`
reuses a logged-in GitHub CLI). Requests go straight to the REST API with urllib.
Read-only; makes about 50-150 requests (more for a busy CI or many releases).
Needs Python 3.11 or later (tomllib).

    python probe_repo.py owner/name [--out probe.json]
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    print(
        f"Python 3.11 or later is required (found {sys.version_info[0]}.{sys.version_info[1]}).",
        file=sys.stderr,
    )
    sys.exit(2)

import argparse
import base64
import http.client
import json
import os
import re
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"
RELEASE_PAGE_CAP = 20
# Events GitHub can attach to a workflow run, asked about one by one when the default branch has too many runs to list.
KNOWN_EVENTS = (
    "push",
    "schedule",
    "pull_request",
    "pull_request_target",
    "workflow_dispatch",
    "workflow_run",
    "release",
    "repository_dispatch",
    "dynamic",
    "issue_comment",
    "issues",
    "create",
    "delete",
    "check_suite",
    "check_run",
    "deployment",
    "deployment_status",
    "page_build",
    "registry_package",
    "status",
    "workflow_call",
)


class NetworkError(ConnectionError):
    """A request that failed at the network level three times. An OSError, so registry lookups can catch it."""


class ApiError(Exception):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"GET {url} returned HTTP {status}: {body[:200]}")
        self.status = status


TOKEN = os.environ.get("GITHUB_TOKEN")


def get(
    url: str, accept: str = "application/vnd.github+json"
) -> tuple[object, dict[str, str]]:
    """GET a URL; returns (json, lower-cased headers). Raises ApiError on HTTP errors."""
    if not url.startswith("http"):
        url = API + url
    headers = {"Accept": accept, "User-Agent": "oss-dashboard-probe"}
    if TOKEN and url.startswith(API):
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    # A dropped connection or a 502/503/504 is retried twice; anything else, or a third failure, is reported.
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                body = res.read().decode("utf-8")
                hdrs = {k.lower(): v for k, v in res.headers.items()}
                return (json.loads(body) if body.strip() else None), hdrs
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise ApiError(e.code, url, e.read().decode("utf-8", "replace")) from e
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            ConnectionError,
            TimeoutError,
        ) as e:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise NetworkError(
                f"probe: GET {url} failed three times ({e}); check the network or proxy and run the probe again."
            ) from e
    raise AssertionError("unreachable")


def next_link(link: str | None) -> str | None:
    for part in (link or "").split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip()[1:-1]
    return None


def last_page(link: str | None) -> int | None:
    m = re.search(r'[?&]page=(\d+)[^>]*>;\s*rel="last"', link or "")
    return int(m.group(1)) if m else None


def count_via_link(path: str) -> int:
    """Item count of a list endpoint, from the last page number at per_page=1."""
    sep = "&" if "?" in path else "?"
    data, h = get(f"{path}{sep}per_page=1")
    return last_page(h.get("link")) or len(data or [])


def file_text(repo: str, path: str, ref: str) -> str | None:
    try:
        data, _ = get(f"/repos/{repo}/contents/{path}?ref={ref}")
    except ApiError as e:
        if e.status == 404:
            return None
        raise
    if isinstance(data, dict) and data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", "replace")
    return None


# Registry checks that could not be answered; main() adds them to the report's warnings.
REGISTRY_WARNINGS: list[str] = []


def exists(url: str) -> bool:
    """Whether a registry has this package; a registry that is down is a warning, not a failed probe."""
    try:
        get(url)
        return True
    except ApiError as e:
        if e.status == 404:
            return False
        if e.status < 500:
            raise
        REGISTRY_WARNINGS.append(
            f"Could not check {url} (HTTP {e.status}); add that package to PACKAGES by hand if it exists."
        )
    except NetworkError as e:
        REGISTRY_WARNINGS.append(
            f"Could not check {url} ({e.__cause__}); add that package to PACKAGES by hand if it exists."
        )
    return False


MISSING = object()


def registry_get(
    url: str, label: str, warnings: list[str], retries_on_5xx: int = 0
) -> object:
    """GET a registry URL: its JSON, MISSING on 404, or None (with a warning) when the registry fails,
    so one registry being down never fails the probe."""
    try:
        data, _ = get(url)
        return data
    except ApiError as e:
        if e.status in (404, 410):
            return MISSING
        if e.status >= 500 and retries_on_5xx > 0:
            time.sleep(3)
            return registry_get(url, label, warnings, retries_on_5xx - 1)
        warnings.append(
            f"{label} lookup failed (HTTP {e.status} from {url}); install channels from it may be missing. Re-run the probe later or add them to CHANNELS by hand."
        )
    except (
        OSError,
        ValueError,
    ) as e:  # network errors and timeouts; ValueError covers bad JSON
        warnings.append(
            f"{label} lookup failed ({e} from {url}); install channels from it may be missing. Re-run the probe later or add them to CHANNELS by hand."
        )
    return None


def strip_comments(yaml: str) -> str:
    """A workflow's text without comment lines, trailing comments or shell line continuations, lower-cased."""
    y = "\n".join(
        line.split(" #", 1)[0]
        for line in yaml.lower().splitlines()
        if not line.lstrip().startswith("#")
    )
    return re.sub(r"\\\s*\n\s*", " ", y)


REUSABLE = re.compile(
    r"""^\s*(?:-\s*)?uses:\s*['"]?(\./\.github/workflows/[^\s'"@#]+|[\w.-]+/[\w.-]+/\.github/workflows/[^\s'"@#]+@[^\s'"#]+)""",
    re.M,
)


class Workflows:
    """Workflow texts with the reusable workflows they call inlined (one level), fetched once each."""

    def __init__(self, repo: str, ref: str, warnings: list[str]):
        self.repo, self.ref, self.warnings = repo, ref, warnings
        self.cache: dict[tuple[str, str, str], str | None] = {}

    def _fetch(self, repo: str, path: str, ref: str, soft: bool = True) -> str | None:
        """The file's text, or None when it is missing (or, when `soft`, unreadable: a private repo answers 403/404)."""
        key = (repo.lower(), path, ref)
        if key not in self.cache:
            try:
                self.cache[key] = file_text(repo, path, ref)
            except ApiError:
                if not soft:
                    raise
                self.cache[key] = None
        return self.cache[key]

    def text(self, path: str, name: str) -> str:
        own = self._fetch(self.repo, path, self.ref, soft=False) or ""
        parts = [own]
        for target in dict.fromkeys(m.group(1) for m in REUSABLE.finditer(own)):
            if target.startswith("./"):
                called = self._fetch(self.repo, target[2:], self.ref)
            else:
                owner, name_, rest = target.split("/", 2)
                file, ref = rest.rsplit("@", 1)
                called = self._fetch(f"{owner}/{name_}", file, ref)
            if called is None:
                msg = f"{name} calls reusable workflow {target}, which this token cannot read; set TESTS in src/config.ts by hand."
                if msg not in self.warnings:
                    self.warnings.append(msg)
            else:
                parts.append(called)
        return "\n".join(parts)


def go_log_countable(yaml: str) -> bool | None:
    """Whether `go test` in these commands writes per-test lines (-v), JSON (-json) or runs under gotestsum
    (any one such command is enough). None when no `go test` command is visible."""
    y = strip_comments(yaml)
    if "gotestsum" in y:
        return True
    cmds = re.findall(r"\bgo test\b[^\n]*", y)
    if not cmds:
        return None
    return any(re.search(r"(^|\s)-(test\.)?(v|json)(=true)?(\s|$)", c) for c in cmds)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def list_runs(repo: str, params: str, start: datetime, end: datetime) -> list[dict]:
    """Every run created in [start, end] matching `params`. The API returns at most 1,000 runs per
    query, so a window that reports more is halved, as the Worker's collector does."""
    created = urllib.parse.quote(f"{iso(start)}..{iso(end)}")
    url: str | None = (
        f"/repos/{repo}/actions/runs?per_page=100&created={created}&{params}"
    )
    data, h = get(url)
    if data["total_count"] > 1000:
        if end - start <= timedelta(minutes=1):
            raise SystemExit(
                f"probe: more than 1,000 runs ({params}) between {iso(start)} and {iso(end)}; the Actions API would truncate."
            )
        mid = (start + (end - start) / 2).replace(microsecond=0)
        return list_runs(repo, params, start, mid) + list_runs(
            repo, params, mid + timedelta(seconds=1), end
        )
    runs = list(data["workflow_runs"])
    url = next_link(h.get("link"))
    while url:
        data, h = get(url)
        runs += data["workflow_runs"]
        url = next_link(h.get("link"))
    return runs


# Published workspace members: offered, not added, because summing packages that depend on each other counts one
# install several times.
WORKSPACE_CANDIDATES: dict[str, list[str]] = {"npm": [], "crates": []}


def detect_packages(repo: str, ref: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"npm": [], "pypi": [], "crates": []}
    pkg = file_text(repo, "package.json", ref)
    if pkg:
        j = json.loads(pkg)
        if (
            j.get("name")
            and not j.get("private")
            and exists(f"https://registry.npmjs.org/{j['name'].replace('/', '%2F')}")
        ):
            out["npm"].append(j["name"])
        elif j.get("name") and j.get("private"):
            # Some projects mark the root private and publish a built copy (dist/) under the same name.
            try:
                meta, _ = get(
                    f"https://registry.npmjs.org/{j['name'].replace('/', '%2F')}"
                )
                if (
                    isinstance(meta, dict)
                    and repo.lower() in json.dumps(meta.get("repository", "")).lower()
                ):
                    out["npm"].append(j["name"])
            except ApiError as e:
                if e.status != 404:
                    raise
    # Monorepos publish from workspace folders (package.json "workspaces" or pnpm-workspace.yaml).
    globs: list[str] = []
    if pkg:
        ws = json.loads(pkg).get("workspaces") or []
        globs += ws.get("packages", []) if isinstance(ws, dict) else ws
    pnpm = file_text(repo, "pnpm-workspace.yaml", ref)
    if pnpm:
        globs += [
            g.strip() for g in re.findall(r"^\s*-\s*['\"]?([^'\"\n#]+)", pnpm, re.M)
        ]
    dirs: list[str] = []
    for g in globs[:10]:
        if g.startswith("!"):
            continue
        base = re.sub(r"/\*+$", "", g.strip().rstrip("/"))
        if "*" in base:
            continue
        if base != g.strip().rstrip("/"):
            try:
                listing, _ = get(f"/repos/{repo}/contents/{base}?ref={ref}")
            except ApiError:
                continue
            dirs += [
                e["path"]
                for e in listing
                if isinstance(listing, list) and e.get("type") == "dir"
            ]
        else:
            dirs.append(base)
    for d in dirs[:30]:
        text = file_text(repo, f"{d}/package.json", ref)
        if not text:
            continue
        w = json.loads(text)
        n = w.get("name")
        if (
            n
            and not w.get("private")
            and n not in out["npm"]
            and exists(f"https://registry.npmjs.org/{n.replace('/', '%2F')}")
        ):
            WORKSPACE_CANDIDATES["npm"].append(n)
    py = file_text(repo, "pyproject.toml", ref)
    name = None
    if py:
        t = tomllib.loads(py)
        name = t.get("project", {}).get("name") or t.get("tool", {}).get(
            "poetry", {}
        ).get("name")
    if not name:
        setup = file_text(repo, "setup.py", ref) or ""
        m = re.search(r"name\s*=\s*['\"]([A-Za-z0-9_.\-]+)['\"]", setup)
        name = m.group(1) if m else None
    if name and exists(f"https://pypi.org/pypi/{name}/json"):
        out["pypi"].append(name)
    cargo = file_text(repo, "Cargo.toml", ref)
    if cargo:
        t = tomllib.loads(cargo)
        n = t.get("package", {}).get("name")
        if (
            n
            and t.get("package", {}).get("publish") is not False
            and exists(f"https://crates.io/api/v1/crates/{n}")
        ):
            out["crates"].append(n)
        # A workspace publishes its member crates ("crates/*" globs expanded one level).
        members: list[str] = []
        for g in t.get("workspace", {}).get("members", [])[:20]:
            base = re.sub(r"/\*+$", "", g.strip().rstrip("/"))
            if "*" in base:
                continue
            if base != g.strip().rstrip("/"):
                try:
                    listing, _ = get(f"/repos/{repo}/contents/{base}?ref={ref}")
                except ApiError:
                    continue
                members += [
                    e["path"]
                    for e in listing
                    if isinstance(listing, list) and e.get("type") == "dir"
                ]
            else:
                members.append(base)
        for d in members[:30]:
            text = file_text(repo, f"{d}/Cargo.toml", ref)
            if not text:
                continue
            pkg_t = tomllib.loads(text).get("package", {})
            name = pkg_t.get("name")
            if (
                name
                and pkg_t.get("publish") is not False
                and name not in out["crates"]
                and exists(f"https://crates.io/api/v1/crates/{name}")
            ):
                WORKSPACE_CANDIDATES["crates"].append(name)
    return out


def yaml_values(text: str, keys: tuple[str, ...]) -> list[str]:
    """Whitespace/comma-separated tokens under any of `keys` (inline value or the indented block below it)."""
    out: list[str] = []
    key_re = re.compile(
        r"^(\s*)(?:-\s*)?(" + "|".join(map(re.escape, keys)) + r")\s*:\s*(.*)$"
    )
    indent: int | None = None
    for line in text.splitlines():
        m = key_re.match(line)
        if m:
            indent = len(m.group(1))
            out += re.split(r"[\s,\[\]]+", m.group(3))
            continue
        if indent is not None:
            ind = len(line) - len(line.lstrip())
            if line.strip() and not (
                ind > indent or (ind == indent and line.lstrip().startswith("-"))
            ):
                indent = None
            else:
                out += re.split(r"[\s,\[\]]+", line.strip().lstrip("-"))
    return [t.strip("'\"|>") for t in out if t.strip("'\"|>")]


def docker_candidates(text: str, repo: str) -> tuple[set[str], bool]:
    """Docker Hub image names (ns/name) a workflow or goreleaser config publishes, and whether it names ghcr.io."""
    owner = repo.split("/")[0]
    text = re.sub(r"\$\{\{\s*github\.repository\s*\}\}", repo, text, flags=re.I)
    text = re.sub(r"\$\{\{\s*github\.repository_owner\s*\}\}", owner, text, flags=re.I)
    text = re.sub(r"\{\{\s*\.ProjectName\s*\}\}", repo.split("/")[1], text, flags=re.I)
    tokens = yaml_values(text, ("images", "tags", "image_templates"))
    tokens += re.findall(r"\bdocker push\s+(\S+)", text)
    tokens += re.findall(
        r"\b(?:index\.)?docker\.io/[a-z0-9][\w.-]*/[a-z0-9][\w.-]*", text, re.I
    )
    names, ghcr = set(), False
    for t in tokens:
        t = t.split("=", 1)[1] if t.startswith("name=") else t
        t = t.lower()
        if t.split("/", 1)[0] == "ghcr.io":
            ghcr = True
            continue
        t = re.sub(r"^(index\.)?docker\.io/", "", t).split("@", 1)[0]
        t = t.split(":", 1)[0]
        if (
            re.fullmatch(r"[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*", t)
            and "." not in t.split("/")[0]
        ):
            names.add(t)
    return names, ghcr


def detect_channels(
    repo: str,
    ref: str,
    packages: dict[str, list[str]],
    wf_texts: Workflows,
    workflows: list[dict],
    warnings: list[str],
) -> dict[str, list[str]]:
    """Install channels beyond npm/PyPI/crates, each verified to exist on its registry (read-only)."""
    owner, name = repo.split("/")
    lname = name.lower()
    ch: dict[str, list[str]] = {
        "homebrew": [],
        "homebrewCasks": [],
        "docker": [],
        "rubygems": [],
        "nuget": [],
        "conda": [],
        "packagist": [],
    }
    mentions = re.compile(r"github\.com/" + re.escape(repo.lower()) + r"(?![\w.-])")

    # Homebrew: a same-named formula or cask counts only if it points at this repository.
    # formulae.brew.sh (GitHub Pages) often answers a missing name with 503 before settling on 404.
    f = registry_get(
        f"https://formulae.brew.sh/api/formula/{lname}.json",
        "Homebrew formulae",
        warnings,
        retries_on_5xx=2,
    )
    if isinstance(f, dict) and mentions.search(
        f"{((f.get('urls') or {}).get('stable') or {}).get('url', '')} {f.get('homepage', '')}".lower()
    ):
        ch["homebrew"].append(f["name"])
    c = registry_get(
        f"https://formulae.brew.sh/api/cask/{lname}.json",
        "Homebrew casks",
        warnings,
        retries_on_5xx=2,
    )
    if isinstance(c, dict) and mentions.search(
        f"{c.get('homepage', '')} {c.get('url', '')}".lower()
    ):
        ch["homebrewCasks"].append(c["token"])

    # Docker Hub: the owner/name image, plus images the workflows or goreleaser push there.
    candidates = {f"{owner}/{name}".lower()}
    ghcr = False
    texts = [wf_texts.text(w["path"], w["name"]) for w in workflows]
    texts = [
        t
        for t in texts
        if re.search(
            r"docker/build-push-action|docker push|docker/metadata-action", t, re.I
        )
    ]
    texts += [
        t
        for t in (
            file_text(repo, p, ref) for p in (".goreleaser.yml", ".goreleaser.yaml")
        )
        if t
    ]
    for t in texts:
        found, g = docker_candidates(t, repo)
        candidates |= found
        ghcr |= g
    for img in sorted(candidates):
        d = registry_get(
            f"https://hub.docker.com/v2/repositories/{img}/", "Docker Hub", warnings
        )
        if isinstance(d, dict):
            ch["docker"].append(img)
    if ghcr:
        warnings.append(
            "The workflows publish images to ghcr.io, which shows no public pull counts; those images are not tracked."
        )

    # Root and src/ manifests for RubyGems and NuGet.
    try:
        root, _ = get(f"/repos/{repo}/contents?ref={urllib.parse.quote(ref)}")
    except ApiError as e:
        warnings.append(
            f"Could not list the repository root (HTTP {e.status}); RubyGems and NuGet were not checked."
        )
        root = []
    for gs in [
        x for x in root if x["type"] == "file" and x["name"].endswith(".gemspec")
    ]:
        m = re.search(
            r"\.name\s*=\s*['\"]([^'\"]+)['\"]", file_text(repo, gs["path"], ref) or ""
        )
        if m:
            g = registry_get(
                f"https://rubygems.org/api/v1/gems/{urllib.parse.quote(m.group(1))}.json",
                "RubyGems",
                warnings,
            )
            if isinstance(g, dict):
                ch["rubygems"].append(g.get("name", m.group(1)))
    projects = [
        x["path"] for x in root if x["type"] == "file" and x["name"].endswith(".csproj")
    ]
    src = next(
        (x for x in root if x["type"] == "dir" and x["name"].lower() == "src"), None
    )
    if src:
        listing, _ = get(
            f"/repos/{repo}/contents/{src['path']}?ref={urllib.parse.quote(ref)}"
        )
        projects += [
            x["path"]
            for x in listing
            if x["type"] == "file" and x["name"].endswith(".csproj")
        ]
        for d in [x for x in listing if x["type"] == "dir"][:20]:
            sub, _ = get(
                f"/repos/{repo}/contents/{d['path']}?ref={urllib.parse.quote(ref)}"
            )
            projects += [
                x["path"]
                for x in sub
                if x["type"] == "file" and x["name"].endswith(".csproj")
            ]
    # Test and sample projects first out, then the cap: they can fill it before the real package is reached.
    projects = [
        p
        for p in projects
        if not re.search(
            r"(^|[.\-_])(tests?|benchmarks?|samples?|examples?)($|[.\-_])",
            p.rsplit("/", 1)[-1][: -len(".csproj")],
            re.I,
        )
    ]
    for proj in projects[:15]:
        stem = proj.rsplit("/", 1)[-1][: -len(".csproj")]
        xml = file_text(repo, proj, ref) or ""
        if re.search(r"<IsPackable>\s*false\s*</IsPackable>", xml, re.I):
            continue
        m = re.search(r"<PackageId>\s*([^<$]+?)\s*</PackageId>", xml)
        pid = m.group(1) if m else stem
        q = registry_get(
            f"https://azuresearch-usnc.nuget.org/query?q=packageid:{urllib.parse.quote(pid)}&prerelease=true",
            "NuGet",
            warnings,
        )
        hits = q.get("data", []) if isinstance(q, dict) else []
        hit = next((h for h in hits if h["id"].lower() == pid.lower()), None)
        # A project name can match someone else's package: keep it only if the package points back at this repo.
        if (
            hit
            and hit["id"] not in ch["nuget"]
            and (
                mentions.search(json.dumps(hit).lower()) or mentions.search(xml.lower())
            )
        ):
            ch["nuget"].append(hit["id"])
        elif hit and hit["id"] not in ch["nuget"]:
            warnings.append(
                f"NuGet has a package \"{hit['id']}\" matching project {proj}, but it does not point at this repository; not added to CHANNELS.nuget."
            )

    # conda-forge: the PyPI name is the repository's own; a bare repo-name match must point back here.
    for n, own in [*((p, True) for p in packages.get("pypi", [])), (name, False)]:
        n = n.lower().replace("_", "-")
        if f"conda-forge/{n}" in ch["conda"]:
            continue
        p = registry_get(
            f"https://api.anaconda.org/package/conda-forge/{urllib.parse.quote(n)}",
            "Anaconda (conda-forge)",
            warnings,
        )
        if isinstance(p, dict) and (own or mentions.search(json.dumps(p).lower())):
            ch["conda"].append(f"conda-forge/{n}")

    # Packagist: composer.json's name is the package; it is this repository's by definition.
    composer = file_text(repo, "composer.json", ref)
    if composer:
        cname = (json.loads(composer).get("name") or "").lower()
        if re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", cname):
            s = registry_get(
                f"https://packagist.org/packages/{cname}/stats.json",
                "Packagist",
                warnings,
            )
            if isinstance(s, dict) and "downloads" in s:
                ch["packagist"].append(cname)
    return ch


def detect_framework(
    repo: str, ref: str, test_yaml: str, manifests: bool = True
) -> str | None:
    """The runner a workflow's commands name, else (when `manifests`) a guess from the repo's manifests."""
    # Comments mention runners they do not run ("the pytest-summary parser"), so only commands count.
    y = strip_comments(test_yaml)
    for fw, pat in (
        ("pytest", r"\bpytest\b"),
        ("vitest", r"\bvitest\b"),
        ("jest", r"\bjest\b"),
        ("mocha", r"\bmocha\b"),
        ("cargo", r"cargo (nextest|test)\b"),
        ("go", r"\bgo test\b|gotestsum"),
        ("rspec", r"\brspec\b|\brake (spec|ci)\b"),
        ("dotnet", r"\bdotnet test\b"),
        ("maven", r"\bmvnw?\b[^\n]*\b(test|verify|install)\b"),
        ("phpunit", r"\b(phpunit|pest)\b"),
    ):
        if re.search(pat, y):
            return fw
    # Wrappers (tox, nox, npm test, make test, ...) hide the runner: it is the one the manifests name.
    if re.search(
        r"\b(tox|nox|hatch test|(npm|pnpm|yarn|bun) (run )?test|make test|just test|uv run (tox|nox))\b",
        y,
    ):
        manifests = True
    if not manifests:
        return None
    pkg = file_text(repo, "package.json", ref)
    if pkg:
        deps = {
            **json.loads(pkg).get("devDependencies", {}),
            **json.loads(pkg).get("dependencies", {}),
        }
        if "vitest" in deps:
            return "vitest"
        if "jest" in deps:
            return "jest"
        if "mocha" in deps:
            return "mocha"
    if file_text(repo, "Cargo.toml", ref):
        return "cargo"
    if file_text(repo, "go.mod", ref):
        return "go"
    if file_text(repo, "pyproject.toml", ref) or file_text(repo, "setup.py", ref):
        return "pytest"
    if file_text(repo, "Gemfile", ref) and file_text(repo, ".rspec", ref):
        return "rspec"
    if file_text(repo, "pom.xml", ref):
        return "maven"
    if file_text(repo, "composer.json", ref):
        return "phpunit"
    return None


PR_EVENTS = ("pull_request", "pull_request_target")


def pull_request_runs(repo: str, days: int = 30) -> int:
    """Completed pull_request and pull_request_target runs on every branch over the last `days` days."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    created = urllib.parse.quote(f"{iso(now - timedelta(days=days))}..{iso(now)}")
    total = 0
    for ev in PR_EVENTS:
        data, _ = get(
            f"/repos/{repo}/actions/runs?event={ev}&status=completed&created={created}&per_page=1"
        )
        total += data["total_count"]
    return total


def events_on_default_branch(
    repo: str, ref: str, days: int = 30
) -> tuple[dict[str, int], bool]:
    """Completed runs per event on the default branch over the last `days` days, merge-queue runs
    for that branch included. Returns (counts, exact): `exact` is False when the branch had more
    than 1,000 runs and the counts come from per-event totals of a fixed event list.
    Pull-request events are left out: their head_branch is the PR's source branch, so a fork's
    `main` would match the default branch by accident."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=days)
    created = urllib.parse.quote(f"{iso(start)}..{iso(now)}")
    base = f"/repos/{repo}/actions/runs?branch={urllib.parse.quote(ref)}&status=completed&created={created}"
    first, _ = get(base + "&per_page=100")
    counts: Counter[str] = Counter()
    exact = first["total_count"] <= 1000
    if exact:
        for r in list_runs(
            repo, f"branch={urllib.parse.quote(ref)}&status=completed", start, now
        ):
            if r["head_branch"] == ref and r["event"] not in PR_EVENTS:
                counts[r["event"]] += 1
    else:
        for ev in sorted(
            (set(KNOWN_EVENTS) | {r["event"] for r in first["workflow_runs"]})
            - {"merge_group", *PR_EVENTS}
        ):
            data, _ = get(f"{base}&event={ev}&per_page=1")
            if data["total_count"]:
                counts[ev] = data["total_count"]
    queue = f"gh-readonly-queue/{ref}/"
    counts["merge_group"] = sum(
        1
        for r in list_runs(repo, "event=merge_group&status=completed", start, now)
        if (r["head_branch"] or "").startswith(queue)
    )
    out = {ev: counts.get(ev, 0) for ev in ("push", "schedule", "merge_group")}
    out.update(
        {
            ev: n
            for ev, n in sorted(counts.items(), key=lambda x: -x[1])
            if ev not in out and n
        }
    )
    return out, exact


def workflow_main_runs(repo: str, ref: str, file: str) -> tuple[int, int]:
    """(push runs, merge-queue runs) a workflow has completed for the default branch, all time.
    Merge-queue runs cannot be filtered by branch, so above 100 they are the total scaled by the
    share of the latest 100 that were queued for this branch."""
    push, _ = get(
        f"/repos/{repo}/actions/workflows/{file}/runs?branch={urllib.parse.quote(ref)}&event=push&status=completed&per_page=1"
    )
    mq, _ = get(
        f"/repos/{repo}/actions/workflows/{file}/runs?event=merge_group&status=completed&per_page=100"
    )
    total, sample = mq["total_count"], mq["workflow_runs"]
    ours = sum(
        1
        for r in sample
        if (r["head_branch"] or "").startswith(f"gh-readonly-queue/{ref}/")
    )
    merge = (
        ours
        if total <= len(sample)
        else round(total * ours / len(sample)) if sample else 0
    )
    return push["total_count"], merge


def main() -> int:
    # Workflow names and warnings can hold emoji; a piped Windows console is cp1252 and would crash on them.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", help="owner/name")
    ap.add_argument("--out", default="probe.json")
    a = ap.parse_args()
    if not TOKEN:
        print(
            'No GitHub token: set GITHUB_TOKEN, e.g. GITHUB_TOKEN="$(gh auth token)" with a logged-in GitHub CLI. Unauthenticated calls hit the 60/hour limit.',
            file=sys.stderr,
        )
        return 2
    repo = a.repo
    meta, _ = get(f"/repos/{repo}")
    ref = meta["default_branch"]
    report: dict = {
        "repo": meta["full_name"],
        "name": meta["name"],
        "default_branch": ref,
        "private": meta["private"],
        "stars": meta["stargazers_count"],
        "forks": meta["forks_count"],
        "open_issues_and_prs": meta["open_issues_count"],
        "permission": meta.get("permissions", {}),
        "warnings": [],
    }

    # History sizes drive the first collection's duration and the page guards.
    try:
        report["commits"] = count_via_link(f"/repos/{repo}/commits?sha={ref}")
    except ApiError as e:
        if e.status == 409:
            print(
                f"probe: {repo} is an empty repository (GitHub answers HTTP 409: no commits yet). "
                "There is nothing to build a dashboard from; push a first commit and probe again.",
                file=sys.stderr,
            )
            return 2
        raise
    report["code_stats_available"] = report["commits"] <= 10_000
    if not report["code_stats_available"]:
        report["warnings"].append(
            "GitHub's code-frequency statistics are unavailable past 10,000 commits; scaffold sets CODE_STATS = false"
        )

    # Access: the stargazer list is shown only to people with access to the repo.
    try:
        get(
            f"/repos/{repo}/stargazers?per_page=1",
            accept="application/vnd.github.star+json",
        )
        report["stargazer_access"] = True
    except ApiError as e:
        report["stargazer_access"] = False
        report["warnings"].append(
            f"Stargazer list refused (HTTP {e.status}): this token's owner lacks access. The deployed GITHUB_TOKEN must belong to a maintainer, or star history fails."
        )
    if meta["stargazers_count"] >= 40_000:
        report["warnings"].append(
            "40,000+ stars: GitHub cuts the stargazer list off there. Set STAR_HISTORY = false."
        )
    if meta["forks_count"] > 40_000:
        report["warnings"].append(
            f"{meta['forks_count']:,} forks: the fork list may be cut off like the stargazer list, and the collector's 400-page guard stops at 40,000; fork history will be incomplete."
        )
    try:
        get(f"/repos/{repo}/actions/runs?per_page=1")
        report["actions_access"] = True
    except ApiError as e:
        report["actions_access"] = False
        report["warnings"].append(
            f"Actions API refused (HTTP {e.status}); the token needs Actions: read."
        )
    try:
        get(f"/repos/{repo}/traffic/views")
        report["traffic_access"] = True
    except ApiError as e:
        report["traffic_access"] = False
        if e.status == 403:
            report["warnings"].append(
                'Traffic API refused (HTTP 403): the deployed token needs push access (fine-grained: the "Administration" repository permission, read) for the Traffic card; scaffold sets TRAFFIC = false.'
            )
        else:
            report["warnings"].append(
                f"Traffic API failed (HTTP {e.status}); scaffold sets TRAFFIC = false. Re-run the probe, or set TRAFFIC by hand once it answers."
            )

    # The issues list paginates by cursor (no last-page number), so count through search.
    found, _ = get(f"/search/issues?q=repo:{repo}&per_page=1")
    report["issues_and_prs"] = found["total_count"]
    for k, guard in (("commits", 40_000), ("issues_and_prs", 40_000)):
        if report[k] > guard:
            report["warnings"].append(
                f"{report[k]:,} {k}: past the collector's 400-page guard; raise it in src/history.ts and expect the first full collection to take several minutes."
            )

    # CI volume: the runs API returns at most 1,000 runs per query; the collector splits busy days.
    # Yesterday's count, one whole day: a multi-day count can come back capped on busy repositories.
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    runs, _ = get(f"/repos/{repo}/actions/runs?per_page=1&created={yday}")
    report["runs_yesterday"] = runs.get("total_count", 0)
    if report["runs_yesterday"] > 1000:
        minutes = max(1, round(report["runs_yesterday"] / 100 * 1.5 / 60))
        report["warnings"].append(
            f"{report['runs_yesterday']:,} workflow runs yesterday: reading one day of runs takes about {minutes} min, so backfill with "
            "days=1 per call (days=1&skip=N), keep NETWORK_RETRIES on, and expect the nightly run to take that long per day read."
        )

    # Which events build the default branch decides whether the CI page has anything to show.
    events, exact = events_on_default_branch(repo, ref)
    report["events_on_default_branch"] = events
    report["events_on_default_branch_exact"] = exact
    if not exact:
        report["warnings"].append(
            "Over 1,000 runs on the default branch in 30 days: events_on_default_branch counts only the common events and those seen in the latest 100 runs."
        )
    report["pull_request_runs_30d"] = pull_request_runs(repo)
    landed = events["push"] + events["schedule"] + events["merge_group"]
    last_push = None
    if not landed:
        # A quiet month is not a PR-only setup: look for any older push run on the branch.
        older, _ = get(
            f"/repos/{repo}/actions/runs?branch={urllib.parse.quote(ref)}&event=push&status=completed&per_page=1"
        )
        last_push = (
            older["workflow_runs"][0]["created_at"][:10]
            if older["total_count"]
            else None
        )
    if last_push:
        report["warnings"].append(
            f"No push, schedule or merge-queue runs on {ref} in the last 30 days (the latest push run is from {last_push}): the default-branch cards"
            f" (pass rate, flaky, tests) stay empty until CI runs on {ref} again; {report['pull_request_runs_30d']:,} pull-request runs show in the CI page's 'All branches and triggers' view."
        )
    elif not landed and report["pull_request_runs_30d"]:
        report["warnings"].append(
            "CI runs only on pull requests: the default-branch cards (pass rate, flaky, tests) will be empty; the CI page's 'All branches and triggers' view still shows every run"
            f" (last 30 days: {report['pull_request_runs_30d']:,} pull-request runs; on {ref}: {', '.join(f'{k} {v}' for k, v in events.items() if v) or 'no other completed runs'})."
        )
    elif not landed:
        report["warnings"].append(
            "CI never runs on the default branch after merge; the CI page will be empty unless BUILD_EVENTS in src/config.ts includes the events this repo uses"
            f" (last 30 days on {ref}: {', '.join(f'{k} {v}' for k, v in events.items() if v) or 'no completed runs'})."
        )

    workflows = []
    url: str | None = f"/repos/{repo}/actions/workflows?per_page=100"
    while url:
        wfs, h = get(url)
        for w in wfs.get("workflows", []):
            if w["path"].startswith("dynamic/"):
                continue
            workflows.append(
                {
                    "name": w["name"],
                    "path": w["path"],
                    "file": w["path"].rsplit("/", 1)[-1],
                    "state": w["state"],
                }
            )
        url = next_link(h.get("link"))
    report["workflows"] = workflows

    # The test workflow: among active workflows whose YAML runs a known test runner, the one with the
    # most push and merge-queue runs on the default branch, preferring one with "test" in its name. A guess to confirm.
    wf_texts = Workflows(repo, ref, report["warnings"])
    scored = []
    for w in [w for w in workflows if w["state"] == "active"]:
        yaml = wf_texts.text(w["path"], w["name"])
        fw = detect_framework(repo, ref, yaml, manifests=False)
        if not fw:
            continue
        push, merge = workflow_main_runs(repo, ref, w["file"])
        scored.append(
            (
                push + merge,
                bool(re.search(r"test", w["name"], re.I)),
                fw,
                w,
                push,
                merge,
            )
        )
    # A workflow named for tests beats a busier build workflow that happens to run a few; "unit" breaks ties.
    scored.sort(
        key=lambda x: (not x[1], -x[0], not re.search(r"unit", x[3]["name"], re.I))
    )
    report["test_workflow_candidates"] = [
        {
            "name": w["name"],
            "file": w["file"],
            "framework": fw,
            "push_runs": push,
            "merge_group_runs": merge,
        }
        for _, _, fw, w, push, merge in scored
    ]
    best = next((x for x in scored if x[0] > 0), None)
    report["test_workflow"] = best[3]["name"] if best else None
    report["test_framework"] = (
        best[2] if best else detect_framework(repo, ref, "", manifests=True)
    )
    if best:
        # A workflow can run two suites (cargo test and a JS suite behind `pnpm test`); the dashboard counts one.
        y = strip_comments(wf_texts.text(best[3]["path"], best[3]["name"]))
        runners = {
            "pytest": r"\bpytest\b",
            "vitest": r"\bvitest\b",
            "jest": r"\bjest\b",
            "mocha": r"\bmocha\b",
            "cargo": r"cargo (nextest|test)\b",
            "go": r"\bgo test\b|gotestsum",
            "rspec": r"\brspec\b|\brake (spec|ci)\b",
            "dotnet": r"\bdotnet test\b",
            "maven": r"\bmvnw?\b[^\n]*\b(test|verify|install)\b",
            "phpunit": r"\b(phpunit|pest)\b",
        }
        found = [fw for fw, pat in runners.items() if re.search(pat, y)]
        if re.search(r"\b((npm|pnpm|yarn|bun) (run )?test)\b", y):
            js = detect_framework(repo, ref, "", manifests=True)
            if js in ("vitest", "jest", "mocha") and js not in found:
                found.append(js)
        report["test_frameworks_in_workflow"] = found
        if len(found) > 1:
            report["warnings"].append(
                f"\"{best[3]['name']}\" runs several test suites ({', '.join(found)}); the dashboard counts one (TESTS.framework = "
                f"{report['test_framework']}). Pick the suite whose count matters most, usually the larger one."
            )
    if not best:
        report["warnings"].append(
            "No workflow that runs on the default branch (push or merge queue) runs a recognised test runner (pytest, jest, vitest, mocha, go test, cargo, rspec, dotnet test, maven, phpunit); the Tests source starts off."
        )

    # Go logs carry test counts only per test (-v), as JSON (-json) or in gotestsum's DONE line.
    report["test_log_countable"] = None
    if best and best[2] == "go":
        name = best[3]["name"]
        report["test_log_countable"] = go_log_countable(
            wf_texts.text(best[3]["path"], name)
        )
        if report["test_log_countable"] is False:
            report["warnings"].append(
                f"go test runs without -v/-json/gotestsum, so its logs carry no test counts: add -v (or use gotestsum) in {name}, or the Tests source stays empty"
            )

    # Eval candidates: workflows that upload the contract file name, or are named like evals/benchmarks.
    report["eval_workflow_candidates"] = [
        w["file"]
        for w in workflows
        if re.search(r"eval|bench", w["name"] + " " + w["file"], re.I)
    ]

    rels: list[dict] = []
    url = f"/repos/{repo}/releases?per_page=100"
    pages = 0
    while url and pages < RELEASE_PAGE_CAP:
        page, h = get(url)
        rels += page
        pages += 1
        url = next_link(h.get("link"))
    report["releases_total"] = len(rels)
    report["releases_complete"] = url is None
    if url:
        report["warnings"].append(
            f"More than {len(rels):,} releases: read the latest {RELEASE_PAGE_CAP} pages only; tags and release files from older releases are missing from this report."
        )
    report["tags"] = list(dict.fromkeys(r["tag_name"] for r in rels))
    assets: Counter[str] = Counter()
    for r in rels:
        for x in r.get("assets", []):
            assets[x["name"]] += x["download_count"]
    report["asset_names"] = sorted(assets)
    report["asset_downloads"] = sum(assets.values())
    if not rels:
        report["warnings"].append(
            "No GitHub releases: the Users page will show registry downloads only."
        )

    report["packages"] = detect_packages(repo, ref)
    report["package_candidates"] = WORKSPACE_CANDIDATES
    report["warnings"] += REGISTRY_WARNINGS
    for reg, names in WORKSPACE_CANDIDATES.items():
        if names:
            report["warnings"].append(
                f"Published {reg} workspace packages: {', '.join(names[:12])}{' …' if len(names) > 12 else ''}. They were not added to PACKAGES: "
                "packages that depend on each other count one install several times. Add the ones users install directly."
            )
    report["warnings"].append(
        "Packages were taken from root manifests (package.json, pyproject.toml/setup.py, Cargo.toml); workspace members are listed as candidates. Add any others to PACKAGES in src/config.ts."
    )
    report["channels"] = detect_channels(
        meta["full_name"],
        ref,
        report["packages"],
        wf_texts,
        workflows,
        report["warnings"],
    )
    # Scheduled workflows count toward the default branch's pass rate; housekeeping ones should be excluded.
    report["scheduled_workflows"] = [
        w["name"]
        for w in workflows
        if w["state"] == "active"
        and re.search(
            r"^\s*schedule\s*:",
            strip_comments(wf_texts.text(w["path"], w["name"])),
            re.M,
        )
    ]
    if report["scheduled_workflows"]:
        report["warnings"].append(
            "Scheduled workflows count toward the default branch's pass rate: "
            + ", ".join(report["scheduled_workflows"])
            + ". List housekeeping ones (stale, lock, reports, sweeps meant to fail) in CI_EXCLUDE_WORKFLOWS."
        )

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Wrote {a.out}")
    for k in (
        "default_branch",
        "stars",
        "forks",
        "commits",
        "issues_and_prs",
        "runs_yesterday",
        "events_on_default_branch",
        "pull_request_runs_30d",
        "test_workflow",
        "test_framework",
        "test_log_countable",
        "packages",
        "channels",
        "traffic_access",
        "code_stats_available",
        "releases_total",
    ):
        print(f"  {k}: {report[k]}")
    print(f"  tags: {len(report['tags'])}, release files: {len(report['asset_names'])}")
    for w in report["warnings"]:
        print(f"  WARNING: {w}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NetworkError as e:
        # A GitHub call that the network kept dropping: stop with the message, not a traceback.
        print(e, file=sys.stderr)
        sys.exit(2)
