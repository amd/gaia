# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Add skills from a pasted link — one skill or a whole library.

Accepted links:

* ``https://github.com/<owner>/<repo>`` — every ``SKILL.md`` in the repo
* ``https://github.com/<owner>/<repo>/tree/<ref>/<path>`` — only under ``path``
* ``https://github.com/<owner>/<repo>/blob/<ref>/<path>/SKILL.md`` — that skill
* ``<owner>/<repo>`` shorthand, as ``npx skills add`` takes it
* ``https://skills.sh/<owner>/<repo>[/<skill>]`` — the GitHub repo it lists
* any other ``https://…/*.zip`` — a bundle holding one or more skills

:func:`preview_link` downloads and audits without writing anything, so a UI can
show what a library holds — and what its audit found — before the user agrees.
:func:`add_from_link` then imports the chosen skills through
:func:`gaia.skills.capture.import_bundle`, the same gate ``gaia skill import``
uses: every skill lands ``experimental``, a BLOCK verdict never lands, and code
(``tools.py`` or ``scripts/``) needs an explicit opt-in.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urlparse

from gaia.logger import get_logger
from gaia.skills.errors import SkillError
from gaia.skills.format import SKILL_FILENAME, parse_skill_file
from gaia.skills.manager import SkillManager
from gaia.utils.archive import ArchiveError, safe_extract

log = get_logger(__name__)

#: Shown before anything from a link is added. Every surface renders this text.
SECURITY_WARNING = (
    "Skills from a link are written by someone else. A skill's instructions steer "
    "what GAIA does with your files, shell, and accounts, and a skill that ships "
    "code (tools.py or scripts/) runs that code on this PC. GAIA audits each skill "
    "and refuses anything the audit blocks, but an audit is not a guarantee. Only "
    "add skills from people you trust, and read what they do first."
)

#: One-click libraries. Each name also works anywhere a link does
#: (``/skills add pstack``), and always fetches the library's latest version.
FEATURED_LIBRARIES: List[Dict[str, str]] = [
    {
        "id": "pstack",
        "name": "pstack",
        "url": "https://github.com/michael-denyer/pstack-claude",
        "description": "Engineering workflows: design, review, CI fixes, TDD, "
        "PR hygiene (MIT)",
    },
]

#: Largest download accepted, and largest unpacked size: a skills library is text.
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_UNPACKED_BYTES = 300 * 1024 * 1024
#: More SKILL.md files than this is not a skills library.
MAX_SKILLS = 500

#: Licence and notice files carried into every skill installed from a library,
#: so an MIT-style notice travels with each copy.
_NOTICE_PREFIXES = ("license", "licence", "notice", "copying")

#: Where an archive is unpacked inside the work dir.
_UNPACKED = "unpacked"

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
_REPO_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


class SkillLinkError(SkillError):
    """A link could not be read as a skill or skills library."""


@dataclass
class LinkSource:
    """Where a pasted link resolves to."""

    #: What the user pasted, recorded as each skill's origin.
    link: str
    #: The https URL of the zip actually downloaded.
    download_url: str
    #: Path inside the archive to search (``""`` = all of it).
    subpath: str = ""
    #: Only this skill, when the link named one.
    only_skill: Optional[str] = None
    #: ``owner/repo`` for a GitHub source, for display.
    repo: Optional[str] = None


@dataclass
class LinkSkill:
    """One skill found behind a link, and what its audit said."""

    name: str
    description: str
    #: Folder of the skill, relative to the library root.
    path: str
    has_code: bool = False
    verdict: str = ""
    findings: List[str] = field(default_factory=list)
    installed: bool = False
    #: Installed from this same source, so adding it again updates it in place.
    update: bool = False
    #: Why this skill cannot be added (unparseable, BLOCK, duplicate name).
    error: Optional[str] = None


