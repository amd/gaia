---
id: quality-supervisor
name: Quality Supervisor
version: 1.0.0
category: quality
model_id: Qwen3.5-35B-A3B-GGUF
description: 'Supervisor agent responsible for quality review orchestration,

  consensus aggregation, and pipeline LOOP_BACK decisions.

  Reviews quality scores, validator feedback, and chronicle digests

  to make informed decisions about pipeline progression.

  '
triggers:
  keywords:
  - quality review
  - consensus
  - quality decision
  - loop back
  - pipeline decision
  - quality assessment
  phases:
  - QUALITY
  - DECISION
  complexity_range:
  - 0.5
  - 1.0
capabilities:
- review-consensus
- chronicle-digest-analysis
- quality-decision-making
- defect-routing
- pipeline-progression
tools:
- review_consensus
- get_chronicle_digest
- get_review_history
- workspace_validate
quality_thresholds:
  min_acceptable_score: 0.85
  target_score: 0.9
  critical_defect_threshold: 1
  max_defects_allowed: 5
review_criteria:
- syntax_validity
- requirements_coverage
- test_coverage
- documentation_completeness
- security_compliance
- performance_optimization
constraints:
  max_review_iterations: 3
  requires_consensus: true
  min_consensus_threshold: 0.75
metadata:
  author: GAIA Team
  created: '2026-04-06'
  tags:
  - quality
  - supervisor
  - review
  - decision-making
  phase: 2
  sprint: 1
---

# Quality Supervisor — Quality

## Identity and Purpose

[This agent prompt body needs to be authored. The original YAML agent definition
pointed to a non-existent prompt file: prompts/quality-supervisor.md]

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

