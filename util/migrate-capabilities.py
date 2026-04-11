#!/usr/bin/env python
"""Migrate legacy capability vocabulary to unified model.

Scans all YAML agent configs in config/agents/ and updates the capabilities
section to use the unified vocabulary defined in the Unified Capability Model.

Usage:
    python util/migrate-capabilities.py --dry-run    # Preview changes
    python util/migrate-capabilities.py --apply      # Apply changes
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

# Mapping from old capability strings to unified vocabulary.
# Keys are legacy capability strings found in config/agents/*.yaml.
# Values are the unified capability IDs from the Unified Capability Model.
CAPABILITY_MAP: dict[str, str] = {
    # Analysis capabilities
    "domain-analysis": "domain-analysis",
    "requirements-extraction": "requirements-analysis",
    "dependency-mapping": "dependency-analysis",
    "keyword-extraction": "text-analysis",
    "code-analysis": "code-analysis",
    "gap-analysis": "gap-analysis",
    "performance-analysis": "performance-analysis",
    "quality-analysis": "quality-analysis",

    # Development capabilities
    "code-generation": "code-generation",
    "code-review": "code-review",
    "refactoring": "code-refactoring",
    "debugging": "debugging",
    "testing": "test-automation",
    "documentation-generation": "documentation-generation",

    # Architecture capabilities
    "architecture-design": "architecture-design",
    "system-design": "system-design",
    "api-design": "api-design",
    "database-design": "database-design",
    "workflow-modeling": "workflow-modeling",
    "topology-design": "topology-design",

    # Security capabilities
    "security-audit": "security-audit",
    "vulnerability-detection": "vulnerability-detection",
    "compliance-check": "compliance-audit",
    "threat-modeling": "threat-modeling",

    # DevOps capabilities
    "ci-cd": "ci-cd-automation",
    "deployment": "deployment-management",
    "infrastructure": "infrastructure-management",
    "monitoring": "observability",

    # Project management capabilities
    "project-planning": "project-planning",
    "task-management": "task-management",
    "sprint-planning": "sprint-planning",

    # Review capabilities
    "code-quality-review": "code-quality-review",
    "technical-writing": "technical-writing",
    "accessibility-review": "accessibility-review",
    "peer-review": "peer-review",

    # Data capabilities
    "data-engineering": "data-engineering",
    "data-analysis": "data-analysis",
    "data-pipeline": "data-pipeline",
    "etl": "etl-processing",
}


def migrate_capabilities(
    yaml_path: Path, dry_run: bool = True
) -> tuple[list[str], list[str]]:
    """Migrate capabilities in a single YAML file.

    Returns:
        Tuple of (old_capabilities, new_capabilities) found in the file.
    """
    content = yaml_path.read_text(encoding="utf-8")

    if yaml is None:
        # Fallback: string-based replacement without yaml library
        return _string_migrate(content, yaml_path, dry_run)

    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        return [], []

    agent = data.get("agent", {})
    if not isinstance(agent, dict):
        return [], []

    caps = agent.get("capabilities", [])
    if not isinstance(caps, list):
        return [], []

    old_caps: list[str] = []
    new_caps: list[str] = []
    changed = False

    for cap in caps:
        old_caps.append(cap)
        if cap in CAPABILITY_MAP:
            unified = CAPABILITY_MAP[cap]
            if unified != cap:
                new_caps.append(unified)
                changed = True
            else:
                new_caps.append(cap)
        else:
            # Keep unknown capabilities as-is
            new_caps.append(cap)

    if changed and not dry_run:
        agent["capabilities"] = new_caps
        updated_content = yaml.dump(data, default_flow_style=False, sort_keys=False)
        yaml_path.write_text(updated_content, encoding="utf-8")

    return old_caps, new_caps


def _string_migrate(
    content: str, yaml_path: Path, dry_run: bool = True
) -> tuple[list[str], list[str]]:
    """Fallback string-based migration when PyYAML is not installed."""
    old_caps: list[str] = []
    new_caps: list[str] = []
    changed = False

    lines = content.splitlines()
    in_capabilities = False
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("capabilities:"):
            in_capabilities = True
            new_lines.append(line)
            continue

        if in_capabilities:
            if stripped.startswith("- "):
                cap = stripped[2:].strip().strip('"').strip("'")
                old_caps.append(cap)
                if cap in CAPABILITY_MAP:
                    unified = CAPABILITY_MAP[cap]
                    if unified != cap:
                        new_caps.append(unified)
                        indent = line[: len(line) - len(line.lstrip())]
                        new_lines.append(f"{indent}- {unified}")
                        changed = True
                    else:
                        new_caps.append(cap)
                        new_lines.append(line)
                else:
                    new_caps.append(cap)
                    new_lines.append(line)
            else:
                in_capabilities = False
                new_lines.append(line)
        else:
            new_lines.append(line)

    if changed and not dry_run:
        yaml_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return old_caps, new_caps


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate agent capabilities to unified vocabulary"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Apply changes (default is dry-run)"
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path(__file__).parent.parent / "config" / "agents",
        help="Directory containing agent YAML files",
    )
    args = parser.parse_args()

    config_dir: Path = args.config_dir
    if not config_dir.is_dir():
        print(f"Config directory not found: {config_dir}")
        return

    yaml_files = sorted(config_dir.glob("*.yaml"))
    if not yaml_files:
        print(f"No YAML files found in {config_dir}")
        return

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Capability Migration ({mode})")
    print(f"Config dir: {config_dir}")
    print(f"Files to process: {len(yaml_files)}")
    print()

    total_files = 0
    total_changed = 0

    for yaml_file in yaml_files:
        old_caps, new_caps = migrate_capabilities(yaml_file, dry_run=not args.apply)
        if not old_caps:
            continue

        total_files += 1
        changed = old_caps != new_caps
        if changed:
            total_changed += 1

        status = "CHANGED" if changed else "OK"
        print(f"  [{status}] {yaml_file.name}")
        if changed:
            for old, new in zip(old_caps, new_caps):
                if old != new:
                    print(f"    - {old} -> {new}")

    print()
    print(f"Processed {total_files} files, {total_changed} changed")

    if not args.apply and total_changed > 0:
        print(f"\nTo apply changes, run: python {__file__} --apply")


if __name__ == "__main__":
    main()