@dataclass
class LinkPreview:
    link: str
    source: str
    skills: List[LinkSkill]
    #: What was audited: ``commit:<sha>`` for a GitHub archive, else
    #: ``sha256:<digest>``. Pass it to :func:`add_from_link` so what lands is
    #: exactly what the user reviewed.
    pin: str = ""
    warning: str = SECURITY_WARNING

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LinkAddResult:
    added: List[Dict[str, Any]] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def resolve_link(link: str) -> LinkSource:
    """Turn a pasted link into the zip to download and the part of it to read."""
    text = (link or "").strip().rstrip("/")
    if not text:
        raise SkillLinkError("Paste a link to a skill or skills library.")
    featured = {f["id"]: f["url"] for f in FEATURED_LIBRARIES}
    if text.lower() in featured:
        text = featured[text.lower()]

    # A link copied from a browser address bar carries a query and a fragment
    # (?tab=readme-ov-file, #readme). Neither names a folder, and left on they
    # make a repo read as "repo.git" and the owner/repo shorthand fail the https
    # check, so read the repository out of the link without them. A .zip link
    # keeps its query, which is often the signed token that makes it download.
    bare = text.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if bare.endswith(".git"):
        bare = bare[: -len(".git")]

    shorthand = bare.split("/")
    if (
        "://" not in bare
        and len(shorthand) == 2
        and all(_REPO_PART.match(p) for p in shorthand)
    ):
        return _github(text, shorthand[0], shorthand[1], ref="HEAD", rest=[])

    if bare.startswith(("github.com/", "skills.sh/")):
        bare = "https://" + bare
    parsed = urlparse(bare)
    if parsed.scheme != "https":
        raise SkillLinkError(
            f"Refusing {link!r}: only https links are accepted, because a skill "
            "fetched over an unencrypted connection can be altered in transit."
        )
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]

    if host in ("github.com", "www.github.com"):
        if len(parts) < 2:
            raise SkillLinkError(
                f"{link!r} is not a repository link. Use "
                "https://github.com/<owner>/<repo>."
            )
        owner, repo = parts[0], parts[1]
        if len(parts) >= 4 and parts[2] in ("tree", "blob"):
            return _github(text, owner, repo, ref=parts[3], rest=parts[4:])
        if len(parts) == 2:
            return _github(text, owner, repo, ref="HEAD", rest=[])
        raise SkillLinkError(
            f"{link!r} points at a GitHub page that is not a repository, folder, or "
            "file. Paste the repository link, or a /tree/<branch>/<folder> link."
        )

    if host in ("skills.sh", "www.skills.sh"):
        if len(parts) < 2:
            raise SkillLinkError(
                f"{link!r} is not a skills.sh skill or repository page. Use "
                "https://skills.sh/<owner>/<repo>/<skill>."
            )
        source = _github(text, parts[0], parts[1], ref="HEAD", rest=[])
        source.only_skill = parts[2] if len(parts) >= 3 else None
        return source

    if parsed.path.lower().endswith(".zip"):
        return LinkSource(link=text, download_url=text)

    raise SkillLinkError(
        f"GAIA can't read skills from {link!r}. Paste a GitHub repository or folder "
        "link, a skills.sh link, or an https link to a .zip."
    )


def _github(
    link: str, owner: str, repo: str, *, ref: str, rest: List[str]
) -> LinkSource:
    if not all(_REPO_PART.match(p) and p not in (".", "..") for p in (owner, repo)):
        raise SkillLinkError(f"{link!r} does not name a GitHub repository.")
    subpath = "/".join(rest)
    if rest and rest[-1] == SKILL_FILENAME:
        subpath = "/".join(rest[:-1])
    return LinkSource(
        link=link,
        download_url=(
            f"https://codeload.github.com/{owner}/{repo}/zip/{quote(ref, safe='')}"
        ),
        subpath=subpath,
        repo=f"{owner}/{repo}",
    )


