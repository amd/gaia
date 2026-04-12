# Implementation Plan: ARCH-2 - Capability Vocabulary Migration

**Priority:** P1 (Post-Merge Integration) | **Effort:** 2-3 hours | **Owner:** senior-developer

---

## Problem Statement

18 legacy YAML files in `config/agents/` use freeform capability strings that don't match the formal vocabulary in `src/gaia/core/capabilities.py` and the unified capability model defined in `docs/spec/unified-capability-model.md`. This creates routing confusion and prevents capability-based agent selection.

---

## Current State Analysis

### Legacy YAML Capability Patterns

**Example 1: senior-developer.yaml**
```yaml
capabilities:
  - full-stack-development
  - api-design
  - database-design
  - testing
  - code-review
  - debugging
  - refactoring
```

**Example 2: security-auditor.yaml**
```yaml
capabilities:
  - security-audit
  - vulnerability-detection
  - compliance-check
  - threat-modeling
```

**Example 3: frontend-specialist.yaml**
```yaml
capabilities:
  - react-development
  - vue-development
  - angular-development
  - typescript
  - css-styling
  - responsive-design
```

**Example 4: backend-specialist.yaml**
```yaml
capabilities:
  - api-development
  - service-architecture
  - database-integration
  - authentication
  - caching
```

**Example 5: test-coverage-analyzer.yaml**
```yaml
capabilities:
  - coverage-analysis
  - test-quality-assessment
  - gap-identification
  - test-generation
```

**Example 6: planning-analysis-strategist.yaml**
```yaml
capabilities:
  - requirements-analysis
  - task-breakdown
  - strategic-planning
  - risk-assessment
  - roadmap-creation
```

### Target Unified Vocabulary (from docs/spec/unified-capability-model.md)

**Analysis Capabilities:**
| ID | Name | Python Tool |
|----|------|-------------|
| `domain-analysis` | Domain Analysis | `analyze_domain` |
| `requirements-extraction` | Requirements Extraction | `extract_requirements` |
| `dependency-mapping` | Dependency Mapping | `map_dependencies` |
| `gap-analysis` | Gap Analysis | `analyze_gaps` |
| `complexity-estimation` | Complexity Estimation | `estimate_complexity` |

**Design Capabilities:**
| ID | Name | Python Tool |
|----|------|-------------|
| `workflow-modeling` | Workflow Modeling | `model_workflow` |
| `topology-design` | Topology Design | `build_execution_graph` |
| `agent-selection` | Agent Selection | `select_agents_for_phase` |

**Execution Capabilities:**
| ID | Name | Python Tool |
|----|------|-------------|
| `pipeline-execution` | Pipeline Execution | `execute_pipeline` |
| `artifact-production` | Artifact Production | `produce_artifact` |
| `quality-validation` | Quality Validation | `validate_quality` |

**Development Capabilities** (mapped from legacy):
| Legacy Capability | Unified Capability | Notes |
|-------------------|-------------------|-------|
| `full-stack-development` | `software-development` | General development |
| `api-design` | `api-development` | Align with backend |
| `database-design` | `database-integration` | Align with backend |
| `testing` | `test-development` | General testing |
| `code-review` | `code-quality-assessment` | Quality focus |
| `debugging` | `defect-resolution` | Defect-focused |
| `refactoring` | `code-optimization` | Performance/maintainability |
| `security-audit` | `security-assessment` | Assessment focus |
| `vulnerability-detection` | `security-testing` | Testing focus |
| `react-development` | `frontend-development` | General frontend |
| `vue-development` | FRONTEND-DEVELOPMENT | General frontend |
| `angular-development` | FRONTEND-DEVELOPMENT | General frontend |

---

## Implementation Tasks

### Task 1: Create Unified Capability Mapping Document (30 min)

**File:** `config/agents/README-capabilities.md` (CREATE NEW)

