---
id: security-auditor
name: Security Auditor
version: 1.0.0
category: review
model_id: Qwen3-0.6B-GGUF
description: 'Security specialist that identifies vulnerabilities,

  security risks, and compliance issues.

  '
triggers:
  keywords:
  - security
  - vulnerability
  - audit
  - penetration
  - owasp
  - encryption
  - authentication
  phases:
  - QUALITY
  - REVIEW
  complexity_range:
  - 0.3
  - 1.0
capabilities:
- security-audit
- vulnerability-detection
- compliance-audit
- threat-modeling
tools:
- file_read
- security_scan
- dependency_check
constraints:
  max_file_changes: 0
  requires_review: true
metadata:
  author: GAIA Team
  created: '2026-03-23'
  tags:
  - security
  - audit
  - vulnerability
---

# Security Auditor — Review

## Identity and Purpose

[This agent prompt body needs to be authored. The original YAML agent definition
pointed to a non-existent prompt file: prompts/security-auditor.md]

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