def _download(url: str, destination: Path) -> Path:
    """Fetch *url* to *destination* through GAIA's SSRF-guarded web client.

    The client refuses hosts that resolve to private, loopback or link-local
    addresses — on every redirect hop — so a pasted link can't reach this PC's
    own services or the local network.
    """
    import requests

    from gaia.web.client import WebClient

    log.info("Downloading skills from %s", url)
    try:
        WebClient().download(
            url,
            save_dir=str(destination.parent),
            filename=destination.name,
            max_size=MAX_DOWNLOAD_BYTES,
        )
    except ValueError as exc:
        raise SkillLinkError(
            f"Refusing {url}: {exc} Skills can only be added from public https "
            "addresses."
        ) from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 404:
            raise SkillLinkError(
                f"Nothing found at {url}. Check the link — a private repository "
                "can't be read without signing in, so add it from a local copy with "
                "'gaia skill import <folder>'."
            ) from exc
        raise SkillLinkError(f"Could not download {url}: {exc}.") from exc
    except (requests.RequestException, IOError) as exc:
        raise SkillLinkError(
            f"Could not download {url}: {exc}. Check the link and your network."
        ) from exc
    return destination


def _archive_pin(archive: Path) -> str:
    """``commit:<sha>`` when the zip records its commit (GitHub does), else a digest."""
    with zipfile.ZipFile(archive) as zf:
        comment = zf.comment.decode("ascii", "ignore").strip()
    if re.fullmatch(r"[0-9a-f]{40}", comment):
        return f"commit:{comment}"
    return "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()


