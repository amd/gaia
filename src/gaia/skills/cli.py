# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
CLI for ``gaia skill
{list|info|create|import|export|migrate|audit|search|install|remove|lock|publish|keygen|trust}``.

Three groups of verbs, all real:

* **Local authoring** — ``list`` / ``info`` / ``create`` / ``import`` / ``export``
  (#888), plus ``migrate`` for converting OpenClaw / Hermes skills. No network,
  no registry.
* **Pre-publish gate** — ``audit`` (#2468) runs the same security engine the hub
  runs at publish time, so an author self-checks instead of discovering a
  rejection.
* **Marketplace** (#2467) — ``search`` / ``install`` / ``remove`` / ``publish``,
  plus the ``keygen`` / ``trust`` key management the tier ladder rests on. These
  talk to the Agent Hub's skills lane. ``lock`` reads ``skill-lock.json`` back
  and reports (or re-records) drift between it and what is installed.

Exit codes are shared by all: ``0`` ok, ``2`` usage, ``3`` not found, ``4``
invalid (a malformed skill, a refused install, a rejected publish). ``audit``
additionally uses ``5`` for REVIEW and ``6`` for BLOCK.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from gaia.logger import get_logger
from gaia.skills.errors import SkillError, SkillNotFoundError, SkillValidationError
from gaia.skills.format import (
    SKILL_FILENAME,
    SKILL_TOOLS_FILENAME,
    GaiaMetadata,
    Skill,
    SkillTool,
    parse_skill_file,
    reset_security_tier,
)
from gaia.skills.lock import forget_skill
from gaia.skills.manager import SkillManager
from gaia.skills.migrate import (
    VENDORS,
    MigrationOutcome,
    find_source_skills,
    format_report,
    install_migrated,
    migrate_skill_dir,
)
from gaia.skills.signing import ROLE_AMD, ROLE_PUBLISHER
from gaia.skills.tiers import LOWEST_TIER

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Runtime imports of drift stay inside the two verbs that need it, to keep
    # its cost off every other `gaia skill` invocation.
    from gaia.skills.drift import RelockResult

log = get_logger(__name__)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_INVALID = 4
#: ``gaia skill audit`` verdicts. Distinct codes so CI can hold a skill for
#: review without treating it as a rejection (issue #2468).
EXIT_REVIEW = 5
EXIT_BLOCK = 6

_DEFAULT_DESCRIPTION = (
    "Describe what this skill does and when the model should use it. "
    "This text is the trigger signal."
)


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``gaia skill`` and its subcommands."""
    p = subparsers.add_parser(
        "skill",
        help="Author and manage agent skills (SKILL.md capabilities)",
        description=(
            "Discover, inspect, scaffold, import, and export SKILL.md skills, and "
            "search / install / publish them through the Agent Hub's skills lane. "
            "Skills are discovered from agent-bundled skills/, ~/.gaia/skills/, "
            "and (read-only) .claude/skills/."
        ),
    )
    sub = p.add_subparsers(
        dest="skill_action", metavar="<subcommand>", help="Subcommand"
    )

    p_list = sub.add_parser("list", help="List every discovered skill and its root")
    p_list.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit JSON instead of a table",
    )
    p_list.add_argument(
        "--root",
        default=None,
        help="Only show skills from this discovery root "
        "(agent-bundled | user | claude-import)",
    )

    p_info = sub.add_parser("info", help="Show one skill's manifest in detail")
    p_info.add_argument("name", help="Skill name (== its directory name)")
    p_info.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit JSON instead of text"
    )
    p_info.add_argument(
        "--body", action="store_true", help="Also print the Markdown instructions"
    )

    p_create = sub.add_parser("create", help="Scaffold a new skill directory")
    p_create.add_argument("name", help="Skill name (lowercase-with-hyphens)")
    p_create.add_argument(
        "--dir",
        dest="directory",
        default=None,
        help="Parent directory for the new skill (default: ~/.gaia/skills)",
    )
    p_create.add_argument(
        "--description", default=None, help="Description / trigger signal"
    )
    p_create.add_argument(
        "--with-tools",
        action="store_true",
        help=f"Also scaffold {SKILL_TOOLS_FILENAME} with an example @tool function",
    )
    p_create.add_argument(
        "--force", action="store_true", help="Overwrite an existing skill directory"
    )

    p_import = sub.add_parser(
        "import",
        help="Copy a skill folder, .zip, or URL into ~/.gaia/skills/ (stamped experimental)",
    )
    p_import.add_argument(
        "source", help="Path to a skill directory or .zip, or an https URL"
    )
    p_import.add_argument(
        "--name", default=None, help="Install under this name instead of the source's"
    )
    p_import.add_argument(
        "--force", action="store_true", help="Overwrite an existing installed skill"
    )

    p_export = sub.add_parser("export", help="Export a skill to a .zip bundle")
    p_export.add_argument("name", help="Skill name to export")
    p_export.add_argument(
        "--output", default=None, help="Destination .zip (default: ./<name>.zip)"
    )

    _add_audit_parser(sub)
    p_migrate = sub.add_parser(
        "migrate",
        help="Convert an OpenClaw or Hermes skill to GAIA format (stamped experimental)",
        description=(
            "Convert a foreign skill to GAIA's SKILL.md. Point it at one skill "
            "directory or at a directory of them (a ClawHub checkout). Vendor fields "
            "GAIA does not model are preserved under metadata.<vendor> and reported. "
            "Every migrated skill lands at the experimental security tier."
        ),
    )
    p_migrate.add_argument(
        "source", help="Skill directory, its SKILL.md, or a directory of skills"
    )
    p_migrate.add_argument(
        "--from",
        dest="vendor",
        default="auto",
        choices=[*VENDORS, "auto"],
        help="Source format (default: auto-detect from metadata.<vendor>)",
    )
    p_migrate.add_argument(
        "--out",
        dest="out",
        default=None,
        help="Write migrated skills here instead of installing into ~/.gaia/skills",
    )
    p_migrate.add_argument(
        "--name",
        default=None,
        help="Migrate under this name (single-skill sources only)",
    )
    p_migrate.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing skill of the same name",
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be migrated without writing anything",
    )
    p_migrate.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the migration report as JSON",
    )

    _add_marketplace_subparsers(sub)


def _add_audit_parser(sub: argparse._SubParsersAction) -> None:
    """Register ``gaia skill audit`` (issue #2468).

    Kept in its own function so the marketplace verbs landing alongside it in
    this file stay easy to merge.
    """
    from gaia.skills.audit import SEVERITY_ORDER
    from gaia.skills.format import SECURITY_TIERS

    p_audit = sub.add_parser(
        "audit",
        help="Run the pre-publish security audit on a skill directory",
        description=(
            "Scan a skill's code and its instruction body, then print an "
            "ALLOW / REVIEW / BLOCK verdict with file:line findings. This is "
            "the same engine the hub runs at publish time, so a clean local "
            "audit is what a successful publish requires. Exit codes: "
            f"{EXIT_OK} allow, {EXIT_REVIEW} review, {EXIT_BLOCK} block, "
            f"{EXIT_INVALID} the skill could not be parsed."
        ),
    )
    p_audit.add_argument("path", help="Skill directory (the one containing SKILL.md)")
    p_audit.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the machine-readable report on stdout (the payload the hub "
        "publish path consumes as its 'audit' part)",
    )
    p_audit.add_argument(
        "--output",
        default=None,
        help="Write the JSON report to this file as well",
    )
    p_audit.add_argument(
        "--sarif",
        default=None,
        help="Write SARIF 2.1.0 to this file, for upload to GitHub code scanning",
    )
    p_audit.add_argument(
        "--path-prefix",
        default=None,
        dest="path_prefix",
        help="Prefix SARIF result paths with this repository-relative directory, "
        "so code scanning anchors findings to real files when the skill is "
        "nested in a checkout (default: the audited path itself)",
    )
    p_audit.add_argument(
        "--tier",
        default=None,
        choices=SECURITY_TIERS,
        help="Audit against this tier instead of the one the skill declares — "
        "check a claim before making it",
    )
    p_audit.add_argument(
        "--fail-on",
        default=None,
        dest="fail_on",
        choices=[s for s in SEVERITY_ORDER if s != "info"],
        help="Exit non-zero when any finding reaches this severity, even if the "
        "tier's own gate would allow it",
    )
    p_audit.add_argument(
        "--show-snippets",
        action="store_true",
        dest="show_snippets",
        help="Include the offending source text. Withheld by default so "
        "exploitable detail stays out of shared logs and artifacts.",
    )


def _add_marketplace_subparsers(sub: argparse._SubParsersAction) -> None:
    """Register the marketplace verbs: search / install / remove / publish / keys.

    Kept in its own function so the marketplace lane (#2467) stays a localized,
    additive block rather than edits threaded through every parser above.
    """
    p_search = sub.add_parser(
        "search", help="Search the Agent Hub's skills lane for a published skill"
    )
    p_search.add_argument(
        "query", nargs="?", default="", help="Substring to match (omit to list all)"
    )
    p_search.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit JSON instead of a table",
    )
    p_search.add_argument("--hub-url", default=None, help="Hub origin override")

    p_install = sub.add_parser(
        "install", help="Install a published skill into ~/.gaia/skills/"
    )
    p_install.add_argument(
        "reference", help="Skill to install: <name> or <name>@<version-range>"
    )
    p_install.add_argument(
        "--allow-experimental",
        action="store_true",
        help="Required to install a skill whose signature attests only 'experimental'",
    )
    p_install.add_argument(
        "--force", action="store_true", help="Replace an already-installed copy"
    )
    p_install.add_argument(
        "--yes",
        action="store_true",
        dest="assume_yes",
        help="Grant dangerous permissions without prompting (for CI)",
    )
    p_install.add_argument("--hub-url", default=None, help="Hub origin override")
    p_install.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit JSON instead of text"
    )

    p_remove = sub.add_parser("remove", help="Remove an installed skill")
    p_remove.add_argument("name", help="Skill name to remove")

    p_lock = sub.add_parser(
        "lock",
        help="Check installed skills against skill-lock.json, or re-record it",
        description=(
            "Compare every skill in ~/.gaia/skills against skill-lock.json: its "
            "version, the security tier install enforced, and a digest of its "
            "files. Reports skills the lock has lost track of and skills it "
            "tracks that are gone. Exits "
            f"{EXIT_INVALID} when anything differs, {EXIT_OK} when the lock and "
            "the disk agree. Untracked skills are expected right after "
            "'gaia skill create' / 'import' / 'migrate' — --relock records them."
        ),
    )
    lock_mode = p_lock.add_mutually_exclusive_group()
    lock_mode.add_argument(
        "--check",
        action="store_true",
        help="Report drift without changing anything (the default)",
    )
    lock_mode.add_argument(
        "--relock",
        action="store_true",
        help="Re-record the current state as intended. Refuses to re-record a "
        "signature-backed skill whose files changed — that would launder an "
        "attestation the new bytes never had.",
    )
    p_lock.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit JSON instead of text"
    )

    p_publish = sub.add_parser(
        "publish", help="Validate, audit, sign, and publish a skill to the Agent Hub"
    )
    p_publish.add_argument("directory", help="Skill folder to publish")
    p_publish.add_argument(
        "--hub-url", default=None, help="Hub origin override (default: GAIA_HUB_URL)"
    )
    p_publish.add_argument(
        "--key-name",
        default="publisher",
        help="Signing key to use (default: publisher)",
    )
    p_publish.add_argument(
        "--publisher", default="", help="Publisher identity recorded in the signature"
    )
    p_publish.add_argument(
        "--unsigned",
        action="store_true",
        help=f"Publish without a signature (only valid for '{LOWEST_TIER}')",
    )
    p_publish.add_argument(
        "--audit-report",
        default=None,
        help="Use this security-audit report JSON instead of running the audit engine",
    )
    p_publish.add_argument(
        "--dry-run",
        action="store_true",
        help="Run every gate and build the upload, but do not send it",
    )

    p_keygen = sub.add_parser(
        "keygen", help="Generate an Ed25519 publisher key for signing skills"
    )
    p_keygen.add_argument(
        "--key-name", default="publisher", help="Key name (default: publisher)"
    )
    p_keygen.add_argument(
        "--force", action="store_true", help="Overwrite an existing key of that name"
    )

    p_trust = sub.add_parser(
        "trust", help="Manage the public keys whose signatures this machine trusts"
    )
    trust_sub = p_trust.add_subparsers(dest="trust_action", metavar="<action>")
    trust_sub.add_parser("list", help="List trusted signing keys")
    p_trust_add = trust_sub.add_parser("add", help="Trust a public key")
    p_trust_add.add_argument(
        "public_key", help="Base64 Ed25519 public key, or a path to a .pub file"
    )
    p_trust_add.add_argument(
        "--publisher", default="", help="Publisher name shown for this key"
    )
    p_trust_add.add_argument(
        "--role",
        default=ROLE_PUBLISHER,
        choices=[ROLE_PUBLISHER, ROLE_AMD],
        help=f"'{ROLE_PUBLISHER}' attests 'community'; '{ROLE_AMD}' attests 'verified'",
    )
    p_trust_remove = trust_sub.add_parser("remove", help="Stop trusting a key")
    p_trust_remove.add_argument("key_id", help="Key id to remove (see 'trust list')")


def handle(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``gaia skill ...`` command. Returns an exit code."""
    action = getattr(args, "skill_action", None)
    if action is None:
        sys.stderr.write("gaia skill: missing subcommand. Try 'gaia skill --help'.\n")
        return EXIT_USAGE

    handlers = {
        "list": _handle_list,
        "info": _handle_info,
        "create": _handle_create,
        "import": _handle_import,
        "export": _handle_export,
        "migrate": _handle_migrate,
        # Pre-publish security gate (#2468)
        "audit": _handle_audit,
        # Marketplace lane (#2467)
        "search": _handle_search,
        "install": _handle_install,
        "remove": _handle_remove,
        "lock": _handle_lock,
        "publish": _handle_publish,
        "keygen": _handle_keygen,
        "trust": _handle_trust,
    }
    handler = handlers.get(action)
    if handler is None:
        sys.stderr.write(f"gaia skill: unknown subcommand {action!r}\n")
        return EXIT_USAGE

    if getattr(args, "as_json", False):
        # stdout carries machine-readable JSON; keep log lines off it.
        from gaia.logger import route_console_logging_to_stderr

        route_console_logging_to_stderr()

    try:
        return handler(args)
    except SkillNotFoundError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return EXIT_NOT_FOUND
    except SkillValidationError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return EXIT_INVALID
    except SkillError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return EXIT_INVALID


def _manager() -> SkillManager:
    return SkillManager()


def _handle_list(args: argparse.Namespace) -> int:
    from gaia.skills.drift import DRIFT_UNTRACKED, check_drift

    manager = _manager()
    skills = manager.list_skills()
    if args.root:
        skills = [s for s in skills if s.root == args.root]
    errors = manager.discovery_errors
    drift = check_drift(manager.user_root)
    # An untracked skill is already a row in the table below; re-flagging it here
    # would bury the drifts that are not otherwise visible.
    notable = [d for d in drift.drifts if d.kind != DRIFT_UNTRACKED]

    if getattr(args, "as_json", False):
        payload = {
            "roots": [
                {"label": r.label, "path": str(r.path), "exists": r.path.is_dir()}
                for r in manager.roots
            ],
            "skills": [_skill_summary(s) for s in skills],
            "shadowed": [_skill_summary(s) for s in manager.shadowed()],
            "errors": errors,
            "drift": drift.to_dict(),
        }
        print(json.dumps(payload, indent=2))
        return EXIT_INVALID if errors else EXIT_OK

    if not skills:
        print("No skills found. Searched:")
        for root in manager.roots:
            mark = "" if root.path.is_dir() else "  (missing)"
            print(f"  {root.label:<14} {root.path}{mark}")
        print("\nCreate one with: gaia skill create my-skill")
    else:
        print(f"{'NAME':<28} {'VERSION':<10} {'TIER':<13} {'ROOT':<14} TOOLS")
        for skill in skills:
            tools = ", ".join(skill.tool_names) or "-"
            print(
                f"{skill.name:<28} {skill.version or '-':<10} "
                f"{skill.security_tier:<13} {skill.root or '-':<14} {tools}"
            )

    for shadow in manager.shadowed():
        print(
            f"  ↳ '{shadow.name}' in {shadow.directory} is shadowed by the "
            f"higher-precedence copy",
            file=sys.stderr,
        )

    if notable:
        blocking = sum(1 for d in notable if d.fatal)
        names = ", ".join(sorted({d.skill for d in notable}))
        print(
            f"\n⚠ {len(notable)} skill file(s) no longer match "
            f"{drift.lock_file.name} ({names}"
            f"{f'; {blocking} will refuse to load' if blocking else ''}). "
            "Details: gaia skill lock --check",
            file=sys.stderr,
        )

    if errors:
        print(f"\n{len(errors)} skill folder(s) failed to load:", file=sys.stderr)
        for path, message in errors.items():
            print(f"  {path}: {message}", file=sys.stderr)
        return EXIT_INVALID
    return EXIT_OK


def _handle_info(args: argparse.Namespace) -> int:
    manager = _manager()
    skill = manager.load(args.name)

    if getattr(args, "as_json", False):
        payload = _skill_summary(skill)
        payload["frontmatter"] = skill.to_frontmatter()
        if getattr(args, "body", False):
            payload["body"] = skill.body
        print(json.dumps(payload, indent=2, default=str))
        return EXIT_OK

    print(f"{skill.name}  {skill.version or '(unversioned)'}")
    print(f"  {skill.description}")
    print(f"  path         : {skill.directory}")
    print(f"  root         : {skill.root}{' (read-only)' if skill.read_only else ''}")
    print(f"  license      : {skill.license or '-'}")
    print(f"  security tier: {skill.security_tier}")
    print(f"  permissions  : {', '.join(skill.gaia.permissions) or 'none'}")
    if skill.gaia.tools:
        print("  provides     :")
        for tool in skill.gaia.tools:
            params = ", ".join(
                f"{n}{'' if spec.get('required') else '?'}"
                for n, spec in tool.parameters.items()
            )
            print(f"    {skill.name}/{tool.name}({params})  {tool.description}")
    else:
        print("  provides     : (instruction-only)")
    if skill.gaia.tools_required:
        print(f"  consumes     : {', '.join(skill.gaia.tools_required)}")

    shadowed = manager.shadowed(skill.name)
    for shadow in shadowed:
        print(f"  shadows      : {shadow.directory} ({shadow.root})")

    if getattr(args, "body", False) and skill.body:
        print("\n--- instructions ---")
        print(skill.body)
    return EXIT_OK


def _handle_create(args: argparse.Namespace) -> int:
    parent = Path(args.directory) if args.directory else _manager().user_root
    target = parent / args.name

    if target.exists() and not args.force:
        sys.stderr.write(
            f"❌ {target} already exists. Pass --force to overwrite it, or pick "
            "another name.\n"
        )
        return EXIT_INVALID

    gaia_meta = GaiaMetadata()
    if args.with_tools:
        gaia_meta.tools = [
            SkillTool(
                name="example_tool",
                description="Replace this with what your tool does.",
                parameters={"text": {"type": "string", "required": True}},
                returns={"type": "object"},
            )
        ]

    skill = Skill(
        name=args.name,
        description=args.description or _DEFAULT_DESCRIPTION,
        version="0.1.0",
        license="MIT",
        gaia=gaia_meta,
        body=_scaffold_body(args.name, with_tools=args.with_tools),
    )
    # Validate the scaffold through the real parser before writing it, so
    # 'gaia skill create' can never emit a SKILL.md that 'gaia skill info' rejects.
    from gaia.skills.format import parse_skill

    parse_skill(skill.to_markdown(), source=f"<scaffold {args.name}>")

    if target.exists() and args.force:
        shutil.rmtree(target)
        forget_skill(parent, args.name)
    target.mkdir(parents=True)
    skill.write(target / SKILL_FILENAME)
    if args.with_tools:
        (target / SKILL_TOOLS_FILENAME).write_text(_SCAFFOLD_TOOLS, encoding="utf-8")

    print(f"✅ Created skill '{args.name}' at {target}")
    print(f"   Edit {target / SKILL_FILENAME}, then: gaia skill info {args.name}")
    return EXIT_OK


def _handle_import(args: argparse.Namespace) -> int:
    manager = _manager()
    destination_root = manager.user_root

    with tempfile.TemporaryDirectory(prefix="gaia-skill-import-") as tmp:
        source_dir = _materialize_source(args.source, Path(tmp))
        skill = parse_skill_file(source_dir, check_directory_name=False)
        name = args.name or skill.name
        target = destination_root / name

        if target.exists():
            if not args.force:
                sys.stderr.write(
                    f"❌ Skill '{name}' is already installed at {target}. Pass "
                    "--force to replace it.\n"
                )
                return EXIT_INVALID
            shutil.rmtree(target)

        shutil.copytree(source_dir, target)
        # Imported skills re-earn trust: stamp experimental regardless of claim.
        imported = parse_skill_file(target, check_directory_name=False)
        imported.name = name
        previous_tier = reset_security_tier(imported)
        imported.write(target / SKILL_FILENAME)

    # These bytes are not the hub's any more, so its provenance must not describe
    # them — otherwise the replaced skill reads as tampered-with hub content.
    forget_skill(destination_root, name)

    print(f"✅ Imported skill '{name}' into {target}")
    if previous_tier != "experimental":
        print(
            f"   Security tier reset: {previous_tier} → experimental "
            "(imported skills re-earn trust)."
        )
    print(f"   Inspect it with: gaia skill info {name}")
    return EXIT_OK


def _handle_export(args: argparse.Namespace) -> int:
    manager = _manager()
    skill = manager.load(args.name)
    source = skill.directory
    if source is None:  # pragma: no cover - discovery always sets a path
        raise SkillNotFoundError(f"Skill '{args.name}' has no directory on disk.")

    output = Path(args.output) if args.output else Path.cwd() / f"{skill.name}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, arcname=f"{skill.name}/{path.relative_to(source)}")

    print(f"✅ Exported skill '{skill.name}' to {output}")
    print(f"   Import it elsewhere with: gaia skill import {output}")
    return EXIT_OK


def _handle_audit(args: argparse.Namespace) -> int:
    """Run the pre-publish security audit (issue #2468)."""
    from gaia.skills.audit import (
        SEVERITY_ORDER,
        audit_skill,
        render_json,
        render_sarif,
        render_text,
    )

    # The tier override goes into the audit, not onto the report afterwards, so
    # the verdict and its tier-claim findings always agree with each other.
    report = audit_skill(args.path, tier=getattr(args, "tier", None))

    show_snippets = getattr(args, "show_snippets", False)

    if getattr(args, "as_json", False):
        print(render_json(report, include_snippets=show_snippets))
    else:
        print(render_text(report, include_snippets=show_snippets))

    if getattr(args, "output", None):
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            render_json(report, include_snippets=show_snippets), encoding="utf-8"
        )

    if getattr(args, "sarif", None):
        destination = Path(args.sarif)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Default the prefix to the audited path so SARIF uploaded from a repo
        # checkout anchors to the real file, not a bare 'tools.py' at the root.
        prefix = getattr(args, "path_prefix", None)
        if prefix is None:
            prefix = _repo_relative_prefix(args.path)
        destination.write_text(
            render_sarif(report, include_snippets=show_snippets, path_prefix=prefix),
            encoding="utf-8",
        )

    fail_on = getattr(args, "fail_on", None)
    if fail_on and report.worst is not None:
        if SEVERITY_ORDER.index(report.worst) >= SEVERITY_ORDER.index(fail_on):
            return EXIT_BLOCK

    return {"ALLOW": EXIT_OK, "REVIEW": EXIT_REVIEW, "BLOCK": EXIT_BLOCK}[
        report.verdict
    ]


def _repo_relative_prefix(audited_path: str) -> str:
    """The audited directory relative to the repo root, for SARIF paths.

    Falls back to an empty prefix (paths relative to the skill) when the skill is
    outside the working directory — an absolute or ``../`` SARIF path would be
    rejected by code scanning, so no prefix is better than a wrong one.
    """
    try:
        relative = Path(audited_path).resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return ""
    return relative.as_posix()


def _handle_migrate(args: argparse.Namespace) -> int:
    sources = find_source_skills(args.source)
    if len(sources) > 1 and args.name:
        sys.stderr.write(
            f"❌ --name applies to a single skill, but {args.source} holds "
            f"{len(sources)} skills. Migrate them one at a time to rename, or drop "
            "--name to keep each skill's own name.\n"
        )
        return EXIT_USAGE

    destination = Path(args.out).expanduser() if args.out else _manager().user_root
    outcomes: list[MigrationOutcome] = []
    for source in sources:
        try:
            outcomes.append(
                migrate_skill_dir(source, vendor=args.vendor, name=args.name)
            )
        except SkillError as exc:
            # One undetectable skill must not hide the report for the rest of a
            # collection — the same treatment no-frontmatter sources already get.
            outcomes.append(
                MigrationOutcome(
                    source=Path(source), vendor="unknown", blockers=[str(exc)]
                )
            )

    installed: dict[str, str] = {}
    install_errors: dict[str, str] = {}
    if not args.dry_run:
        for outcome in outcomes:
            if not outcome.migrated:
                continue
            try:
                target = install_migrated(outcome, destination, force=args.force)
            except SkillError as exc:
                # One collision must not hide the report for the rest of a batch.
                # Tracked apart from `blockers`: the skill migrated fine, it just
                # could not be written, which is a different thing to tell a user.
                install_errors[outcome.name] = f"{exc}"
                continue
            installed[outcome.name] = str(target)

    migrated = [o for o in outcomes if o.migrated]
    refused = [o for o in outcomes if not o.migrated]

    if getattr(args, "as_json", False):
        payload = {
            "source": str(args.source),
            "destination": None if args.dry_run else str(destination),
            "dry_run": bool(args.dry_run),
            "total": len(outcomes),
            "migrated": len(migrated),
            "unmigratable": len(refused),
            "install_errors": install_errors,
            "skills": [
                {
                    **o.to_dict(),
                    "installed_at": installed.get(o.name),
                    "install_error": install_errors.get(o.name),
                }
                for o in outcomes
            ],
        }
        print(json.dumps(payload, indent=2))
        return EXIT_INVALID if (refused or install_errors) else EXIT_OK

    report = format_report(outcomes)
    if report:
        print(report, end="")

    verb = "Would migrate" if args.dry_run else "Migrated"
    print(f"{verb} {len(migrated)}/{len(outcomes)} skill(s) to GAIA format.")
    if installed and not args.dry_run:
        print(f"   Installed {len(installed)} into {destination}")
        print(
            "   Every migrated skill is at the experimental tier — review it, then: "
            f"gaia skill info {next(iter(installed))}"
        )
    if install_errors:
        print(
            f"\n{len(install_errors)} skill(s) migrated but could not be written:",
            file=sys.stderr,
        )
        for name, message in install_errors.items():
            print(f"  {name}: {message}", file=sys.stderr)
        if not refused:
            return EXIT_INVALID
    if refused:
        print(
            f"\n{len(refused)} skill(s) could not be migrated (see ✗ above). v1 accepts "
            "instruction-only and connector-backed skills; a skill needing local "
            "shell, filesystem, database, desktop, or env access is refused rather "
            "than silently stripped of the permission.",
            file=sys.stderr,
        )
        return EXIT_INVALID
    return EXIT_OK


# ----------------------------------------------------------------------
# Marketplace verbs (#2467)
# ----------------------------------------------------------------------


def _handle_search(args: argparse.Namespace) -> int:
    from gaia.skills.hub import search_skills

    found = search_skills(args.query, base_url=args.hub_url)
    results = found.entries

    if getattr(args, "as_json", False):
        print(
            json.dumps(
                {
                    "query": args.query,
                    "offline": found.offline,
                    "generated_at": found.generated_at,
                    "age_seconds": found.age_seconds,
                    "age_text": found.age_text,
                    "stale": found.stale,
                    "skills": results,
                },
                indent=2,
            )
        )
        return EXIT_OK

    # Say so before the results, not after: a stale list read as current is how a
    # user ends up installing something that was unpublished.
    if found.stale:
        from gaia.hub.catalog import CACHE_STALE_AFTER_SECONDS

        days = round(CACHE_STALE_AFTER_SECONDS / 86400)
        print(
            f"⚠ This catalog is {found.age_text} — over {days} days old. The hub "
            "is unreachable, so skills published since are missing and skills "
            "unpublished since are still listed, including any whose security "
            "tier changed. Reconnect and re-run to refresh, or check GAIA_HUB_URL.",
            file=sys.stderr,
        )
    elif found.offline:
        print(
            f"⚠ The hub was unreachable — showing the offline catalog cache from "
            f"{found.age_text}. It may be stale.",
            file=sys.stderr,
        )

    if not results:
        target = f" matching {args.query!r}" if args.query else ""
        print(f"No published skills{target}.")
        print("  The hub's skills lane may be empty, or try a broader query.")
        return EXIT_OK

    print(f"{'NAME':<28} {'VERSION':<10} {'TIER':<13} {'TOOLS':<6} DESCRIPTION")
    for entry in results:
        metadata = entry.get("skill_metadata") or {}
        tools = len(metadata.get("tools") or [])
        description = (entry.get("description") or "").replace("\n", " ")
        if len(description) > 48:
            description = description[:45] + "..."
        print(
            f"{entry.get('id', '?'):<28} {entry.get('latest_version', '-'):<10} "
            f"{entry.get('security_tier', '-'):<13} {tools:<6} {description}"
        )
    origin = (
        f"offline cache, last refreshed {found.age_text}"
        if found.offline
        else f"hub, fetched {found.age_text}"
    )
    print(f"\nCatalog: {origin}")
    print("Install one with: gaia skill install <name>")
    return EXIT_OK


def _handle_install(args: argparse.Namespace) -> int:
    from gaia.skills.install import install_skill

    result = install_skill(
        args.reference,
        manager=_manager(),
        base_url=args.hub_url,
        allow_experimental=args.allow_experimental,
        force=args.force,
        assume_yes=args.assume_yes,
    )

    if getattr(args, "as_json", False):
        print(
            json.dumps(
                {
                    "name": result.name,
                    "version": result.version,
                    "path": str(result.path),
                    "requested": result.requested,
                    "claimed_tier": result.claimed_tier,
                    "attested_tier": result.attested_tier,
                    "installed_tier": result.installed_tier,
                    "signature": (
                        result.signature.describe() if result.signature else "unsigned"
                    ),
                    "permissions": result.permissions,
                },
                indent=2,
            )
        )
        return EXIT_OK

    print(f"✅ Installed skill '{result.name}' {result.version} → {result.path}")
    print(f"   requested   : {result.requested}")
    print(
        f"   provenance  : {result.signature.describe() if result.signature else 'unsigned'}"
    )
    print(f"   tier        : {result.installed_tier}")
    if result.downgraded:
        print(
            f"   ⚠ tier reduced: claimed '{result.claimed_tier}', but its signature "
            f"attests only '{result.attested_tier}'."
        )
    if result.permissions:
        print(f"   permissions : {', '.join(result.permissions)}")
    if result.replaced_version:
        print(f"   replaced    : {result.replaced_version}")
    print(f"   Inspect it with: gaia skill info {result.name}")
    return EXIT_OK


def _handle_remove(args: argparse.Namespace) -> int:
    from gaia.skills.install import remove_skill

    result = remove_skill(args.name, manager=_manager())
    version = f" {result.version}" if result.version else ""
    print(f"✅ Removed skill '{result.name}'{version} from {result.path}")
    if not result.was_locked:
        print("   (it was not hub-installed, so no lock entry was tracked)")
    return EXIT_OK


def _handle_lock(args: argparse.Namespace) -> int:
    """Check the user root against ``skill-lock.json``, or re-record it."""
    from gaia.skills.drift import check_drift, relock

    root = _manager().user_root

    if getattr(args, "relock", False):
        return _render_relock(relock(root), root, as_json=args.as_json)

    report = check_drift(root)

    if getattr(args, "as_json", False):
        print(json.dumps(report.to_dict(), indent=2))
        return EXIT_OK if report.clean else EXIT_INVALID

    if report.clean:
        print(f"✅ {report.lock_file} matches what is installed in {root}")
        return EXIT_OK

    fatal, warnings = len(report.fatal), len(report.warnings)
    print(
        f"⚠ {len(report.drifts)} difference(s) between {root} and "
        f"{report.lock_file.name} ({fatal} blocking, {warnings} advisory):\n",
        file=sys.stderr,
    )
    print(report.render(), file=sys.stderr)
    if fatal:
        print(
            "\nThe blocking ones are signature-backed skills whose files changed, "
            "so they will refuse to load until reinstalled — the tier they carry "
            "was earned by different bytes.",
            file=sys.stderr,
        )
    print(
        "\nRe-record the state you intended with: gaia skill lock --relock",
        file=sys.stderr,
    )
    return EXIT_INVALID


def _render_relock(result: "RelockResult", root: Path, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result.to_dict(), indent=2))
        return EXIT_INVALID if result.refused else EXIT_OK

    if not result.changed and not result.refused:
        print(f"✅ Nothing to re-record — {result.lock_file} already matches {root}")
        return EXIT_OK

    print(f"✅ Re-recorded {result.lock_file}")
    if result.updated:
        print(f"   updated : {', '.join(result.updated)}")
    if result.added:
        print(f"   now tracked (source: local) : {', '.join(result.added)}")
    if result.removed:
        print(f"   dropped (no longer installed) : {', '.join(result.removed)}")

    if not result.refused:
        return EXIT_OK

    names = ", ".join(sorted({d.skill for d in result.refused}))
    print(
        f"\n❌ Refused to re-record {names}: installed at a signature-backed tier, "
        "but the files changed. Re-recording would leave the lock asserting a "
        "signature over bytes it never covered.",
        file=sys.stderr,
    )
    for drift in result.refused:
        print(f"  ✗ [{drift.kind}] {drift.skill}: {drift.summary}", file=sys.stderr)
        print(f"      → {drift.remediation}", file=sys.stderr)
    return EXIT_INVALID


def _handle_publish(args: argparse.Namespace) -> int:
    from gaia.hub.publisher import get_hub_token
    from gaia.skills.publish import publish_skill

    token = get_hub_token() or ""
    result = publish_skill(
        Path(args.directory),
        token=token,
        hub_url=args.hub_url,
        key_name=args.key_name,
        publisher=args.publisher,
        unsigned=args.unsigned,
        audit_report=Path(args.audit_report) if args.audit_report else None,
        dry_run=args.dry_run,
    )

    verb = "Would publish" if args.dry_run else "Published"
    print(f"✅ {verb} skill '{result.name}' {result.version}")
    print(f"   artifact    : {result.artifact_filename}")
    print(f"   tier        : {result.security_tier}")
    provenance = f"signed with key {result.key_id}" if result.signed else "unsigned"
    print(f"   signature   : {provenance}")
    if result.audit is not None:
        print(
            f"   audit       : {result.audit.verdict} "
            f"({result.audit.engine}, {len(result.audit.findings)} finding(s))"
        )
    published = (result.response or {}).get("published") or {}
    if published.get("latest_version"):
        print(f"   latest      : {published['latest_version']}")
    if args.dry_run:
        print("   Nothing was uploaded (--dry-run).")
    else:
        print(f"   Find it with: gaia skill search {result.name}")
    return EXIT_OK


def _handle_keygen(args: argparse.Namespace) -> int:
    from gaia.skills.signing import generate_key, keys_dir

    root = _manager().user_root
    key = generate_key(root, name=args.key_name, force=args.force)
    directory = keys_dir(root)
    print(f"✅ Generated signing key '{args.key_name}' ({key.key_id})")
    print(f"   private : {directory / f'{args.key_name}.key'}  (keep this secret)")
    print(f"   public  : {directory / f'{args.key_name}.pub'}")
    print(
        "\n   Share the PUBLIC key with anyone who should trust your skills; they "
        "run:\n"
        "     gaia skill trust add <public-key> --publisher <you>"
    )
    return EXIT_OK


def _handle_trust(args: argparse.Namespace) -> int:
    from gaia.skills.signing import TrustStore

    action = getattr(args, "trust_action", None)
    if action is None:
        sys.stderr.write(
            "gaia skill trust: missing action. Try 'gaia skill trust --help'.\n"
        )
        return EXIT_USAGE

    root = _manager().user_root
    store = TrustStore.load(root)

    if action == "list":
        if not store.entries:
            print("No trusted signing keys.")
            print(
                f"  Every skill therefore installs at '{LOWEST_TIER}'. Trust a "
                "publisher's public key with: gaia skill trust add <key>"
            )
            return EXIT_OK
        print(f"{'KEY ID':<34} {'ROLE':<11} {'ATTESTS':<13} PUBLISHER")
        for entry in store.entries.values():
            attests = "verified" if entry["role"] == ROLE_AMD else "community"
            print(
                f"{entry['key_id']:<34} {entry['role']:<11} {attests:<13} "
                f"{entry.get('publisher') or '-'}"
            )
        return EXIT_OK

    if action == "add":
        material = args.public_key
        candidate = Path(material).expanduser()
        if candidate.is_file():
            material = candidate.read_text(encoding="utf-8").strip()
        identifier = store.add(
            public_key_b64=material, publisher=args.publisher, role=args.role
        )
        store.save()
        attests = "verified" if args.role == ROLE_AMD else "community"
        print(f"✅ Trusting key {identifier} (role={args.role}, attests '{attests}')")
        print(f"   Trust store: {store.path}")
        return EXIT_OK

    if not store.remove(args.key_id):
        sys.stderr.write(
            f"❌ Key {args.key_id} is not in the trust store at {store.path}. List "
            "trusted keys with 'gaia skill trust list'.\n"
        )
        return EXIT_NOT_FOUND
    store.save()
    print(f"✅ Removed key {args.key_id} from the trust store")
    print(
        "   Skills signed by it now attest only "
        f"'{LOWEST_TIER}'; already-installed copies keep the tier they were "
        "installed at (see skill-lock.json)."
    )
    return EXIT_OK


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _skill_summary(skill: Skill) -> dict:
    return {
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "license": skill.license,
        "security_tier": skill.security_tier,
        "root": skill.root,
        "read_only": skill.read_only,
        "path": str(skill.directory) if skill.directory else None,
        "instruction_only": skill.is_instruction_only,
        "tools": [skill.namespaced_tool_name(n) for n in skill.tool_names],
        "tools_required": list(skill.gaia.tools_required),
        "permissions": list(skill.gaia.permissions),
    }


def _materialize_source(source: str, workdir: Path) -> Path:
    """Return a directory containing the source skill, downloading/unzipping."""
    if source.startswith(("http://", "https://")):
        archive = _download(source, workdir / "download.zip")
        return _unpack(archive, workdir / "unpacked")

    path = Path(source).expanduser()
    if path.is_dir():
        return path
    if path.suffix == ".zip" and path.is_file():
        return _unpack(path, workdir / "unpacked")

    raise SkillValidationError(
        f"Cannot import {source!r}: it is neither a directory, a .zip bundle, nor an "
        "https URL. Point 'gaia skill import' at a skill folder containing "
        f"{SKILL_FILENAME}, at a .zip produced by 'gaia skill export', or at a URL "
        "serving one."
    )


def _download(url: str, destination: Path) -> Path:
    import requests

    log.info("Downloading skill bundle from %s", url)
    try:
        response = requests.get(url, timeout=60, stream=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SkillError(
            f"Could not download {url}: {exc}. Check the URL and your network, then "
            "retry — or download the .zip yourself and pass the local path."
        ) from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=65536):
            handle.write(chunk)
    return destination


def _unpack(archive: Path, destination: Path) -> Path:
    """Extract a skill .zip, rejecting path traversal, and return its root."""
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                resolved = (destination / member).resolve()
                if (
                    destination.resolve() not in resolved.parents
                    and resolved != destination.resolve()
                ):
                    raise SkillValidationError(
                        f"Refusing to extract {archive}: entry {member!r} escapes the "
                        "destination directory. The bundle is malformed or hostile."
                    )
            bundle.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise SkillValidationError(
            f"{archive} is not a valid .zip bundle: {exc}. Export it with "
            "'gaia skill export <name>' and retry."
        ) from exc

    return _find_skill_root(destination, archive)


def _find_skill_root(directory: Path, origin: Path) -> Path:
    """Locate the directory holding SKILL.md inside an extracted bundle."""
    if (directory / SKILL_FILENAME).is_file():
        return directory
    candidates = sorted(directory.glob(f"*/{SKILL_FILENAME}"))
    if len(candidates) == 1:
        return candidates[0].parent
    if not candidates:
        raise SkillValidationError(
            f"No {SKILL_FILENAME} found in {origin}. A skill bundle must contain "
            f"{SKILL_FILENAME} at its root or one level down."
        )
    names = ", ".join(c.parent.name for c in candidates)
    raise SkillValidationError(
        f"{origin} contains more than one skill ({names}). Import them one at a time "
        "by extracting the bundle and pointing 'gaia skill import' at a single folder."
    )


def _scaffold_body(name: str, *, with_tools: bool) -> str:
    title = name.replace("-", " ").title()
    if with_tools:
        return (
            f"# {title}\n\n"
            "Explain when the model should reach for this skill and how to use its "
            "tools.\n\n"
            f"1. Call `{name}/example_tool` with the text to process.\n"
            "2. Summarize the result for the user.\n"
        )
    return (
        f"# {title}\n\n"
        "Write the procedure the model should follow. Keep it concrete — numbered "
        "steps beat prose.\n\n"
        "1. First step.\n2. Second step.\n3. What 'done' looks like.\n"
    )


_SCAFFOLD_TOOLS = '''# Tools provided by this skill.
# Every function here must have a matching entry in metadata.gaia.tools —
# a mismatch makes the skill fail to load (by design, no partial loads).

from gaia.agents.base.tools import tool


@tool
def example_tool(text: str) -> dict:
    """Replace this with what your tool does."""
    return {"echo": text}
'''


def resolve_manager(agent_skill_dirs: Optional[list] = None) -> SkillManager:
    """Build a manager for embedders that need the CLI's root configuration."""
    return SkillManager(agent_skill_dirs=agent_skill_dirs or [])
