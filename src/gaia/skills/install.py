# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``gaia skill install`` / ``remove`` — resolve, verify, gate, then write.

Every check runs **before** anything lands in the skills root, so a refused skill
leaves no partial install behind (the same property the loader guarantees for
tools). In order:

1. **Resolve** the version from the hub's per-skill manifest against the SemVer
   range the caller pinned (:mod:`gaia.skills.versions`).
2. **Download + checksum** the bundle against ``versions[v].artifact.sha256``.
3. **Unpack** into a temp dir, refusing any archive entry that escapes it.
4. **Parse** the raw ``SKILL.md`` — fetched from ``GET
   /skills/<name>/<version>/SKILL.md``, never from the catalog's ``readme``,
   which is a front-matter-stripped rendering.
5. **Verify the signature** over every bundled file, and resolve the trust role of
   the signing key (:mod:`gaia.skills.signing`).
6. **Collapse the tier** to ``min(claimed, attested)`` — this is what makes "no
   unsigned skill installs as ``verified``" structural rather than a rule someone
   has to remember.
7. **Refuse un-bridged permissions.** v1 bridges connector-backed domains only; a
   skill wanting ``filesystem``/``shell``/``database``/``desktop``/``env`` is
   refused, because the sandbox that would contain it does not exist yet (#888).
   A tier stamp is not a substitute for it.
8. **Enforce the permission ceiling** for the effective tier, then prompt for
   dangerous grants (``community``) or require ``--allow-experimental``.
9. **Copy in and re-stamp** the effective tier into the installed ``SKILL.md``,
   and record the whole decision in ``skill-lock.json`` — including a digest of
   the bytes that landed, which is what lets :mod:`gaia.skills.drift` notice if
   they change afterwards.

Steps 4/9 reuse ``gaia skill import``'s materialize → parse → copy → re-stamp
shape on purpose: one save path with one security behavior. Import always stamps
``experimental`` (it has no provenance to reason about); install stamps the tier
the signature earned.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from gaia.hub.catalog import Fetcher, get_hub_base_url
from gaia.logger import get_logger
from gaia.skills.audit.findings import content_digest
from gaia.skills.errors import SkillError, SkillNotFoundError, SkillValidationError
from gaia.skills.format import (
    SKILL_FILENAME,
    SKILL_TOOLS_FILENAME,
    Skill,
    parse_skill,
    validate_skill,
)
from gaia.skills.hub import (
    download_artifact,
    fetch_skill_doc,
    fetch_skill_manifest,
)
from gaia.skills.lock import SOURCE_HUB, LockEntry, SkillLock
from gaia.skills.manager import SkillManager
from gaia.skills.permissions import refuse_unbridged_permissions
from gaia.skills.signing import (
    SIGNATURE_FILENAME,
    TrustStore,
    VerifiedSignature,
    attested_tier,
    verify_bundle,
)
from gaia.skills.tiers import (
    LOWEST_TIER,
    dangerous_grants,
    effective_tier,
    enforce_tier_ceiling,
)

log = get_logger(__name__)

_DOCS = "https://amd-gaia.ai/docs/plans/skill-format#security-tiers"

#: Confirmation callback: ``(prompt) -> approved``. Defaults to an interactive
#: prompt; ``--yes`` substitutes an auto-approve, and a non-TTY refuses.
Confirmer = Callable[[str], bool]


class SkillInstallError(SkillError):
    """An install was refused, or could not complete."""


def parse_skill_ref(reference: str) -> tuple[str, str]:
    """Split ``name[@version-range]`` into ``(name, spec)``.

    ``"web-research"`` → ``("web-research", "*")``;
    ``"web-research@^1.2"`` → ``("web-research", "^1.2")``.
    """
    text = (reference or "").strip()
    if not text:
        raise SkillValidationError(
            "No skill named. Use 'gaia skill install <name>' or "
            "'<name>@<version-range>', e.g. 'web-research@^1.0'."
        )
    if "@" not in text:
        return text, "*"
    name, _, spec = text.partition("@")
    name, spec = name.strip(), spec.strip()
    if not name or not spec:
        raise SkillValidationError(
            f"Could not read the skill reference {reference!r}. Expected "
            "'<name>@<version-range>', e.g. 'web-research@^1.0' or "
            "'web-research@1.2.3'."
        )
    return name, spec


@dataclass
class InstallResult:
    """What an install actually did — the CLI and the UI both render this."""

    name: str
    version: str
    path: Path
    requested: str = "*"
    claimed_tier: str = ""
    attested_tier: str = ""
    installed_tier: str = ""
    signature: Optional[VerifiedSignature] = None
    permissions: list[str] = field(default_factory=list)
    replaced_version: str = ""

    @property
    def downgraded(self) -> bool:
        """True when the signature did not support the tier the skill claimed."""
        return bool(self.claimed_tier) and self.claimed_tier != self.installed_tier


def _interactive_confirm(prompt: str) -> bool:
    """Ask on the terminal; refuse when there is no terminal to ask."""
    import sys

    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def install_skill(
    reference: str,
    *,
    manager: Optional[SkillManager] = None,
    base_url: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
    allow_experimental: bool = False,
    force: bool = False,
    confirm: Optional[Confirmer] = None,
    assume_yes: bool = False,
) -> InstallResult:
    """Install ``name[@version-range]`` from the hub into the user skills root.

    Args:
        reference: ``name`` or ``name@<semver-range>``.
        manager: Supplies the destination root; defaults to the standard roots.
        base_url: Hub origin override.
        fetcher: Injected transport for catalog/artifact reads.
        allow_experimental: Required to install a skill whose effective tier is
            ``experimental`` — unattested code does not install by accident.
        force: Replace an already-installed copy.
        confirm: Prompt callback for dangerous grants at ``community``.
        assume_yes: Pre-approve dangerous grants (``--yes``, for CI).

    Returns:
        An :class:`InstallResult` describing the version, tier, and signature.

    Raises:
        SkillInstallError: the install was refused (tier opt-in missing, a
            dangerous grant declined, or the skill is already installed).
        SkillPermissionError: a permission is un-bridged or above the ceiling.
        SkillSignatureError: a present signature does not verify.
        SkillNotFoundError: no published version satisfies the pin.
    """
    name, spec = parse_skill_ref(reference)
    resolver = manager if manager is not None else SkillManager()
    destination_root = resolver.user_root
    target = destination_root / name

    lock = SkillLock.load(destination_root)
    previous = lock.get(name)
    if target.exists() and not force:
        installed = f" (version {previous.version})" if previous else ""
        raise SkillInstallError(
            f"Skill '{name}' is already installed at {target}{installed}. Pass "
            f"--force to replace it, or remove it first with "
            f"'gaia skill remove {name}'."
        )

    remote = fetch_skill_manifest(name, base_url=base_url, fetcher=fetcher)
    version = remote.resolve(spec)
    artifact = remote.artifact(version)
    log.info("Resolved skill '%s' %s from pin %r", name, version, spec)

    confirmer = confirm or (
        (lambda _prompt: True) if assume_yes else _interactive_confirm
    )

    with tempfile.TemporaryDirectory(prefix="gaia-skill-install-") as tmp:
        workdir = Path(tmp)
        bundle = download_artifact(
            name,
            version,
            artifact,
            workdir / artifact.filename,
            base_url=base_url,
            fetcher=fetcher,
        )
        source_dir = _unpack_bundle(bundle, workdir / "unpacked", name=name)

        # The raw SKILL.md from R2 is authoritative. The bundle ships one too; if
        # they disagree, the bundle was repacked after publish and we stop.
        skill_markdown = fetch_skill_doc(
            name, version, base_url=base_url, fetcher=fetcher
        )
        skill = parse_skill(skill_markdown, source=f"hub:{name}@{version}")
        validate_skill(skill, source=f"hub:{name}@{version}")
        _assert_matches_bundle(skill, source_dir, name=name, version=version)

        if skill.name != name:
            raise SkillValidationError(
                f"The hub served SKILL.md for '{skill.name}' under the name "
                f"'{name}'. Refusing to install a skill whose manifest disagrees "
                "with the catalog — report this with both names."
            )
        if skill.version and skill.version != version:
            raise SkillValidationError(
                f"The SKILL.md the hub served for '{name}' {version} declares "
                f"version {skill.version!r}. Refusing to install a version "
                "mismatch — report this with the skill name and both versions."
            )

        signature = verify_bundle(
            source_dir,
            name=name,
            version=version,
            trust_store=TrustStore.load(destination_root),
        )
        claimed = skill.security_tier
        attested = attested_tier(signature)
        tier = effective_tier(claimed, attested)
        log.info(
            "Skill '%s' %s: claimed=%s attested=%s effective=%s (%s)",
            name,
            version,
            claimed,
            attested,
            tier,
            signature.describe(),
        )

        _gate_tier(
            skill,
            tier=tier,
            claimed=claimed,
            signature=signature,
            allow_experimental=allow_experimental,
            confirmer=confirmer,
        )

        if target.exists():
            shutil.rmtree(target)
        destination_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, target)

        # Re-stamp only when the signature did not support the claim, so
        # `gaia skill info` and the loader read the enforced tier rather than the
        # publisher's. Rewriting SKILL.md invalidates the bundled
        # SIGNATURE.json's digest for that file, which is why an install that
        # honored the claim is left byte-identical and still verifiable; a
        # downgraded one records its verified provenance in the lock instead.
        if tier != claimed:
            skill.gaia.security_tier = tier
            skill.path = target / SKILL_FILENAME
            skill.write(target / SKILL_FILENAME)
            log.info(
                "Re-stamped '%s' %s from claimed tier '%s' to enforced '%s'; the "
                "bundled %s no longer matches the rewritten %s (its verified "
                "provenance is recorded in the skill lock)",
                name,
                version,
                claimed,
                tier,
                SIGNATURE_FILENAME,
                SKILL_FILENAME,
            )

    # Hashed after the re-stamp so the digest describes the bytes that landed,
    # not the ones in the archive. `gaia skill lock` compares against this.
    installed_digest = content_digest(target)

    lock.record(
        LockEntry(
            name=name,
            version=version,
            requested=spec,
            source=SOURCE_HUB,
            # Record the origin actually fetched from, not the flag: `base_url` is
            # None whenever the hub came from GAIA_HUB_URL or the default, and
            # "which hub did this come from" is the provenance the lock exists for.
            hub_url=base_url or get_hub_base_url(),
            artifact_sha256=artifact.sha256,
            artifact_filename=artifact.filename,
            content_digest=installed_digest,
            claimed_tier=claimed,
            attested_tier=attested,
            installed_tier=tier,
            signature={
                "signed": signature.signed,
                "key_id": signature.key_id,
                "publisher": signature.publisher,
                "role": signature.role,
            },
            permissions=list(skill.gaia.permissions),
            path=str(target),
        )
    )
    lock.save()
    resolver.reload()

    return InstallResult(
        name=name,
        version=version,
        path=target,
        requested=spec,
        claimed_tier=claimed,
        attested_tier=attested,
        installed_tier=tier,
        signature=signature,
        permissions=list(skill.gaia.permissions),
        replaced_version=previous.version if previous else "",
    )


