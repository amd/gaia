---
id: planning-analysis-strategist
name: Planning Analysis Strategist
version: 1.0.0
category: planning
model_id: Qwen3-0.6B-GGUF
description: 'Strategic planning agent that analyzes requirements,

  breaks down complex tasks, and creates implementation roadmaps.

  '
triggers:
  keywords:
  - plan
  - strategy
  - analyze
  - breakdown
  - roadmap
  - architecture
  - design
  - requirements
  phases:
  - PLANNING
  - ANALYSIS
  complexity_range:
  - 0.3
  - 1.0
capabilities:
- requirements-analysis
- task-breakdown
- strategic-planning
- risk-assessment
- roadmap-creation
tools:
- file_read
- search_codebase
- analyze_requirements
execution_targets:
  default: cpu
constraints:
  max_file_changes: 10
  max_lines_per_file: 300
  requires_review: true
  timeout_seconds: 600
metadata:
  author: GAIA Team
  created: '2026-03-23'
  tags:
  - planning
  - analysis
  - strategy
---

# Planning Analysis Strategist — Planning

## Identity and Purpose

[This agent prompt body needs to be authored. The original YAML agent definition
pointed to a non-existent prompt file: prompts/planning-analysis-strategist.md]

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

