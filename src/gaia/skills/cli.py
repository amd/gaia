# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
CLI for ``gaia skill {list|info|create|import|export|audit}``.

``install`` / ``search`` / ``publish`` belong to the marketplace phase
(issue #2467) and are deliberately absent — not even as stubs, so a user never
discovers a verb that cannot work.

``audit`` runs the pre-publish security gate (issue #2468) that the hub also runs
at publish time, so an author can self-check before publishing rather than
discovering a rejection.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from gaia.logger import get_logger
from gaia.skills.errors import SkillError, SkillNotFoundError, SkillValidationError
from gaia.skills.format import (
    SKILL_FILENAME,
    SKILL_TOOLS_FILENAME,
    GaiaMetadata,
    Skill,
    SkillTool,
    parse_skill_file,
)
from gaia.skills.manager import SkillManager

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
            "Discover, inspect, scaffold, import, and export SKILL.md skills. "
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
        "audit": _handle_audit,
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
    manager = _manager()
    skills = manager.list_skills()
    if args.root:
        skills = [s for s in skills if s.root == args.root]
    errors = manager.discovery_errors

    if getattr(args, "as_json", False):
        payload = {
            "roots": [
                {"label": r.label, "path": str(r.path), "exists": r.path.is_dir()}
                for r in manager.roots
            ],
            "skills": [_skill_summary(s) for s in skills],
            "shadowed": [_skill_summary(s) for s in manager.shadowed()],
            "errors": errors,
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
        previous_tier = imported.gaia.security_tier
        imported.gaia.security_tier = "experimental"
        imported.write(target / SKILL_FILENAME)

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