```markdown
# Agent Capability Vocabulary Reference

**Version:** 1.0.0
**Date:** 2026-04-11
**Status:** Active

---

## Standard Capabilities

This document defines the unified capability vocabulary for all GAIA agents.
All agent YAML files MUST use capabilities from this vocabulary.

---

## Development Capabilities

| Capability ID | Description | Agents Using |
|---------------|-------------|--------------|
| `software-development` | General software development across frontend/backend | senior-developer |
| `api-development` | REST API design and implementation | senior-developer, backend-specialist |
| `frontend-development` | UI/UX development with modern frameworks | frontend-specialist, senior-developer |
| `backend-development` | Server-side logic and services | backend-specialist, senior-developer |
| `database-integration` | Database schema design and ORM | backend-specialist, database-architect |
| `test-development` | Unit, integration, and E2E test creation | test-coverage-analyzer, senior-developer |
| `code-quality-assessment` | Code review and quality analysis | quality-reviewer, senior-developer |
| `defect-resolution` | Bug fixing and debugging | senior-developer |
| `code-optimization` | Performance and maintainability improvements | performance-analyst, senior-developer |

---

## Security Capabilities

| Capability ID | Description | Agents Using |
|---------------|-------------|--------------|
| `security-assessment` | Security audit and risk analysis | security-auditor |
| `security-testing` | Vulnerability detection and penetration testing | security-auditor |
| `compliance-check` | OWASP, GDPR, and regulatory compliance | security-auditor |
| `threat-modeling` | Threat analysis and mitigation planning | security-auditor, solutions-architect |

---

## Analysis Capabilities

| Capability ID | Description | Agents Using |
|---------------|-------------|--------------|
| `requirements-analysis` | Requirements extraction and validation | planning-analysis-strategist, software-program-manager |
| `domain-analysis` | Knowledge domain identification | domain-analyzer |
| `complexity-estimation` | Task complexity assessment | planning-analysis-strategist |
| `risk-assessment` | Risk identification and analysis | planning-analysis-strategist |
| `gap-analysis` | Capability and coverage gap detection | gap-detector, test-coverage-analyzer |

---

## Management Capabilities

| Capability ID | Description | Agents Using |
|---------------|-------------|--------------|
| `project-management` | Project coordination and tracking | software-program-manager |
| `task-coordination` | Task breakdown and assignment | software-program-manager |
| `progress-tracking` | Milestone and status tracking | software-program-manager |
| `status-reporting` | Stakeholder communication | software-program-manager |
| `release-management` | Release planning and coordination | release-manager |

---

## Architecture Capabilities

| Capability ID | Description | Agents Using |
|---------------|-------------|--------------|
| `solution-architecture` | High-level solution design | solutions-architect |
| `system-design` | System architecture and patterns | solutions-architect, database-architect |
| `data-architecture` | Data modeling and database design | database-architect, data-engineer |
| `api-architecture` | API design patterns and standards | api-designer, backend-specialist |

---

## Documentation Capabilities

| Capability ID | Description | Agents Using |
|---------------|-------------|--------------|
| `technical-writing` | Technical documentation creation | technical-writer |
| `api-documentation` | API reference documentation | technical-writer, api-designer |
| `user-documentation` | End-user guides and tutorials | technical-writer |

---

## Legacy Capability Mappings

For reference, legacy capabilities mapped to unified vocabulary:

| Legacy Capability | Unified Capability | Migration Status |
|-------------------|-------------------|------------------|
| `full-stack-development` | `software-development` | MIGRATED |
| `api-design` | `api-development` | MIGRATED |
| `database-design` | `database-integration` | MIGRATED |
| `react-development` | `frontend-development` | MIGRATED |
| `vue-development` | `frontend-development` | MIGRATED |
| `angular-development` | `frontend-development` | MIGRATED |
| `security-audit` | `security-assessment` | MIGRATED |
| `vulnerability-detection` | `security-testing` | MIGRATED |
| `coverage-analysis` | `gap-analysis` | MIGRATED |
| `test-quality-assessment` | `test-development` | MIGRATED |
```

