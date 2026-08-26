# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Capture a skill from pasted ``SKILL.md`` text, a URL, or a local folder/zip.

The one primitive behind the agent's ``capture_skill`` tool (and reusable by any
CLI verb): classify the source, parse it, run the static security audit, and —
if the audit does not BLOCK — land the whole bundle in ``~/.gaia/skills`` at the
``experimental`` tier with its provenance recorded in ``skill-lock.json``.

**Code is inert until trusted.** A captured bundle's ``tools.py``/``scripts``
are copied in but never imported: the lock records ``code_trusted: false``, and
``Agent.load_skill`` injects the instructions while *deferring* tool
registration until a human runs ``gaia skill promote <name>`` in a terminal
(:func:`promote_skill`), which re-audits and — only on a clean ALLOW — flips
``code_trusted``. Instructions load immediately because they carry no
executable reach; code waits because it runs inside the agent's own process.

Everything here reuses the existing machinery: :func:`~gaia.skills.format.parse_skill`
/ :func:`~gaia.skills.format.reset_security_tier` for the schema gate,
:func:`~gaia.skills.audit.engine.audit_skill` for the (runtime-safe, never
executes or networks) audit, :func:`~gaia.skills.install._unpack_bundle` for
traversal-guarded zips, :class:`~gaia.web.client.WebClient` for SSRF-guarded
fetches, and :class:`~gaia.skills.lock.SkillLock` for provenance.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from gaia.logger import get_logger
from gaia.skills.errors import SkillError, SkillNotFoundError, SkillValidationError
from gaia.skills.format import (
    MAX_NAME_LENGTH,
    NAME_PATTERN,
    SKILL_FILENAME,
    SKILL_TOOLS_FILENAME,
    Skill,
    parse_skill,
    parse_skill_file,
    reset_security_tier,
)
from gaia.skills.lock import SOURCE_CAPTURED, LockEntry, SkillLock
from gaia.skills.manager import SkillManager
from gaia.skills.tiers import LOWEST_TIER

log = get_logger(__name__)

_DOCS = "https://amd-gaia.ai/docs/spec/agent-skills"

#: Leading bytes of every zip archive ("PK\x03\x04").
_ZIP_MAGIC = b"PK\x03\x04"

#: ``version`` recorded in the lock when the captured SKILL.md declares none.
UNVERSIONED = "unversioned"


class SkillCaptureError(SkillError):
    """A capture or promote was refused — audit BLOCK, bad source, or conflict."""


@dataclass
class CaptureResult:
    """What a capture actually did — rendered by the agent tool and the CLI."""

    name: str
    path: Path
    tier: str
    #: Where the source came from: ``url`` / ``path`` / ``text``.
    source_kind: str
    #: The URL or filesystem path captured from, or ``pasted-text``.
    origin: str
    #: True when the bundle ships ``tools.py``/``scripts`` or declares tools —
    #: i.e. there is code that stays inert until ``gaia skill promote``.
    has_code: bool
    #: A bundled ``scripts/`` dir. Tracked separately from has_code because
    #: registration gating does NOT make a script file unrunnable — the caller
    #: must be told the difference rather than sold a blanket "inert".
    has_scripts: bool = False
    #: Unqualified names of the tools the bundle declares (inert until promote).
    deferred_tools: List[str] = field(default_factory=list)
    #: Audit verdict for the capture (ALLOW or REVIEW; BLOCK never lands).
    verdict: str = "ALLOW"
    #: Rendered audit findings the caller must surface (REVIEW, or advisory
    #: medium+ findings at ALLOW). Empty for a clean capture.
    review_findings: List[str] = field(default_factory=list)
    #: Instructions are always loadable immediately — code is what waits.
    instructions_loadable: bool = True


@dataclass
class PromoteResult:
    """Outcome of :func:`promote_skill` — the CLI maps ``verdict`` to exit codes."""

    name: str
    promoted: bool
    verdict: str
    reason: str
    #: Rendered findings for a refused promote (empty on ALLOW).
    findings: List[str] = field(default_factory=list)


