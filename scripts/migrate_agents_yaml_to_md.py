#!/usr/bin/env python
"""
Migration script: Convert YAML agent definitions to MD frontmatter format.

Reads all .yaml files from config/agents/ and produces equivalent .md files
with YAML frontmatter + Markdown body. Does NOT modify or delete source YAML files.

Usage:
    python scripts/migrate_agents_yaml_to_md.py [--agents-dir PATH] [--dry-run]
"""

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)

# Placeholder prompt body for agents without existing prompt files
PLACEHOLDER_PROMPT = """# {AGENT_NAME} — {ROLE_TITLE}

## Identity and Purpose

[This agent prompt body needs to be authored. The original YAML agent definition
pointed to a non-existent prompt file: {ORIGINAL_PROMPT_REF}]

## Core Principles

- [To be authored based on agent role and capabilities]

## Workflow

### Phase 1: Analysis

[To be authored]

### Phase 2: Implementation

[To be authored]

### Phase 3: Validation

[To be authored]

## Output Specification

[To be authored]

## Constraints and Safety

[To be authored]
"""


def convert_complexity_range(triggers: dict) -> dict:
    """Convert complexity_range from dict to list format."""
    if not triggers:
        return triggers
    raw = triggers.get("complexity_range")
    if isinstance(raw, dict):
        triggers["complexity_range"] = [
            float(raw.get("min", 0.0)),
            float(raw.get("max", 1.0)),
        ]
    return triggers


def build_frontmatter(agent_data: dict) -> str:
    """Build YAML frontmatter string from agent data."""
    # Remove system_prompt from frontmatter (it goes in the body)
    frontmatter_data = {k: v for k, v in agent_data.items() if k != "system_prompt"}

    # Convert complexity_range from dict to list format
    if "triggers" in frontmatter_data:
        frontmatter_data["triggers"] = convert_complexity_range(
            frontmatter_data["triggers"]
        )

    return yaml.dump(frontmatter_data, default_flow_style=False, sort_keys=False, allow_unicode=True)


def get_placeholder_prompt(agent_data: dict, original_prompt_ref: str) -> str:
    """Generate a placeholder prompt body."""
    name = agent_data.get("name", agent_data.get("id", "Unknown Agent"))
    category = agent_data.get("category", "general")
    category_title = category.replace("-", " ").title()

    return PLACEHOLDER_PROMPT.format(
        AGENT_NAME=name,
        ROLE_TITLE=category_title,
        ORIGINAL_PROMPT_REF=original_prompt_ref,
    )


def migrate_agent(yaml_path: Path, output_dir: Path, dry_run: bool = False) -> dict:
    """Migrate a single YAML agent file to MD format.

    Returns a dict with migration results: {status, input, output, prompt_source}
    """
    result = {
        "status": "pending",
        "input": str(yaml_path),
        "output": "",
        "prompt_source": "",
    }

    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            result["status"] = "error: empty file"
            return result

        # Handle both nested (agent: key) and flat formats
        agent_data = data.get("agent", data)

        original_prompt_ref = agent_data.get("system_prompt", "")

        # Check if the referenced prompt file exists
        prompt_body = ""
        if original_prompt_ref and original_prompt_ref != "":
            # The prompt path is relative to the agents directory
            prompt_path = yaml_path.parent / original_prompt_ref
            if prompt_path.exists():
                with open(prompt_path, "r", encoding="utf-8") as pf:
                    prompt_body = pf.read()
                result["prompt_source"] = f"loaded from {original_prompt_ref}"
            else:
                prompt_body = get_placeholder_prompt(agent_data, original_prompt_ref)
                result["prompt_source"] = f"placeholder (original ref: {original_prompt_ref})"
        else:
            prompt_body = get_placeholder_prompt(agent_data, "not specified")
            result["prompt_source"] = "placeholder (no system_prompt in YAML)"

        # Build frontmatter
        frontmatter = build_frontmatter(agent_data)

        # Construct the MD file content
        md_content = f"---\n{frontmatter}---\n\n{prompt_body}\n"

        # Determine output path
        output_path = output_dir / f"{yaml_path.stem}.md"
        result["output"] = str(output_path)

        if not dry_run:
            with open(output_path, "w", encoding="utf-8-sig") as f:
                f.write(md_content)

        result["status"] = "success"

    except Exception as e:
        result["status"] = f"error: {e}"

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Migrate YAML agent definitions to MD frontmatter format"
    )
    parser.add_argument(
        "--agents-dir",
        default="config/agents",
        help="Directory containing YAML agent files (default: config/agents)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be converted without writing files",
    )
    args = parser.parse_args()

    agents_dir = Path(args.agents_dir)
    if not agents_dir.exists():
        print(f"Error: Agents directory '{agents_dir}' does not exist")
        sys.exit(1)

    yaml_files = sorted(agents_dir.glob("*.yaml"))
    if not yaml_files:
        print(f"No YAML files found in '{agents_dir}'")
        sys.exit(0)

    print(f"Found {len(yaml_files)} YAML agent files to migrate")
    print(f"Output directory: {agents_dir}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()

    results = []
    for yaml_file in yaml_files:
        result = migrate_agent(yaml_file, agents_dir, args.dry_run)
        results.append(result)
        status_icon = "[OK]" if result["status"] == "success" else "[FAIL]"
        print(f"  {status_icon} {yaml_file.name} -> {result['output']}")
        print(f"     Status: {result['status']}")
        print(f"     Prompt: {result['prompt_source']}")

    # Summary
    success = [r for r in results if r["status"] == "success"]
    errors = [r for r in results if r["status"].startswith("error")]

    print()
    print(f"Migration complete: {len(success)} succeeded, {len(errors)} failed")

    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"  - {err['input']}: {err['status']}")
        sys.exit(1)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
