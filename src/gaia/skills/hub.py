# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Client for the skills lane of the Agent Hub (the Cloudflare Worker + R2).

The skills lane is a *fourth lane of the existing hub*, not a second registry, so
this module speaks the contract PR #2668 locked in:

============================================  =============================================
Endpoint                                       Use
============================================  =============================================
``GET  /index.json``                           the whole catalog; skills are the
                                               entries with ``type: "skill"``
``GET  /skills/<name>/manifest.json``          every published version + artifact
                                               (``sha256``, size, R2 path)
``GET  /skills/<name>/<version>/SKILL.md``     the raw ``SKILL.md`` for a version
``GET  /skills/<name>/<version>/<filename>``   the bundle artifact
``POST /publish/skill``                        multipart publish (Bearer auth)
============================================  =============================================

**The installer reads ``SKILL.md`` from R2, never from the catalog.** A skill
entry's ``readme`` field carries the instruction body for the hub page to render,
but the Worker strips its front matter to build it — so it is a *rendering* of the
skill, not the skill. Installing from it would drop the manifest entirely. The
raw object is the source of truth, and this module's :func:`fetch_skill_doc` is
the only path the installer uses.

Catalog reads reuse :mod:`gaia.hub.catalog` (its cache, its offline fallback, its
``skill_entries`` filter) rather than re-fetching ``index.json`` independently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from gaia.hub.catalog import (
    Fetcher,
    fetch_bytes,
    get_hub_base_url,
    load_index,
    skill_entries,
)
from gaia.logger import get_logger
from gaia.skills.errors import SkillError, SkillNotFoundError, SkillValidationError
from gaia.skills.versions import resolve as resolve_version_spec
from gaia.skills.versions import validate_spec as validate_version_spec

log = get_logger(__name__)

#: Upload timeout for the multipart publish (seconds). Generous — a bundle plus
#: its SKILL.md crosses the wire before the Worker validates and reindexes.
PUBLISH_TIMEOUT = 120

_DOCS = "https://amd-gaia.ai/docs/plans/skill-format"


class SkillHubError(SkillError):
    """A hub request failed, or the hub rejected a publish."""


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def skill_manifest_url(name: str, base_url: Optional[str] = None) -> str:
    return f"{base_url or get_hub_base_url()}/skills/{name}/manifest.json"


def skill_doc_url(name: str, version: str, base_url: Optional[str] = None) -> str:
    """URL of the raw ``SKILL.md`` for one published version."""
    return f"{base_url or get_hub_base_url()}/skills/{name}/{version}/SKILL.md"


def skill_artifact_url(
    name: str, version: str, filename: str, base_url: Optional[str] = None
) -> str:
    return f"{base_url or get_hub_base_url()}/skills/{name}/{version}/{filename}"