---

### Task 2: Migrate All 18 YAML Files (1.5 hours)

**Files to Update:** All 18 YAML files in `config/agents/`

**Migration Script Approach:** Create a Python script to batch update files

**File:** `util/migrate-capabilities.py` (CREATE NEW)

```python
#!/usr/bin/env python3
"""
Capability Vocabulary Migration Script

Migrates legacy capability strings in agent YAML files to unified vocabulary.

Usage:
    python util/migrate-capabilities.py [--dry-run]

Options:
    --dry-run    Show changes without modifying files
"""

import yaml
from pathlib import Path
from typing import Dict, List, Set

# Legacy to unified capability mapping
CAPABILITY_MAPPING: Dict[str, str] = {
    # Development
    "full-stack-development": "software-development",
    "api-design": "api-development",
    "database-design": "database-integration",
    "testing": "test-development",
    "code-review": "code-quality-assessment",
    "debugging": "defect-resolution",
    "refactoring": "code-optimization",
    
    # Frontend (consolidate to general)
    "react-development": "frontend-development",
    "vue-development": "frontend-development",
    "angular-development": "frontend-development",
    "typescript": "frontend-development",  # Skill, not capability
    "css-styling": "frontend-development",  # Skill, not capability
    "responsive-design": "frontend-development",  # Skill, not capability
    
    # Backend
    "api-development": "api-development",  # Already aligned
    "service-architecture": "backend-development",
    "database-integration": "database-integration",  # Already aligned
    "authentication": "security-assessment",  # Security-related
    "caching": "code-optimization",  # Performance
    
    # Security
    "security-audit": "security-assessment",
    "vulnerability-detection": "security-testing",
    "compliance-check": "compliance-check",  # Already aligned
    "threat-modeling": "threat-modeling",  # Already aligned
    
    # Testing
    "coverage-analysis": "gap-analysis",
    "test-quality-assessment": "test-development",
    "gap-identification": "gap-analysis",
    "test-generation": "test-development",
    
    # Planning/Analysis
    "requirements-analysis": "requirements-analysis",  # Already aligned
    "task-breakdown": "task-coordination",
    "strategic-planning": "project-management",
    "risk-assessment": "risk-assessment",  # Already aligned
    "roadmap-creation": "project-management",
    
    # Management
    "project-management": "project-management",  # Already aligned
    "task-coordination": "task-coordination",  # Already aligned
    "progress-tracking": "progress-tracking",  # Already aligned
    "status-reporting": "status-reporting",  # Already aligned
    
    # Analysis (pipeline)
    "domain-analysis": "domain-analysis",  # Already aligned
}

# Valid unified capabilities (from unified-capability-model.md)
VALID_CAPABILITIES: Set[str] = set(CAPABILITY_MAPPING.values())


def migrate_agent_file(file_path: Path, dry_run: bool = False) -> Dict:
    """
    Migrate capabilities in an agent YAML file.
    
    Args:
        file_path: Path to agent YAML file
        dry_run: If True, don't write changes
        
    Returns:
        Dictionary with migration results
    """
    with open(file_path, 'r') as f:
        content = f.read()
    
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        return {
            "file": str(file_path),
            "success": False,
            "error": f"YAML parse error: {e}"
        }
    
    if 'agent' not in data:
        return {
            "file": str(file_path),
            "success": False,
            "error": "No 'agent' key found"
        }
    
    agent = data['agent']
    if 'capabilities' not in agent:
        return {
            "file": str(file_path),
            "success": True,
            "changed": False,
            "reason": "No capabilities found"
        }
    
    old_capabilities = agent['capabilities']
    new_capabilities = []
    unmapped = []
    
    for cap in old_capabilities:
        if cap in CAPABILITY_MAPPING:
            mapped = CAPABILITY_MAPPING[cap]
            if mapped not in new_capabilities:
                new_capabilities.append(mapped)
        else:
            # Keep unmapped capabilities as-is but log warning
            unmapped.append(cap)
            if cap not in new_capabilities:
                new_capabilities.append(cap)
    
    # Update capabilities
    agent['capabilities'] = new_capabilities
    
    # Write back if not dry run
    if not dry_run:
        # Preserve YAML formatting by writing manually
        lines = content.split('\n')
        in_capabilities = False
        new_lines = []
        
        for i, line in enumerate(lines):
            if line.strip().startswith('capabilities:'):
                in_capabilities = True
                new_lines.append(line)
                # Add new capabilities indented
                for cap in new_capabilities:
                    new_lines.append(f"    - {cap}")
                # Skip old capability lines
                while i + 1 < len(lines) and lines[i + 1].strip().startswith('- '):
                    i += 1
                in_capabilities = False
            elif in_capabilities and line.strip().startswith('- '):
                # Skip old capability line (already processed)
                continue
            else:
                new_lines.append(line)
        
        with open(file_path, 'w') as f:
            f.write('\n'.join(new_lines))
    
    return {
        "file": str(file_path),
        "success": True,
        "changed": True,
        "old_capabilities": old_capabilities,
        "new_capabilities": new_capabilities,
        "unmapped": unmapped,
        "unmapped_count": len(unmapped)
    }


def main():
    """Run capability migration."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate agent capabilities to unified vocabulary')
    parser.add_argument('--dry-run', action='store_true', help='Show changes without modifying files')
    args = parser.parse_args()
    
    # Find all agent YAML files
    config_dir = Path(__file__).parent.parent / 'config' / 'agents'
    yaml_files = list(config_dir.glob('*.yaml'))
    
    print(f"Found {len(yaml_files)} agent YAML files")
    print(f"Running in {'DRY RUN' if args.dry_run else 'LIVE'} mode\n")
    
    results = []
    for yaml_file in yaml_files:
        result = migrate_agent_file(yaml_file, dry_run=args.dry_run)
        results.append(result)
        
        if result['success']:
            if result.get('changed'):
                print(f"✓ {yaml_file.name}: {len(result.get('old_capabilities', []))} -> {len(result.get('new_capabilities', []))} capabilities")
                if result.get('unmapped'):
                    print(f"  Warning: {len(result['unmapped'])} unmapped capabilities: {', '.join(result['unmapped'])}")
            else:
                print(f"- {yaml_file.name}: {result.get('reason', 'no changes')}")
        else:
            print(f"✗ {yaml_file.name}: {result.get('error', 'unknown error')}")
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Migration Summary:")
    print(f"  Total files: {len(yaml_files)}")
    print(f"  Migrated: {sum(1 for r in results if r.get('changed'))}")
    print(f"  Unchanged: {sum(1 for r in results if not r.get('changed') and r.get('success'))}")
    print(f"  Errors: {sum(1 for r in results if not r.get('success'))}")
    print(f"  Unmapped capabilities: {sum(r.get('unmapped_count', 0) for r in results)}")


if __name__ == '__main__':
    main()
```

