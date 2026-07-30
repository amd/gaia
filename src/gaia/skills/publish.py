# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``gaia skill publish ./my-skill/`` — validate, audit, sign, upload.

The order matters, and each step gates the next:

1. **Validate** the source against the real parser, so a skill that would fail to
   load can never enter the catalog.
2. **Refuse un-bridged permissions.** Publishing a skill that no GAIA release can
   load would be publishing a brick (#888 defers the local-capability sandbox).
3. **Audit** (#2468, via :mod:`gaia.skills.audit_gate`). If the engine is not
   installed and no report was supplied, publish **stops** — it does not proceed
   un-audited. The hub gates ``community``/``verified`` on that report, so
   fabricating one would launder an unscanned skill past the gate.
4. **Sign** the staged bundle with a publisher Ed25519 key, embedding
   ``SIGNATURE.json``. Signing is what a skill's tier rests on at install time:
   an unsigned bundle installs at ``experimental`` no matter what its front matter
   claims, so publishing ``community`` unsigned would ship a claim nobody honors —
   hence :func:`publish_skill` refuses that combination up front rather than
   letting the publisher find out from a user's install log.
5. **Package** the signed directory into ``<name>-<version>.zip``.
6. **Upload** to ``POST /publish/skill`` as multipart (``skill`` / ``artifact`` /
   ``changelog`` / ``audit``) with a Bearer token — the contract from PR #2668.

The Worker re-validates everything server-side; these checks exist so the
publisher gets the actionable message *before* a failed upload, not after.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from gaia.logger import get_logger
from gaia.skills.audit_gate import AuditReport, gate_for_publish
from gaia.skills.errors import SkillError, SkillValidationError
from gaia.skills.format import SKILL_FILENAME, Skill, parse_skill_file, validate_skill
from gaia.skills.hub import PublishRequest, Uploader, publish_url, upload_publish
from gaia.skills.manager import user_skills_dir
from gaia.skills.permissions import refuse_unbridged_permissions
from gaia.skills.signing import SIGNATURE_FILENAME, SigningKey, load_key, sign_bundle
from gaia.skills.tiers import LOWEST_TIER

log = get_logger(__name__)

#: Files never shipped in a published bundle (build noise, VCS metadata, caches).
EXCLUDED_NAMES = frozenset({".git", ".DS_Store", "__pycache__", ".pytest_cache", ".venv"})
EXCLUDED_SUFFIXES = (".pyc", ".pyo")

#: Changelog filename picked up from the skill directory, if present.
CHANGELOG_FILENAME = "CHANGELOG.md"

_DOCS = "https://amd-gaia.ai/docs/plans/skill-format"


class SkillPublishError(SkillError):
    """A publish was refused before upload, or the hub rejected it."""


@dataclass
class PublishResult:
    """The outcome of a publish, for the CLI to render."""

    name: str
    version: str
    security_tier: str
    artifact_filename: str
    signed: bool
    key_id: str = ""
    audit: Optional[AuditReport] = None
    #: The Worker's JSON response body.
    response: dict[str, Any] = field(default_factory=dict)
    #: The exact multipart body that was sent (tests assert on its shape).
    request: Optional[PublishRequest] = None


def publish_skill(
    directory: Path,
    *,
    token: str,
    hub_url: Optional[str] = None,
    key_name: str = "publisher",
    publisher: str = "",
    unsigned: bool = False,
    audit_report: Optional[Path] = None,
    keys_root: Optional[Path] = None,
    uploader: Optional[Uploader] = None,
    dry_run: bool = False,
) -> PublishResult:
    """Validate, audit, sign, package, and upload the skill at *directory*.

    Args:
        directory: The skill source folder (must contain ``SKILL.md``).
        token: Hub publish token (Bearer). Resolve it with
            :func:`gaia.hub.publisher.get_hub_token`.
        hub_url: Hub origin override.
        key_name: Which publisher keypair to sign with.
        publisher: Publisher identity recorded in the signature (display only —
            the hub derives real ownership from the token).
        unsigned: Publish without a signature. Only legal for ``experimental``.
        audit_report: A pre-computed audit report to use instead of running the
            engine (for CI that audits in a separate step).
        keys_root: Where signing keys live; defaults to the user skills root.
        uploader: Injected ``(url, request, token) -> response``.
        dry_run: Run every gate and build the request, but do not upload.

    Returns:
        A :class:`PublishResult`, including the request that was (or would be) sent.

    Raises:
        SkillValidationError: the source is not a loadable skill, or is missing a
            publishable ``version``.
        SkillPublishError: a gate refused the publish (unsigned above
            ``experimental``, no token).
        SkillPermissionError: the skill declares an un-bridged local capability.
        SkillAuditUnavailableError / SkillAuditFailedError: the audit gate.
    """
    source = Path(directory).expanduser()
    if not source.is_dir():
        raise SkillValidationError(
            f"{source} is not a directory. Point 'gaia skill publish' at a skill "
            f"folder containing {SKILL_FILENAME}."
        )

    skill = parse_skill_file(source, check_directory_name=False)
    validate_skill(skill, source=str(source))
    _assert_publishable(skill, source)

    # A skill that no GAIA release can load must not reach the catalog.
    refuse_unbridged_permissions(skill.parsed_permissions(), skill_name=skill.name)

    tier = skill.security_tier
    if unsigned and tier != LOWEST_TIER:
        raise SkillPublishError(
            f"Skill '{skill.name}' claims security_tier '{tier}' but --unsigned was "
            f"passed. An unsigned bundle installs at '{LOWEST_TIER}' for every user "
            "regardless of what its front matter claims, so publishing this way "
            f"would ship a '{tier}' promise nobody honors. Either sign it (run "
            f"'gaia skill keygen', then publish without --unsigned) or set "
            f"security_tier: {LOWEST_TIER} in {SKILL_FILENAME}. See "
            f"{_DOCS}#security-tiers"
        )
    if not token and not dry_run:
        raise SkillPublishError(
            "No hub publish token. Set GAIA_HUB_TOKEN, or store one with "
            "'gaia agent login'. Publishing is authenticated — the token is what "
            "the hub records as the skill's owner."
        )

    # ── The audit gate (#2468) — before signing or packaging, so a BLOCKed skill
    #    never even gets a signature.
    audit = gate_for_publish(source, skill_name=skill.name, report_path=audit_report)

    key: Optional[SigningKey] = None
    if not unsigned:
        key = load_key(
            Path(keys_root) if keys_root is not None else user_skills_dir(),
            name=key_name,
        )

    with tempfile.TemporaryDirectory(prefix="gaia-skill-publish-") as tmp:
        staged = _stage(source, Path(tmp) / skill.name)
        if key is not None:
            sign_bundle(
                staged,
                name=skill.name,
                version=str(skill.version),
                key=key,
                publisher=publisher,
            )
        artifact_filename = f"{skill.name}-{skill.version}.zip"
        archive = _zip(staged, Path(tmp) / artifact_filename)

        changelog_path = source / CHANGELOG_FILENAME
        request = PublishRequest(
            # The bundle's SKILL.md is what the signature covers; send exactly
            # those bytes so the R2 object and the signed copy cannot diverge.
            skill_markdown=(staged / SKILL_FILENAME).read_text(encoding="utf-8"),
            artifact_filename=artifact_filename,
            artifact_bytes=archive.read_bytes(),
            changelog=(
                changelog_path.read_text(encoding="utf-8")
                if changelog_path.is_file()
                else None
            ),
            audit_json=audit.to_json(),
        )

    result = PublishResult(
        name=skill.name,
        version=str(skill.version),
        security_tier=tier,
        artifact_filename=artifact_filename,
        signed=key is not None,
        key_id=key.key_id if key is not None else "",
        audit=audit,
        request=request,
    )

    if dry_run:
        log.info(
            "Dry run: would publish skill '%s' %s (%d-byte bundle, tier=%s, %s)",
            skill.name,
            skill.version,
            len(request.artifact_bytes),
            tier,
            "signed" if key else "unsigned",
        )
        return result

    url = publish_url(hub_url)
    send = uploader or upload_publish
    result.response = send(url, request, token)
    log.info("Published skill '%s' %s to %s", skill.name, skill.version, url)
    return result


def _assert_publishable(skill: Skill, source: Path) -> None:
    """Refuse a skill the hub would reject for a missing or reserved version."""
    if not skill.version:
        raise SkillValidationError(
            f"Skill '{skill.name}' has no 'version:' in {source / SKILL_FILENAME}. "
            "The catalog is versioned and each version is immutable, so publishing "
            "requires a SemVer version — add e.g. 'version: 0.1.0'. See "
            f"{_DOCS}#field-reference"
        )
    if skill.version == "0.0.0":
        raise SkillValidationError(
            f"Skill '{skill.name}' is at version 0.0.0, the reserved "
            "\"unversioned\" sentinel, which cannot be published. Bump it to a real "
            f"SemVer (e.g. 0.1.0) in {source / SKILL_FILENAME}. See "
            f"{_DOCS}#field-reference"
        )


def _stage(source: Path, staged: Path) -> Path:
    """Copy the skill into a clean staging dir, dropping build noise.

    Publishing from a staging copy (never the source tree) is what keeps
    ``SIGNATURE.json`` out of the user's working directory and keeps the signed
    digest list free of ``__pycache__`` churn that would make two publishes of
    identical source produce different signatures.
    """
    shutil.copytree(source, staged, ignore=_ignore)
    stale = staged / SIGNATURE_FILENAME
    if stale.exists():
        # A signature copied in from the source tree would be signed over a
        # different digest list; drop it and sign fresh.
        stale.unlink()
    return staged


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES)
    }


def _zip(staged: Path, output: Path) -> Path:
    """Zip *staged* with the skill directory as the archive's single root entry."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(staged.rglob("*")):
            if path.is_file():
                bundle.write(path, arcname=f"{staged.name}/{path.relative_to(staged)}")
    return output
