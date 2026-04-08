---
template_id: code-review-checklist
template_type: checklists
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Code review checklist for validating code quality and standards
schema_version: "1.0"
---

# Code Review Checklist

## Purpose

This checklist ensures code changes meet quality standards before merging. It covers functional correctness, code quality, security, and documentation requirements.

## Required Checks (Must All Pass)

- [ ] **Code Compiles Without Errors**
  - Build succeeds locally
  - Build succeeds in CI
  - No compiler warnings introduced

- [ ] **All Tests Pass**
  - Unit tests pass
  - Integration tests pass
  - No tests disabled or skipped without justification

- [ ] **No Security Vulnerabilities Introduced**
  - No hardcoded credentials
  - Input validation implemented
  - No SQL injection vulnerabilities
  - No command injection vulnerabilities

- [ ] **No Performance Regressions**
  - No significant performance degradation
  - Algorithm complexity is appropriate
  - Resource usage is within bounds

- [ ] **Documentation Updated**
  - Code comments where needed
  - API documentation updated
  - README updated if applicable
  - Changelog entry added

- [ ] **Proper Error Handling**
  - Exceptions caught and handled appropriately
  - Error messages are helpful
  - No silent failures

## Recommended Checks (Majority Should Pass)

- [ ] **Code Follows Style Guidelines**
  - Consistent with project style
  - Linting passes
  - Formatting is correct

- [ ] **Functions Have Appropriate Docstrings**
  - Public functions documented
  - Parameters described
  - Return values described
  - Exceptions documented

- [ ] **No Code Duplication**
  - DRY principles followed
  - Shared logic extracted
  - No copy-paste code

- [ ] **Logging is Appropriate**
  - Log levels used correctly
  - Sufficient context in logs
  - No sensitive data logged

- [ ] **Code is Testable**
  - Functions are pure where possible
  - Dependencies are injectable
  - Side effects are isolated

- [ ] **Variable Names are Descriptive**
  - Names convey purpose
  - No single-letter variables (except loop counters)
  - Consistent naming conventions

- [ ] **Functions are Focused**
  - Single responsibility
  - Reasonable function length
  - Clear function purpose

## Advisory Checks (Informational)

- [ ] **Code Could Be More Readable**
  - Complex logic simplified
  - Magic numbers replaced with constants
  - Nested conditionals reduced

- [ ] **Opportunities for Refactoring Identified**
  - Technical debt noted
  - Future improvements documented
  - Refactoring tickets created

- [ ] **Third-Party Dependencies Reviewed**
  - New dependencies justified
  - License compatibility checked
  - Security advisories checked

## Code Quality Metrics

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Test Coverage | {{TARGET}}% | {{ACTUAL}}% | {{STATUS}} |
| Cyclomatic Complexity | {{TARGET}} | {{ACTUAL}} | {{STATUS}} |
| Code Duplication | < {{TARGET}}% | {{ACTUAL}}% | {{STATUS}} |
| Lint Errors | 0 | {{ACTUAL}} | {{STATUS}} |

## Review Comments

| Type | Severity | Description | Location | Status |
|------|----------|-------------|----------|--------|
| {{TYPE}} | {{SEVERITY}} | {{DESC}} | {{LOCATION}} | {{STATUS}} |

### Comment Types
- Bug: Functional error
- Security: Security concern
- Performance: Performance issue
- Style: Code style issue
- Documentation: Missing/incorrect docs
- Suggestion: Improvement suggestion

### Severity Levels
- Critical: Must fix before merge
- Major: Should fix
- Minor: Nice to fix
- Info: FYI

## Pass/Fail Decision

**PASS Criteria:**
- All required checks pass
- No critical review comments unresolved
- >= 70% of recommended checks pass

**FAIL Criteria:**
- Any required check fails
- Any critical review comment unresolved
- Code does not meet quality threshold

### Decision Record

| Date | Result | Reviewer | Decision |
|------|--------|----------|----------|
| {{DATE}} | {{RESULT}} | {{WHO}} | {{DECISION}} |

## Sign-off

| Role | Name | Date | Sign-off |
|------|------|------|----------|
| Author | {{NAME}} | {{DATE}} | Submitted |
| Reviewer 1 | {{NAME}} | {{DATE}} | {{STATUS}} |
| Reviewer 2 | {{NAME}} | {{DATE}} | {{STATUS}} |
| Approver | {{NAME}} | {{DATE}} | {{STATUS}} |

## Related Components

- [[component-framework/commands/test-commands.md]] - For test execution
- [[component-framework/checklists/deployment-checklist.md]] - For deployment readiness
- [[component-framework/memory/episodic-memory.md]] - For review history