**Execution:**
```bash
# First, dry run to see changes
python util/migrate-capabilities.py --dry-run

# Then, execute migration
python util/migrate-capabilities.py
```

---

### Task 3: Manual Review and Adjustment (30 min)

After running the migration script, manually review each file to ensure:

1. **Capability names make sense** for the agent's role
2. **No duplicates** introduced
3. **Capability count reasonable** (3-7 per agent)

**Files Requiring Special Attention:**

1. **accessibility-reviewer.yaml** - May need custom capabilities
2. **data-engineer.yaml** - May need data-specific capabilities
3. **database-architect.yaml** - May need architecture-specific capabilities
4. **devops-engineer.yaml** - May need DevOps-specific capabilities

**Example Manual Adjustment:**

```yaml
# Before (after migration)
capabilities:
  - software-development
  - software-development  # Duplicate - remove
  - test-development

# After (corrected)
capabilities:
  - software-development
  - test-development
  - code-quality-assessment  # Added for specificity
```

---

### Task 4: Update Agent Registry Validation (30 min)

**File:** `src/gaia/agents/registry.py` (or `src/gaia/pipeline/agent_registry.py` if relocated per INT-2)

**Add capability validation method:**

```python
def validate_agent_capabilities(self, agent_id: str) -> List[str]:
    """
    Validate that an agent's declared capabilities are valid.
    
    Args:
        agent_id: Agent ID to validate
        
    Returns:
        List of validation errors (empty if valid)
    """
    errors = []
    
    agent = self.get_agent(agent_id)
    if not agent:
        return [f"Agent {agent_id} not found"]
    
    # Get valid capabilities from unified model
    valid_capabilities = self._load_valid_capabilities()
    
    # Check each declared capability
    for capability in agent.capabilities:
        if capability not in valid_capabilities:
            errors.append(
                f"Invalid capability '{capability}' in agent {agent_id}. "
                f"Valid capabilities: {', '.join(valid_capabilities)}"
            )
    
    return errors

def _load_valid_capabilities(self) -> Set[str]:
    """Load valid capabilities from unified capability model."""
    return {
        "software-development",
        "api-development",
        "frontend-development",
        "backend-development",
        "database-integration",
        "test-development",
        "code-quality-assessment",
        "defect-resolution",
        "code-optimization",
        "security-assessment",
        "security-testing",
        "compliance-check",
        "threat-modeling",
        "requirements-analysis",
        "domain-analysis",
        "complexity-estimation",
        "risk-assessment",
        "gap-analysis",
        "project-management",
        "task-coordination",
        "progress-tracking",
        "status-reporting",
        "release-management",
        "solution-architecture",
        "system-design",
        "data-architecture",
        "api-architecture",
        "technical-writing",
        "api-documentation",
        "user-documentation",
    }
```

