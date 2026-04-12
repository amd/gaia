---
id: test-coverage-analyzer
name: Test Coverage Analyzer
version: 1.0.0
category: review
model_id: Qwen3-0.6B-GGUF
description: 'Testing specialist that analyzes test coverage,

  identifies gaps, and suggests test improvements.

  '
triggers:
  keywords:
  - test
  - coverage
  - unit test
  - integration test
  - test gap
  - mock
  - assertion
  phases:
  - QUALITY
  - REVIEW
  complexity_range:
  - 0.0
  - 1.0
capabilities:
- coverage-analysis
- test-quality-assessment
- gap-identification
- test-generation
tools:
- file_read
- run_tests
- coverage_report
constraints:
  max_file_changes: 10
  requires_review: true
metadata:
  author: GAIA Team
  created: '2026-03-23'
  tags:
  - testing
  - coverage
  - quality
---

# Test Coverage Analyzer — Review

## Identity and Purpose

[This agent prompt body needs to be authored. The original YAML agent definition
pointed to a non-existent prompt file: prompts/test-coverage-analyzer.md]

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