def publish_url(base_url: Optional[str] = None) -> str:
    return f"{base_url or get_hub_base_url()}/publish/skill"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillSearchResult:
    """Matching skill entries plus where the catalog came from.

    ``offline`` is part of the result, not a log line, because a stale catalog is
    actionably different from a fresh one: a skill listed from cache may have been
    unpublished, and one published since will be missing. Presenting cached
    results as current is the quiet-wrong-answer failure mode.

    ``offline`` alone does not say *how* stale, though — a cache from ten minutes
    ago and one from three months ago look the same to a caller. ``age_seconds``
    and ``stale`` answer that.
    """

    entries: list[dict[str, Any]]
    offline: bool = False
    generated_at: Optional[str] = None
    #: Seconds since the catalog behind these results came off the network.
    age_seconds: Optional[float] = None
    #: True past :data:`gaia.hub.catalog.CACHE_STALE_AFTER_SECONDS`.
    stale: bool = False

    @property
    def age_text(self) -> str:
        """The catalog's age as a phrase ("3 days ago")."""
        from gaia.hub.catalog import describe_age

        return describe_age(self.age_seconds)

    def __iter__(self):
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def search_skills(
    query: str = "",
    *,
    base_url: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
    cache_path: Optional[Path] = None,
    force: bool = False,
) -> SkillSearchResult:
    """Catalog skill entries matching *query* (empty query = every skill).

    Matches case-insensitively against name, description, and declared tool
    names — the three things a user would type. Ordering is catalog order (by id),
    so results are stable between runs.

    Args:
        query: Substring to match; empty returns the whole skills lane.
        base_url: Hub origin override (defaults to ``GAIA_HUB_URL``).
        fetcher: Injected transport (see :data:`gaia.hub.catalog.Fetcher`).
        cache_path: Offline-cache location; pass a temp path in tests so the
            user's real ``~/.gaia/catalog-cache.json`` is never touched.
        force: Bypass the in-process TTL cache.

    Raises:
        SkillHubError: the catalog could not be produced at all — no network and
            no usable cache. Translated from
            :class:`gaia.hub.catalog.CatalogError`, which is a plain
            ``RuntimeError`` and would otherwise escape the CLI's error handling
            as a traceback.
    """
    from gaia.hub.catalog import CatalogError

    try:
        index = load_index(
            base_url=base_url, fetcher=fetcher, cache_path=cache_path, force=force
        )
    except CatalogError as exc:
        raise SkillHubError(
            f"Could not reach the Agent Hub catalog and no offline copy is "
            f"available: {exc} Check your network, or set GAIA_HUB_URL if you are "
            "pointing at a private hub."
        ) from exc

    entries = skill_entries(index.agents)

    needle = query.strip().lower()

    def haystack(entry: dict[str, Any]) -> str:
        metadata = entry.get("skill_metadata") or {}
        tools = " ".join(
            str(t.get("name", ""))
            for t in (metadata.get("tools") or [])
            if isinstance(t, dict)
        )
        return " ".join(
            [
                str(entry.get("id", "")),
                str(entry.get("name", "")),
                str(entry.get("description", "")),
                tools,
            ]
        ).lower()

    matched = (
        entries
        if not needle
        else [entry for entry in entries if needle in haystack(entry)]
    )
    if index.offline:
        log.warning(
            "Serving %d skill(s) from the offline catalog cache, last refreshed %s "
            "(hub generated it %s) — the hub was unreachable, so this list may be "
            "stale",
            len(matched),
            index.age_text,
            index.generated_at or "unknown",
        )
    return SkillSearchResult(
        entries=matched,
        offline=index.offline,
        generated_at=index.generated_at,
        age_seconds=index.age_seconds,
        stale=index.stale,
    )


# ---------------------------------------------------------------------------
# Per-skill manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteArtifact:
    """One published artifact, as recorded in the per-skill manifest."""

    filename: str
    sha256: str
    size_bytes: int = 0
    path: str = ""
    content_type: str = ""


@dataclass(frozen=True)
class RemoteSkill:
    """A skill's published manifest at ``skills/<name>/manifest.json``."""

    name: str
    latest_version: str
    versions: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    author: str = ""
    license: str = ""
    security_tier: str = ""
    permissions: list[str] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)

    def resolve(self, spec: Optional[str]) -> str:
        """Highest published version satisfying *spec*.

        Raises:
            SkillValidationError: *spec* is not a range GAIA can read. Checked
                before the candidate list, so an unreadable pin is reported as
                such even for a skill with nothing published yet.
            SkillNotFoundError: when no published version satisfies it. The error
                lists what *is* published, which is what the user needs to
                correct the pin.
        """
        validate_version_spec(spec)
        available = sorted(self.versions)
        chosen = resolve_version_spec(available, spec)
        if chosen is None:
            wanted = spec or "*"
            raise SkillNotFoundError(
                f"No published version of skill '{self.name}' satisfies {wanted!r}. "
                f"Published: {', '.join(available) or '(none)'}. Loosen the pin, or "
                f"run 'gaia skill info {self.name} --remote' to see the versions. "
                f"See {_DOCS}"
            )
        return chosen

    def artifact(self, version: str) -> RemoteArtifact:
        """The artifact record for *version*.

        Raises:
            SkillNotFoundError: for an unpublished version.
            SkillValidationError: when the manifest carries no usable artifact —
                installing without a checksum to verify against is not an option.
        """
        entry = self.versions.get(version)
        if not isinstance(entry, dict):
            raise SkillNotFoundError(
                f"Skill '{self.name}' has no published version {version}. "
                f"Published: {', '.join(sorted(self.versions)) or '(none)'}."
            )
        raw = entry.get("artifact")
        if not isinstance(raw, dict):
            artifacts = entry.get("artifacts") or []
            raw = artifacts[0] if artifacts and isinstance(artifacts[0], dict) else None
        if (
            not isinstance(raw, dict)
            or not raw.get("filename")
            or not raw.get("sha256")
        ):
            raise SkillValidationError(
                f"The hub manifest for skill '{self.name}' {version} has no artifact "
                "with a filename and sha256, so the download cannot be verified. "
                "This is a hub-side problem — report it with the skill name and "
                "version; GAIA will not install an unverifiable artifact."
            )
        return RemoteArtifact(
            filename=str(raw["filename"]),
            sha256=str(raw["sha256"]),
            size_bytes=int(raw.get("size_bytes") or 0),
            path=str(raw.get("path") or ""),
            content_type=str(raw.get("content_type") or ""),
        )