def _gate_tier(
    skill: Skill,
    *,
    tier: str,
    claimed: str,
    signature: VerifiedSignature,
    allow_experimental: bool,
    confirmer: Confirmer,
) -> None:
    """Run every tier and permission gate. Raises rather than downgrading."""
    permissions = skill.parsed_permissions()

    # v1 has no local-capability sandbox, so a skill wanting one is refused at
    # every tier. A 'verified' stamp does not conjure an enforcement layer.
    refuse_unbridged_permissions(permissions, skill_name=skill.name)

    enforce_tier_ceiling(permissions, tier=tier, skill_name=skill.name)

    if tier == LOWEST_TIER and not allow_experimental:
        why = (
            f"its signature attests only to '{tier}' ({signature.describe()})"
            if claimed != tier
            else "it is published at that tier"
        )
        # Name the concrete risk when the skill ships code. "Unsandboxed" is
        # abstract; "its tools.py runs in your agent's process" is the decision the
        # user is actually being asked to make.
        code_warning = (
            f" This skill ships {SKILL_TOOLS_FILENAME} providing "
            f"{', '.join(skill.tool_names)} — that Python is imported and run "
            "in your agent's own process, with your agent's access, the first "
            "time the skill loads."
            if skill.gaia.tools
            else " This skill is instruction-only: it ships no code, but its "
            "instructions still reach the model."
        )
        raise SkillInstallError(
            f"Skill '{skill.name}' installs at '{LOWEST_TIER}' because {why}. "
            f"Experimental skills are neither signed by a publisher GAIA trusts nor "
            f"audited, and v1 has no sandbox to contain one.{code_warning} "
            "Installing is therefore an explicit choice: re-run with "
            f"--allow-experimental if you trust the source. See {_DOCS}"
        )

    risky = dangerous_grants(permissions)
    if risky and tier != "verified":
        listed = ", ".join(str(p) for p in risky)
        approved = confirmer(
            f"Skill '{skill.name}' ({tier}) requests dangerous permission(s): "
            f"{listed}. Grant them?"
        )
        if not approved:
            raise SkillInstallError(
                f"Install of '{skill.name}' declined: the dangerous permission(s) "
                f"{listed} were not granted. Only a 'verified' (AMD-signed, "
                "audited) skill has these pre-approved; at "
                f"'{tier}' they need your explicit consent. Re-run with --yes to "
                f"grant them non-interactively. See {_DOCS}"
            )


