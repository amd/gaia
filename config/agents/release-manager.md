---
id: release-manager
name: Release Manager
version: 1.0.0
category: management
model_id: Qwen3-0.6B-GGUF
description: 'Release management specialist that coordinates

  versioning, changelogs, and release processes.

  '
triggers:
  keywords:
  - release
  - version
  - changelog
  - tag
  - publish
  - deploy
  - rollout
  phases:
  - DEPLOYMENT
  - MANAGEMENT
  complexity_range:
  - 0.3
  - 1.0
capabilities:
- release-management
- versioning
- changelog-generation
- deployment-coordination
tools:
- file_read
- file_write
- git_operations
- bash_execute
constraints:
  max_file_changes: 10
  requires_review: true
metadata:
  author: GAIA Team
  created: '2026-03-23'
  tags:
  - release
  - versioning
  - deployment
---

# Release Manager — Management

## Identity and Purpose

[This agent prompt body needs to be authored. The original YAML agent definition
pointed to a non-existent prompt file: prompts/release-manager.md]

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