def fetch_skill_manifest(
    name: str, *, base_url: Optional[str] = None, fetcher: Optional[Fetcher] = None
) -> RemoteSkill:
    """Fetch and parse ``skills/<name>/manifest.json``.

    Raises:
        SkillNotFoundError: the hub has no such skill (or it is unreachable — the
            message covers both, because from the client they look the same).
    """
    get = fetcher or fetch_bytes
    url = skill_manifest_url(name, base_url)
    try:
        raw = get(url)
    except Exception as exc:  # noqa: BLE001 - re-raised with context
        raise SkillNotFoundError(
            f"Could not fetch the hub manifest for skill '{name}' from {url}: {exc}. "
            f"Check the name with 'gaia skill search {name}', confirm the hub is "
            "reachable, or set GAIA_HUB_URL if you are pointing at a private hub."
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkillHubError(
            f"The hub returned invalid JSON for skill '{name}' ({url}): {exc}. "
            "Retry; if it persists the hub is serving a corrupt manifest — report "
            "it with the skill name."
        ) from exc

    if not isinstance(data, dict) or not data.get("name"):
        raise SkillHubError(
            f"The hub manifest for skill '{name}' ({url}) is missing its 'name' "
            "field, so it cannot be trusted to describe this skill."
        )
    versions = data.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise SkillNotFoundError(
            f"Skill '{name}' exists in the hub but has no published versions yet, so "
            "there is nothing to install."
        )

    return RemoteSkill(
        name=str(data["name"]),
        latest_version=str(data.get("latest_version") or ""),
        versions=versions,
        description=str(data.get("description") or ""),
        author=str(data.get("author") or ""),
        license=str(data.get("license") or ""),
        security_tier=str(data.get("security_tier") or ""),
        permissions=[str(p) for p in (data.get("permissions") or [])],
        audit=data.get("audit") or {},
    )


def fetch_skill_doc(
    name: str,
    version: str,
    *,
    base_url: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
) -> str:
    """Fetch the raw ``SKILL.md`` for one published version.

    This is the authoritative manifest — front matter included. The catalog's
    ``readme`` field is a front-matter-stripped rendering and must never be used
    in its place (see the module docstring).
    """
    get = fetcher or fetch_bytes
    url = skill_doc_url(name, version, base_url)
    try:
        raw = get(url)
    except Exception as exc:  # noqa: BLE001 - re-raised with context
        raise SkillHubError(
            f"Could not fetch SKILL.md for '{name}' {version} from {url}: {exc}. The "
            "version's manifest is missing from the hub, or the hub is unreachable. "
            "Retry, or pick another version with "
            f"'gaia skill info {name} --remote'."
        ) from exc
    text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    if not text.strip():
        raise SkillHubError(
            f"The hub served an empty SKILL.md for '{name}' {version} ({url}). A "
            "skill without a manifest cannot be installed — report this with the "
            "skill name and version."
        )
    return text


def download_artifact(
    name: str,
    version: str,
    artifact: RemoteArtifact,
    destination: Path,
    *,
    base_url: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
) -> Path:
    """Download an artifact to *destination* and verify its SHA-256.

    Verification happens before the bytes are handed to any unpacker, so a
    corrupted or substituted artifact is never extracted.

    Raises:
        SkillHubError: on a transport failure or a checksum mismatch.
    """
    import hashlib

    get = fetcher or fetch_bytes
    url = skill_artifact_url(name, version, artifact.filename, base_url)
    try:
        payload = get(url)
    except Exception as exc:  # noqa: BLE001 - re-raised with context
        raise SkillHubError(
            f"Could not download the bundle for '{name}' {version} from {url}: "
            f"{exc}. Check your network and retry."
        ) from exc

    actual = hashlib.sha256(payload).hexdigest()
    if actual != artifact.sha256:
        raise SkillHubError(
            f"Checksum mismatch for '{name}' {version}: the hub manifest says "
            f"sha256={artifact.sha256} but the downloaded bundle hashes to "
            f"{actual}. Nothing was installed. Retry — if it persists, the artifact "
            "has been tampered with or corrupted in transit; report it with the "
            "skill name and version."
        )

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    log.info(
        "Downloaded skill bundle %s (%d bytes, sha256 verified)", url, len(payload)
    )
    return destination


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishRequest:
    """The exact multipart body ``POST /publish/skill`` receives.

    Materialized as a value object so a test can assert the *shape* of what would
    be sent — the parts present, the artifact filename, the SKILL.md text — rather
    than only that some upload function was called. The Worker rejects a body
    missing ``skill`` or ``artifact`` (400) and one whose artifact filename is not
    a single safe path segment (400 ``invalid_artifact``), so the shape is the
    contract, not an implementation detail.
    """

    #: Full SKILL.md: YAML front matter + instruction body.
    skill_markdown: str
    #: Bundle filename, e.g. ``web-research-1.2.0.zip`` (one path segment).
    artifact_filename: str
    artifact_bytes: bytes = field(repr=False, default=b"")
    changelog: Optional[str] = None
    #: Serialized audit report (#2468) — the ``audit`` part.
    audit_json: Optional[str] = None

    def parts(self) -> dict[str, Any]:
        """``{part-name: value}`` for every part that will be sent."""
        payload: dict[str, Any] = {
            "skill": self.skill_markdown,
            "artifact": self.artifact_filename,
        }
        if self.changelog is not None:
            payload["changelog"] = self.changelog
        if self.audit_json is not None:
            payload["audit"] = self.audit_json
        return payload


#: Uploader signature: ``(url, request, token) -> response dict``. Injected in tests.
Uploader = Callable[[str, PublishRequest, str], dict[str, Any]]


def upload_publish(url: str, request: PublishRequest, token: str) -> dict[str, Any]:
    """POST a :class:`PublishRequest` as ``multipart/form-data`` and return the JSON.

    Raises:
        SkillHubError: on a transport failure or a non-2xx response. The Worker's
            ``error``/``message`` body is surfaced verbatim — it already names what
            to fix (``audit_required``, ``id_conflict``, ``version_exists``, …).
    """
    import requests

    files = {
        "skill": (None, request.skill_markdown, "text/markdown; charset=utf-8"),
        "artifact": (
            request.artifact_filename,
            request.artifact_bytes,
            "application/zip",
        ),
    }
    if request.changelog is not None:
        files["changelog"] = (None, request.changelog, "text/markdown; charset=utf-8")
    if request.audit_json is not None:
        files["audit"] = (None, request.audit_json, "application/json; charset=utf-8")

    try:
        response = requests.post(
            url,
            files=files,
            headers={"Authorization": f"Bearer {token}"},
            timeout=PUBLISH_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - requests.RequestException and friends
        raise SkillHubError(
            f"Could not reach the hub at {url}: {exc}. Check your network, and "
            "confirm GAIA_HUB_URL points at a running Worker."
        ) from exc

    if response.status_code >= 400:
        raise SkillHubError(_publish_error(url, response))

    try:
        return response.json()
    except ValueError as exc:
        raise SkillHubError(
            f"The hub accepted the publish ({response.status_code}) but returned a "
            f"non-JSON body from {url}. Verify with "
            "'gaia skill search <name>' before assuming it landed."
        ) from exc


def _publish_error(url: str, response: Any) -> str:
    """Turn a Worker error response into an actionable message."""
    code = ""
    message = ""
    try:
        body = response.json()
        code = str(body.get("error") or "")
        message = str(body.get("message") or "")
    except ValueError:
        message = (response.text or "")[:500]

    hint = {
        "audit_required": (
            "Attach a cleared audit report, or publish as 'experimental'."
        ),
        "audit_blocked": "Fix the audit findings and re-run 'gaia skill publish'.",
        "audit_review_required": "The audit needs maintainer sign-off first.",
        "id_conflict": (
            "That name is taken by an agent package — skills and agents share one "
            "id namespace. Rename the skill."
        ),
        "version_exists": (
            "Published versions are immutable. Bump 'version:' in SKILL.md."
        ),
        "forbidden_scope": (
            "Another publisher owns this skill name. Use a different name, or "
            "publish with the owning token."
        ),
        "unauthorized": (
            "The hub rejected your token. Refresh it with 'gaia agent login', or "
            "set GAIA_HUB_TOKEN."
        ),
        "invalid_skill_manifest": (
            "Fix the SKILL.md field the message names, then re-run publish."
        ),
    }.get(code, "")

    return " ".join(
        part
        for part in (
            f"The hub refused the publish ({response.status_code}"
            f"{f' {code}' if code else ''}) at {url}.",
            message,
            hint,
        )
        if part
    )