def _validate_name(name: str) -> str:
    """Refuse anything that is not a bare skill name (path traversal guard)."""
    text = (name or "").strip()
    if text and len(text) <= MAX_NAME_LENGTH and NAME_PATTERN.match(text):
        return text
    raise SkillCaptureError(
        f"{name!r} is not a valid skill name. Use lowercase letters and digits "
        f"separated by single hyphens (max {MAX_NAME_LENGTH} chars) — no "
        f"slashes, no '..', no path. See {_DOCS}"
    )


def _render_findings(report: Any) -> List[str]:
    """``severity: rule (file:line) — message`` per finding, worst first."""
    from gaia.skills.audit.findings import SEVERITY_ORDER

    ordered = sorted(
        report.findings,
        key=lambda f: SEVERITY_ORDER.index(f.severity),
        reverse=True,
    )
    lines = []
    for finding in ordered:
        where = f" ({finding.file}:{finding.line})" if finding.file else ""
        lines.append(
            f"{finding.severity}: {finding.rule_id}{where} — {finding.message}"
        )
    return lines


def _classify_source(source: str) -> str:
    """``url`` / ``path`` / ``text`` — by the precedence the tool documents."""
    text = source.strip()
    if text.startswith(("http://", "https://")):
        return "url"
    candidate = Path(text).expanduser()
    try:
        if candidate.is_dir() or (candidate.suffix == ".zip" and candidate.is_file()):
            return "path"
    except OSError:
        # A multi-line paste can exceed OS path limits; that is pasted text.
        pass
    return "text"


def _fetch_url(url: str, workdir: Path, web_client: Optional[Any]) -> tuple:
    """Fetch ``url`` via the SSRF-guarded WebClient.

    Returns ``("dir", skill_dir)`` for a zip bundle, ``("text", markdown)`` for
    a raw ``SKILL.md`` body.
    """
    from gaia.skills.install import _unpack_bundle

    if web_client is None:
        from gaia.web.client import WebClient

        web_client = WebClient()

    try:
        response = web_client.get(url)
    except (ValueError, IOError) as exc:
        # ValueError is the WebClient's SSRF/validation refusal; keep it loud.
        raise SkillCaptureError(
            f"Could not fetch {url}: {exc}. Only public http(s) URLs are "
            "fetchable (private/loopback addresses are refused unless the "
            "operator allowlists the host via GAIA_WEB_ALLOWED_HOSTS). Check "
            "the URL, or download the file and capture the local path instead."
        ) from exc

    content = response.content
    content_type = (response.headers.get("Content-Type") or "").lower()
    if content.startswith(_ZIP_MAGIC) or "zip" in content_type:
        archive = workdir / "capture.zip"
        archive.write_bytes(content)
        # _unpack_bundle refuses traversal and locates the SKILL.md root.
        return "dir", _unpack_bundle(archive, workdir / "unpacked", name=url)

    try:
        markdown = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillCaptureError(
            f"The response from {url} is neither a .zip bundle nor UTF-8 text, "
            "so it cannot be a SKILL.md. Point the URL at a raw SKILL.md file "
            "or a .zip skill bundle."
        ) from exc
    return "text", markdown


def _materialize_path(source: str, workdir: Path) -> Path:
    """A local folder as-is, or a local ``.zip`` unpacked with the traversal guard."""
    from gaia.skills.install import _unpack_bundle

    path = Path(source.strip()).expanduser()
    if path.is_dir():
        return path
    return _unpack_bundle(path, workdir / "unpacked", name=path.name)