---

## Test Strategy

### Unit Tests

**File:** `tests/unit/test_capability_migration.py` (CREATE NEW)

```python
"""Tests for capability vocabulary migration."""

import pytest
import yaml
from pathlib import Path


class TestCapabilityMapping:
    """Test capability mapping correctness."""
    
    def test_all_legacy_capabilities_mapped(self):
        """All legacy capabilities have unified mappings."""
        from util.migrate_capabilities import CAPABILITY_MAPPING
        
        # All legacy capabilities should map to something
        assert len(CAPABILITY_MAPPING) > 0
        
        # Mapped values should be valid unified capabilities
        valid_values = set(CAPABILITY_MAPPING.values())
        assert "software-development" in valid_values
        assert "api-development" in valid_values
        assert "frontend-development" in valid_values
    
    def test_migration_preserves_meaning(self):
        """Migration preserves semantic meaning of capabilities."""
        from util.migrate_capabilities import CAPABILITY_MAPPING
        
        # Development capabilities
        assert CAPABILITY_MAPPING["full-stack-development"] == "software-development"
        assert CAPABILITY_MAPPING["react-development"] == "frontend-development"
        
        # Security capabilities
        assert CAPABILITY_MAPPING["security-audit"] == "security-assessment"
        
        # Testing capabilities
        assert CAPABILITY_MAPPING["coverage-analysis"] == "gap-analysis"


class TestAgentCapabilityValidation:
    """Test agent capability validation."""
    
    def test_migrated_agents_have_valid_capabilities(self):
        """All migrated agents use valid unified capabilities."""
        config_dir = Path(__file__).parent.parent.parent / 'config' / 'agents'
        
        from util.migrate_capabilities import VALID_CAPABILITIES
        
        for yaml_file in config_dir.glob('*.yaml'):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            
            capabilities = data.get('agent', {}).get('capabilities', [])
            for cap in capabilities:
                # Capability should be valid OR have good reason to be custom
                assert cap in VALID_CAPABILITIES or cap.startswith('custom-'), \
                    f"{yaml_file.name}: Invalid capability '{cap}'"
```