def _assert_matches_bundle(
    skill: Skill, source_dir: Path, *, name: str, version: str
) -> None:
    """Refuse when the bundle's own SKILL.md differs from the one R2 served.

    The signature covers the bundle's copy; the R2 object is what we parse. If
    they diverge, one of them was changed after publish, and we cannot tell which
    — so neither is trustworthy.
    """
    bundled = source_dir / SKILL_FILENAME
    if not bundled.is_file():
        raise SkillValidationError(
            f"The bundle for '{name}' {version} contains no {SKILL_FILENAME}. A "
            "skill bundle must ship its manifest — refusing to install."
        )
    bundled_skill = parse_skill(
        bundled.read_text(encoding="utf-8"), source=str(bundled)
    )
    if (
        bundled_skill.name != skill.name
        or bundled_skill.security_tier != skill.security_tier
        or sorted(bundled_skill.gaia.permissions) != sorted(skill.gaia.permissions)
    ):
        raise SkillValidationError(
            f"The {SKILL_FILENAME} inside the bundle for '{name}' {version} "
            "disagrees with the one the hub serves (name, security_tier, or "
            "permissions differ). One of them was modified after publish — "
            "refusing to install either. Report this with the skill name and "
            "version."
        )


def _unpack_bundle(archive: Path, destination: Path, *, name: str) -> Path:
    """Extract a skill bundle, refusing traversal, and return its root."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                resolved = (destination / member.filename).resolve()
                if resolved != root and root not in resolved.parents:
                    raise SkillValidationError(
                        f"Refusing to extract the bundle for '{name}': entry "
                        f"{member.filename!r} escapes the destination directory. The "
                        "bundle is malformed or hostile; nothing was installed."
                    )
            bundle.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise SkillValidationError(
            f"The bundle downloaded for '{name}' is not a valid .zip: {exc}. Retry "
            "the install; if it persists the published artifact is corrupt — report "
            "it with the skill name and version."
        ) from exc

    if (destination / SKILL_FILENAME).is_file():
        return destination
    candidates = sorted(destination.glob(f"*/{SKILL_FILENAME}"))
    if len(candidates) == 1:
        return candidates[0].parent
    if not candidates:
        raise SkillValidationError(
            f"No {SKILL_FILENAME} in the bundle for '{name}'. A published skill "
            f"bundle must contain {SKILL_FILENAME} at its root or one level down."
        )
    found = ", ".join(c.parent.name for c in candidates)
    raise SkillValidationError(
        f"The bundle for '{name}' contains more than one skill ({found}). A "
        "published bundle must hold exactly one."
    )


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


@dataclass
class RemoveResult:
    """What ``gaia skill remove`` deleted."""

    name: str
    path: Path
    version: str = ""
    was_locked: bool = False


def remove_skill(name: str, *, manager: Optional[SkillManager] = None) -> RemoveResult:
    """Delete an installed skill from the user root and drop its lock entry.

    Only the writable user root is touched. An agent-bundled or Claude-imported
    skill is refused with the reason — deleting files inside an installed agent
    package or a user's ``.claude/skills`` is not this command's business.

    Raises:
        SkillNotFoundError: nothing of that name is installed in the user root.
        SkillInstallError: the skill exists but lives in a read-only root.
    """
    resolver = manager if manager is not None else SkillManager()
    root = resolver.user_root
    target = root / name

    if not target.is_dir():
        discovered = resolver.discover().get(name)
        if discovered is not None:
            raise SkillInstallError(
                f"Skill '{name}' is not installed in {root} — it comes from the "
                f"'{discovered.root}' root ({discovered.directory}). "
                "'gaia skill remove' only deletes skills you installed. Remove an "
                "agent-bundled skill by uninstalling its agent; a Claude Code "
                "import by deleting it from .claude/skills/."
            )
        raise SkillNotFoundError(
            f"No installed skill named '{name}' in {root}. List what is installed "
            "with 'gaia skill list'."
        )

    lock = SkillLock.load(root)
    entry = lock.get(name)
    shutil.rmtree(target)
    was_locked = lock.forget(name)
    if was_locked:
        lock.save()
    resolver.reload()
    log.info("Removed skill '%s' from %s", name, target)

    return RemoveResult(
        name=name,
        path=target,
        version=entry.version if entry else "",
        was_locked=was_locked,
    )


def installed_provenance(manager: Optional[SkillManager] = None) -> dict[str, Any]:
    """``{name: lock-entry-dict}`` for every skill the lock tracks.

    Feeds ``gaia skill list --json`` and the (future) ``/api/skills`` router, so
    both read provenance from the lock rather than re-deriving it. Each entry
    carries its own ``source`` — ``hub`` for an install, ``local`` for a skill
    ``gaia skill lock --relock`` started tracking — so a caller that only wants
    hub provenance filters on it rather than assuming it.
    """
    resolver = manager if manager is not None else SkillManager()
    lock = SkillLock.load(resolver.user_root)
    return {name: entry.to_dict() for name, entry in lock.entries.items()}


__all__ = [
    "Confirmer",
    "InstallResult",
    "RemoveResult",
    "SkillInstallError",
    "install_skill",
    "installed_provenance",
    "parse_skill_ref",
    "remove_skill",
]