def _skip_links(archive: Path) -> Path:
    """Rewrite *archive* without symlink members, which a skill can't ship anyway."""
    with zipfile.ZipFile(archive) as src:
        links = [
            i for i in src.infolist() if (i.external_attr >> 16) & 0o170000 == 0o120000
        ]
        if not links:
            return archive
        cleaned = archive.with_name("nolinks.zip")
        with zipfile.ZipFile(cleaned, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                if info not in links:
                    dst.writestr(info, src.read(info))
    log.info("Skipped %d symlink(s) in %s", len(links), archive)
    return cleaned


def fetch_library(source: LinkSource, workdir: Path) -> tuple:
    """Download and unpack *source*; return ``(directory to search, pin)``."""
    archive = _download(source.download_url, workdir / "library.zip")
    try:
        pin = _archive_pin(archive)
    except zipfile.BadZipFile as exc:
        raise SkillLinkError(
            f"{source.link} did not download as a valid zip: {exc}."
        ) from exc
    unpacked = workdir / _UNPACKED
    try:
        safe_extract(
            _skip_links(archive),
            unpacked,
            kind="zip",
            max_total_bytes=MAX_UNPACKED_BYTES,
        )
    except (ArchiveError, zipfile.BadZipFile) as exc:
        raise SkillLinkError(
            f"{source.link} did not download as a valid zip: {exc}."
        ) from exc

    root = unpacked
    # GitHub archives wrap everything in one <repo>-<ref>/ folder.
    children = [c for c in root.iterdir()]
    if source.repo and len(children) == 1 and children[0].is_dir():
        root = children[0]
    if source.subpath:
        root = (root / source.subpath).resolve()
        if not root.is_dir() or unpacked.resolve() not in root.parents:
            raise SkillLinkError(
                f"{source.subpath!r} is not a folder in {source.repo or source.link}."
            )
    return root, pin


def source_key(source: LinkSource) -> str:
    """The same library, however it was pasted — ``owner/repo`` or its full URL."""
    return f"https://github.com/{source.repo}" if source.repo else source.download_url


def _is_notice(path: Path) -> bool:
    return path.is_file() and path.name.lower().startswith(_NOTICE_PREFIXES)


def _is_licence(path: Path) -> bool:
    return _is_notice(path) and not path.name.lower().startswith("notice")


def _carry_notices(directory: Path, top: Path) -> None:
    """Copy the nearest licence/notice files from the library into *directory*.

    A skill's own licence wins: the library's is only added when the skill has
    none. Notice files are added unless one of the same name is already there.
    """
    import shutil

    has_licence = any(_is_licence(p) for p in directory.iterdir())
    for ancestor in directory.parents:
        if ancestor == top.parent or top not in (ancestor, *ancestor.parents):
            return
        notices = [p for p in ancestor.iterdir() if _is_notice(p)]
        if notices:
            for notice in notices:
                if (has_licence and _is_licence(notice)) or (
                    directory / notice.name
                ).exists():
                    continue
                shutil.copy2(notice, directory / notice.name)
            return


def find_skill_dirs(root: Path) -> List[Path]:
    """Every folder under *root* holding a ``SKILL.md``, sorted."""
    found = []
    for path in sorted(root.rglob(SKILL_FILENAME)):
        if not path.is_file() or any(p in _SKIP_DIRS for p in path.parts):
            continue
        found.append(path.parent)
        if len(found) > MAX_SKILLS:
            raise SkillLinkError(
                f"More than {MAX_SKILLS} skills found — that is not a skills "
                "library. Paste a link to the folder that holds the skills you want."
            )
    return found


def _scan(
    root: Path, source: LinkSource, manager: Optional[SkillManager] = None
) -> Dict[str, tuple]:
    """``name -> (LinkSkill, directory)`` for every skill under *root*.

    Licence files are copied in first, so the audit sees the bundle that lands.
    """
    from gaia.skills.audit.engine import audit_skill_object
    from gaia.skills.capture import _render_findings, _ships_code
    from gaia.skills.lock import SkillLock
    from gaia.skills.permissions import refuse_unbridged_permissions
    from gaia.skills.tiers import LOWEST_TIER, enforce_tier_ceiling

    dirs = find_skill_dirs(root)
    if not dirs:
        raise SkillLinkError(
            f"No {SKILL_FILENAME} found at {source.link}. A skill is a folder whose "
            f"one required file is {SKILL_FILENAME}."
        )
    resolver = manager or SkillManager()
    installed = set(resolver.discover())
    lock = SkillLock.load(resolver.user_root)
    from_here = source_key(source) + "#"
    # Licences sit at the top of the archive, above any folder the link named.
    top = next((p for p in (root, *root.parents) if p.name == _UNPACKED), root)
    scanned: Dict[str, tuple] = {}
    for directory in dirs:
        rel = directory.relative_to(root).as_posix() or "."
        try:
            skill = parse_skill_file(directory, check_directory_name=False)
        except SkillError as exc:
            if source.only_skill and source.only_skill != directory.name:
                continue
            scanned[f"?{rel}"] = (
                LinkSkill(
                    name=directory.name, description="", path=rel, error=str(exc)
                ),
                directory,
            )
            continue
        if source.only_skill and source.only_skill not in (skill.name, directory.name):
            continue
        locked = lock.get(skill.name)
        entry = LinkSkill(
            name=skill.name,
            description=skill.description,
            path=rel,
            has_code=_ships_code(skill, directory),
            installed=skill.name in installed,
            update=bool(locked and locked.origin.startswith(from_here)),
        )
        if skill.name in scanned:
            entry.error = (
                f"Another skill in this library is also named '{skill.name}' "
                f"({scanned[skill.name][0].path}); only the first is added."
            )
            scanned[f"?{rel}"] = (entry, directory)
            continue
        _carry_notices(directory, top)
        report = audit_skill_object(skill, directory=directory, tier=LOWEST_TIER)
        entry.verdict = report.verdict
        entry.findings = _render_findings(report)
        if report.verdict == "BLOCK":
            entry.error = f"Blocked by the security audit: {report.reason}"
        else:
            permissions = skill.parsed_permissions()
            try:
                refuse_unbridged_permissions(permissions, skill_name=skill.name)
                enforce_tier_ceiling(
                    permissions, tier=LOWEST_TIER, skill_name=skill.name
                )
            except SkillError as exc:
                entry.error = str(exc)
        scanned[skill.name] = (entry, directory)
    if source.only_skill and not any(not k.startswith("?") for k in scanned):
        raise SkillLinkError(
            f"No skill named '{source.only_skill}' at {source.repo or source.link}."
        )
    return scanned


def preview_link(link: str, *, manager: Optional[SkillManager] = None) -> LinkPreview:
    """List and audit the skills behind *link* without installing any."""
    source = resolve_link(link)
    with tempfile.TemporaryDirectory(prefix="gaia-skill-link-") as tmp:
        root, pin = fetch_library(source, Path(tmp))
        scanned = _scan(root, source, manager)
    return LinkPreview(
        link=source.link,
        source=source.repo or source.download_url,
        skills=[entry for entry, _ in scanned.values()],
        pin=pin,
    )


def _match(scanned: Dict[str, tuple], wanted: str) -> Optional[str]:
    """The scanned key for *wanted*, by skill name or by folder name."""
    if wanted in scanned:
        return wanted
    for key, (entry, _directory) in scanned.items():
        if Path(entry.path).name == wanted or entry.name == wanted:
            return key
    return None


def add_from_link(
    link: str,
    names: Optional[Iterable[str]] = None,
    *,
    allow_code: bool = False,
    force: bool = False,
    pin: Optional[str] = None,
    manager: Optional[SkillManager] = None,
) -> LinkAddResult:
    """Import the skills behind *link* — all of them, or just *names*.

    Each skill is imported independently, so one refusal never stops the rest;
    refusals are returned in ``failed`` with the reason.

    Args:
        names: Skill or folder names to add; ``None`` adds every addable one.
        allow_code: Allow skills that ship ``tools.py`` or ``scripts/``. Off, they
            are refused rather than landing with code the user never agreed to.
        force: Replace a skill of the same name that came from somewhere else.
            One added earlier from this same source is always updated in place,
            which is how a library is brought up to its latest version.
        pin: The preview's ``pin``. GitHub sources are downloaded at that exact
            commit; any other source must still hash the same, or nothing is
            added — so what lands is what the user reviewed.
    """
    from gaia.skills.capture import import_bundle

    source = resolve_link(link)
    if pin and pin.startswith("commit:") and source.repo:
        owner, repo = source.repo.split("/")
        pinned = _github(source.link, owner, repo, ref=pin[len("commit:") :], rest=[])
        source.download_url = pinned.download_url
    result = LinkAddResult()
    with tempfile.TemporaryDirectory(prefix="gaia-skill-link-") as tmp:
        root, fetched = fetch_library(source, Path(tmp))
        if pin and fetched != pin:
            raise SkillLinkError(
                f"{source.link} changed since you previewed it, so nothing was "
                "added. Read the link again to review the current version."
            )
        scanned = _scan(root, source, manager)
        wanted = None
        if names is not None:
            wanted = set()
            for name in names:
                key = _match(scanned, name)
                if key is None:
                    result.failed[name] = f"No skill named '{name}' at {source.link}."
                elif key.startswith("?"):
                    result.failed[name] = scanned[key][0].error or "Could not be read."
                else:
                    wanted.add(key)
        for key, (entry, directory) in scanned.items():
            if key.startswith("?") or (wanted is not None and key not in wanted):
                if key.startswith("?") and wanted is None:
                    result.failed[entry.path] = entry.error or "Could not be read."
                continue
            if entry.error:
                result.failed[entry.name] = entry.error
                continue
            if entry.installed and not (force or entry.update):
                result.failed[entry.name] = (
                    "A skill with this name is already installed from somewhere "
                    "else. Remove it first, or pass --force to replace it."
                )
                continue
            try:
                imported = import_bundle(
                    directory,
                    origin=f"{source_key(source)}#{entry.path}",
                    manager=manager,
                    force=force or entry.update,
                    allow_experimental=allow_code,
                )
            except SkillError as exc:
                result.failed[entry.name] = str(exc)
                continue
            result.added.append(
                {
                    "name": imported.name,
                    "updated": entry.installed,
                    "path": str(imported.path),
                    "verdict": imported.verdict,
                    "has_code": imported.has_code,
                    "code_trusted": imported.code_trusted,
                    "findings": imported.review_findings,
                }
            )
    log.info(
        "Added %d skill(s) from %s; %d refused",
        len(result.added),
        link,
        len(result.failed),
    )
    return result


__all__ = [
    "FEATURED_LIBRARIES",
    "SECURITY_WARNING",
    "LinkAddResult",
    "LinkPreview",
    "LinkSkill",
    "LinkSource",
    "SkillLinkError",
    "add_from_link",
    "fetch_library",
    "find_skill_dirs",
    "preview_link",
    "resolve_link",
    "source_key",
]