def _parse_text_source(source: str, *, origin: str) -> Skill:
    """Parse pasted/fetched ``SKILL.md`` text, refusing declared-but-absent code."""
    try:
        skill = parse_skill(source, source=origin)
    except SkillValidationError as exc:
        if origin == "<pasted text>" and "\n" not in source.strip():
            raise SkillCaptureError(
                f"Could not capture {source[:120]!r}: it is not an existing "
                "file/folder, not an http(s) URL, and does not parse as pasted "
                f"SKILL.md text ({exc})"
            ) from exc
        raise
    if skill.gaia.tools:
        raise SkillCaptureError(
            f"Skill '{skill.name}' declares tool(s) "
            f"{', '.join(t.name for t in skill.gaia.tools)}, but text has no "
            f"{SKILL_TOOLS_FILENAME} to carry them. Capture the skill's folder "
            "or .zip bundle instead, or remove metadata.gaia.tools to capture "
            "it instruction-only."
        )
    return skill


def capture_skill(
    source: str,
    *,
    name: Optional[str] = None,
    manager: Optional[SkillManager] = None,
    web_client: Optional[Any] = None,
    force: bool = False,
) -> CaptureResult:
    """Capture a skill into the user skills root, code inert until promoted.

    Args:
        source: Pasted ``SKILL.md`` text, an http(s) URL (raw ``SKILL.md`` or a
            ``.zip`` bundle), or a local folder / ``.zip`` path — classified in
            that precedence (URL, then existing path, then text).
        name: Install under this name instead of the skill's own.
        manager: Supplies the destination root; defaults to the standard roots.
        web_client: Injected fetcher (tests); defaults to a fresh
            :class:`~gaia.web.client.WebClient`.
        force: Replace an already-captured copy of the same name.

    Returns:
        A :class:`CaptureResult`; ``review_findings`` is non-empty when the
        audit held findings the user must see.

    Raises:
        SkillCaptureError: the audit verdict is BLOCK, the source is unusable,
            or the name already exists. Nothing is written on a refusal.
        SkillValidationError: the ``SKILL.md`` fails the schema gate.
    """
    if not isinstance(source, str) or not source.strip():
        raise SkillCaptureError(
            "capture_skill needs a source: pasted SKILL.md text, an http(s) "
            f"URL, or a local skill folder/.zip. See {_DOCS}"
        )

    resolver = manager if manager is not None else SkillManager()
    kind = _classify_source(source)

    with tempfile.TemporaryDirectory(prefix="gaia-skill-capture-") as tmp:
        workdir = Path(tmp)
        source_dir: Optional[Path] = None

        if kind == "url":
            origin = source.strip()
            fetched_kind, fetched = _fetch_url(origin, workdir, web_client)
            if fetched_kind == "dir":
                source_dir = fetched
            else:
                skill = _parse_text_source(fetched, origin=origin)
        elif kind == "path":
            origin = str(Path(source.strip()).expanduser())
            source_dir = _materialize_path(source, workdir)
        else:
            origin = "pasted-text"
            skill = _parse_text_source(source, origin="<pasted text>")

        if source_dir is not None:
            skill = parse_skill_file(source_dir, check_directory_name=False)
            if skill.gaia.tools and not (source_dir / SKILL_TOOLS_FILENAME).is_file():
                raise SkillCaptureError(
                    f"Skill '{skill.name}' declares tool(s) "
                    f"{', '.join(t.name for t in skill.gaia.tools)} but the "
                    f"bundle ships no {SKILL_TOOLS_FILENAME}. The manifest is "
                    "the contract — fix the bundle before capturing it."
                )

        final_name = _validate_name(name or skill.name)

        # Audit at the tier the capture will land on. BLOCK refuses outright;
        # REVIEW lands but its findings ride the result for the user to see.
        # A symlinked file is skipped by the audit's source walk but is
        # DEREFERENCED by copytree, so its real bytes would land in the skills
        # root having never been scanned. Refuse the bundle instead: "BLOCK
        # refuses before anything is written" has to hold for every source.
        if source_dir is not None:
            linked = sorted(
                str(p.relative_to(source_dir))
                for p in source_dir.rglob("*")
                if p.is_symlink()
            )
            if linked:
                raise SkillCaptureError(
                    f"Capture of '{final_name}' refused: the bundle contains "
                    f"symlink(s) ({', '.join(linked[:5])}). The audit cannot "
                    "read through a link, but copying the bundle would follow "
                    "it, so the captured code would never have been scanned. "
                    "Replace the link with the real file and re-capture."
                )

        from gaia.skills.audit.engine import audit_skill_object

        report = audit_skill_object(skill, directory=source_dir, tier=LOWEST_TIER)
        if report.verdict == "BLOCK":
            findings = "\n  ".join(_render_findings(report))
            raise SkillCaptureError(
                f"Capture of '{final_name}' refused: the security audit "
                f"verdict is BLOCK ({report.reason}) Findings:\n  {findings}\n"
                "Nothing was written. Fix the skill and re-capture, or do not "
                f"capture it. See {_DOCS}"
            )

        destination_root = resolver.user_root
        target = destination_root / final_name
        if target.exists() and not force:
            raise SkillCaptureError(
                f"Skill '{final_name}' already exists at {target}. Remove it "
                f"first ('gaia skill remove {final_name}') or capture under a "
                "different name."
            )

        destination_root.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        # The bundle and its lock entry land together or not at all. The lock
        # is what marks a capture untrusted, so a bundle on disk WITHOUT one
        # reads as an ordinary skill and its tools.py would import on the next
        # load — a half-finished capture must never fail open into a trusted
        # one. Any failure below removes the directory and re-raises.
        try:
            if source_dir is not None:
                # The WHOLE bundle lands — tools.py/scripts included — so
                # promote trusts exactly the bytes that were audited.
                shutil.copytree(source_dir, target)
                landed = parse_skill_file(target, check_directory_name=False)
            else:
                target.mkdir(parents=True)
                landed = skill

            landed.name = final_name
            previous_tier = reset_security_tier(landed)
            landed.write(target / SKILL_FILENAME)

            lock = SkillLock.load(destination_root)
            lock.record(
                LockEntry(
                    name=final_name,
                    version=landed.version or UNVERSIONED,
                    requested="*",
                    source=SOURCE_CAPTURED,
                    origin=origin,
                    claimed_tier=previous_tier,
                    installed_tier=LOWEST_TIER,
                    permissions=list(landed.gaia.permissions),
                    path=str(target),
                    captured=True,
                    code_trusted=False,
                )
            )
            lock.save()
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    has_scripts = (target / "scripts").is_dir()
    has_code = bool(landed.gaia.tools) or has_scripts
    deferred = [t.name for t in landed.gaia.tools]
    resolver.reload()

    review_findings: List[str] = []
    if report.verdict == "REVIEW" or any(
        f.severity in ("medium", "high", "critical") for f in report.findings
    ):
        review_findings = _render_findings(report)

    log.info(
        "Captured skill '%s' from %s (%s) at tier '%s'%s — verdict %s",
        final_name,
        origin,
        kind,
        LOWEST_TIER,
        f", {len(deferred)} tool(s) inert until promote" if has_code else "",
        report.verdict,
    )
    return CaptureResult(
        name=final_name,
        path=target,
        tier=LOWEST_TIER,
        source_kind=kind,
        origin=origin,
        has_code=has_code,
        has_scripts=has_scripts,
        deferred_tools=deferred,
        verdict=report.verdict,
        review_findings=review_findings,
    )