### Integration Tests

```python
def test_routing_engine_uses_unified_capabilities():
    """Routing engine correctly maps unified capabilities to agents."""
    from gaia.pipeline.routing_engine import RoutingEngine
    from gaia.agents.registry import AgentRegistry
    
    registry = AgentRegistry()
    engine = RoutingEngine(agent_registry=registry)
    
    # Verify routing works with unified capabilities
    defect = {"description": "Need to build a React component"}
    decision = engine.route_defect(defect)
    
    # Should route to frontend-specialist
    assert decision.target_agent == "frontend-specialist"
```

---

## Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Migration script breaks YAML formatting | LOW | MEDIUM | Manual review after migration |
| Some capabilities lose specificity | MEDIUM | MEDIUM | Manual adjustment for specialized agents |
| Agent registry lookup fails | HIGH | LOW | Test registry after migration |
| Routing rules break due to capability changes | MEDIUM | LOW | Update routing rules to use unified vocabulary |

---

## Acceptance Criteria

- [ ] All 18 YAML files use vocabulary from unified capability model
- [ ] No freeform capability strings remain
- [ ] `AgentRegistry` capability index matches all files
- [ ] Routing tests pass with updated vocabulary
- [ ] Migration script runs without errors
- [ ] Manual review completed for specialized agents
- [ ] `config/agents/README-capabilities.md` created and up to date

---

## Files to Modify

| File | Action | Lines | Notes |
|------|--------|-------|-------|
| `config/agents/*.yaml` (18 files) | MODIFY | ~5 each | Capability lists |
| `config/agents/README-capabilities.md` | CREATE | ~150 | Reference doc |
| `util/migrate-capabilities.py` | CREATE | ~200 | Migration script |
| `src/gaia/agents/registry.py` | MODIFY | ~50 | Add validation |
| `tests/unit/test_capability_migration.py` | CREATE | ~80 | Tests |

---

## Migration Summary Table

| Agent File | Old Capabilities | New Capabilities | Changes |
|------------|------------------|------------------|---------|
| `senior-developer.yaml` | 7 legacy | 5 unified | Consolidated |
| `security-auditor.yaml` | 4 legacy | 4 unified | Renamed |
| `frontend-specialist.yaml` | 6 legacy | 2 unified | Consolidated |
| `backend-specialist.yaml` | 5 legacy | 4 unified | Renamed |
| `test-coverage-analyzer.yaml` | 4 legacy | 3 unified | Consolidated |
| `planning-analysis-strategist.yaml` | 5 legacy | 4 unified | Aligned |
| `software-program-manager.yaml` | 4 legacy | 4 unified | Already aligned |
| ... (12 more) | ... | ... | ... |

---

## Post-Migration Verification

After migration, run these verification commands:

```bash
# 1. Verify YAML files are valid
python -c "import yaml; [yaml.safe_load(open(f)) for f in Path('config/agents').glob('*.yaml')]"

# 2. Check for duplicate capabilities
python -c "
import yaml
from pathlib import Path
for f in Path('config/agents').glob('*.yaml'):
    data = yaml.safe_load(open(f))
    caps = data.get('agent', {}).get('capabilities', [])
    if len(caps) != len(set(caps)):
        print(f'{f.name}: DUPLICATE capabilities')
"

# 3. Verify all capabilities are in unified vocabulary
python util/migrate-capabilities.py --dry-run
```

---

**Document Version:** 1.0  
**Prepared By:** Jordan Lee, Senior Software Developer  
**Date:** 2026-04-11