# ---------------------------------------------------------------------------
# The code-inert gate + the human trust step
# ---------------------------------------------------------------------------


def capture_entry(skill: Skill) -> Optional[LockEntry]:
    """The capture lock entry for a discovered skill, or ``None``.

    Reads the lock beside the skill's own root, so only skills that live in a
    root with a ``skill-lock.json`` (the user root) can ever match.
    """
    directory = skill.directory
    if directory is None:
        return None
    entry = SkillLock.load(directory.parent).get(skill.name)
    if entry is None or not entry.captured:
        return None
    return entry


def _grants_a_binary(skill: Skill) -> bool:
    """True when the skill asks for a policied CLI (``shell:execute:<binary>``)."""
    from gaia.skills.binaries import resolve_binary_policies

    try:
        return bool(
            resolve_binary_policies(skill.parsed_permissions(), skill_name=skill.name)
        )
    except Exception:  # pylint: disable=broad-exception-caught
        # An unresolvable policy is decided by the loader's own gate, not here;
        # for the deferral question treat it as reach so we fail CLOSED.
        return True


def code_is_deferred(skill: Skill) -> bool:
    """True when this skill's executable reach must NOT be granted yet.

    A captured skill whose ``code_trusted`` is still false gets its
    instructions injected but its ``tools.py`` never imported and its binary
    grants withheld — the deferral ``gaia skill promote`` lifts. Trust is bound
    to the audited bytes: if the bundle changed since the promote, it defers
    again until the human re-promotes what is there now.

    "Reach" is tools OR a binary permission. A skill can declare zero tools and
    still ask for ``shell:execute:gh``, and an ALLOW-tier subcommand runs with
    no prompt on the premise that loading the skill was the consent — which is
    exactly the premise a pasted or fetched skill breaks.
    """
    if not skill.gaia.tools and not _grants_a_binary(skill):
        return False
    entry = capture_entry(skill)
    if entry is None:
        return False
    if not entry.code_trusted:
        return True

    from gaia.skills.audit.findings import content_digest

    directory = skill.directory
    if directory is None or content_digest(directory) != entry.code_digest:
        log.warning(
            "Captured skill '%s' changed since 'gaia skill promote' audited it "
            "— its code is deferred again until it is re-promoted.",
            skill.name,
        )
        return True
    return False


def promote_skill(
    name: str, *, manager: Optional[SkillManager] = None
) -> PromoteResult:
    """Trust a captured skill's code after a clean audit (terminal step).

    Re-runs the full static audit on the installed directory; only an ALLOW
    verdict flips ``code_trusted`` in the lock (the next ``load_skill``
    registers the tools). REVIEW/BLOCK leave the skill inert and return the
    findings for the caller to print.

    Raises:
        SkillNotFoundError: nothing of that name in the user root.
        SkillCaptureError: the skill exists but was not captured — hub installs
            and imports are not what promote governs.
    """
    from gaia.skills.audit.engine import audit_skill

    safe_name = _validate_name(name)
    resolver = manager if manager is not None else SkillManager()
    root = resolver.user_root
    target = root / safe_name

    if not target.is_dir():
        raise SkillNotFoundError(
            f"No skill named '{safe_name}' in {root}. 'gaia skill promote' "
            "trusts a captured skill's code — see what is installed with "
            "'gaia skill list'."
        )

    lock = SkillLock.load(root)
    entry = lock.get(safe_name)
    if entry is None or not entry.captured:
        raise SkillCaptureError(
            f"Skill '{safe_name}' was not captured, so there is nothing to "
            "promote. Hub installs earn trust through the install gauntlet "
            "('gaia skill install'); imported folders load through the normal "
            f"path. Promote only lifts the code deferral on captured skills. "
            f"See {_DOCS}"
        )

    report = audit_skill(target)
    if report.verdict != "ALLOW":
        # A failed promote REVOKES any earlier trust: a past ALLOW is not a
        # standing grant over bytes the audit now refuses.
        if entry.code_trusted:
            entry.code_trusted = False
            entry.code_digest = ""
            lock.record(entry)
            lock.save()
        return PromoteResult(
            name=safe_name,
            promoted=False,
            verdict=report.verdict,
            reason=report.reason,
            findings=_render_findings(report),
        )

    entry.code_trusted = True
    entry.code_digest = report.content_digest
    lock.record(entry)
    lock.save()
    log.info("Promoted captured skill '%s': code_trusted=true (audit ALLOW)", safe_name)
    return PromoteResult(
        name=safe_name, promoted=True, verdict=report.verdict, reason=report.reason
    )


__all__ = [
    "CaptureResult",
    "PromoteResult",
    "SkillCaptureError",
    "capture_entry",
    "capture_skill",
    "code_is_deferred",
    "promote_skill",
]
