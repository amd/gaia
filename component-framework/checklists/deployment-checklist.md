---
template_id: deployment-checklist
template_type: checklists
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Deployment checklist for validating release readiness and execution
schema_version: "1.0"
---

# Deployment Checklist

## Purpose

This checklist ensures deployments are executed safely and successfully, with proper preparation, execution, and post-deployment verification.

## Pre-Deployment

- [ ] **All Tests Pass in Staging**
  - Unit tests pass
  - Integration tests pass
  - E2E tests pass
  - Performance tests pass

- [ ] **Rollback Plan Documented**
  - Rollback procedure written
  - Rollback tested in staging
  - Rollback time estimated

- [ ] **Monitoring Configured**
  - Dashboards created/updated
  - Alerts configured
  - Log aggregation enabled

- [ ] **Alert Thresholds Set**
  - Error rate thresholds
  - Latency thresholds
  - Resource utilization thresholds

- [ ] **Runbook Updated**
  - Deployment steps documented
  - Troubleshooting guide updated
  - Contact list current

- [ ] **Stakeholders Notified**
  - Release notes distributed
  - Downtime communicated (if applicable)
  - Support team briefed

- [ ] **Database Migrations Prepared**
  - Migration scripts reviewed
  - Backup strategy defined
  - Rollback migrations tested

- [ ] **Feature Flags Configured**
  - Flags created
  - Default states set
  - Toggle procedures documented

## Deployment Execution

- [ ] **Pre-Deployment Backup Completed**
  - Database backed up
  - Configuration backed up
  - Backup verified

- [ ] **Deployment Script Tested**
  - Script validated in staging
  - Dry run completed
  - Script owner identified

- [ ] **Deployment Window Confirmed**
  - Timing approved
  - Team available
  - Freeze period checked

- [ ] **Health Checks Passing**
  - Application health endpoints
  - Database connectivity
  - External service connectivity

- [ ] **Logs Flowing Correctly**
  - Application logs visible
  - Error logs captured
  - Log levels appropriate

- [ ] **Metrics Reporting**
  - Application metrics flowing
  - System metrics visible
  - Business metrics tracked

- [ ] **Smoke Tests Executed**
  - Critical path tests pass
  - Integration points verified
  - User-facing functionality checked

## Post-Deployment

- [ ] **Smoke Tests Pass in Production**
  - All critical paths verified
  - Test results documented
  - Issues logged

- [ ] **User-Facing Functionality Verified**
  - UI tested (if applicable)
  - API endpoints tested
  - Key user journeys validated

- [ ] **Performance Baseline Established**
  - Response times recorded
  - Throughput measured
  - Resource usage baseline set

- [ ] **Incident Response Team Briefed**
  - On-call team aware
  - Escalation path clear
  - War room established (if needed)

- [ ] **Monitoring Review Completed**
  - No unusual errors
  - Metrics within expected ranges
  - No anomalies detected

- [ ] **Post-Deployment Sign-off Obtained**
  - Product owner approval
  - Technical lead approval
  - Operations approval

## Deployment Metrics

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Deployment Duration | {{TARGET}} | {{ACTUAL}} | {{STATUS}} |
| Downtime | {{TARGET}} | {{ACTUAL}} | {{STATUS}} |
| Rollback Required | No | {{ACTUAL}} | {{STATUS}} |
| Issues Found | 0 | {{ACTUAL}} | {{STATUS}} |

## Deployment Log

| Time | Action | Performed By | Status | Notes |
|------|--------|--------------|--------|-------|
| {{TIME}} | {{ACTION}} | {{WHO}} | {{STATUS}} | {{NOTES}} |

## Issues Encountered

| Issue | Severity | Resolution | Resolved By | Time to Resolve |
|-------|----------|------------|-------------|-----------------|
| {{ISSUE}} | {{SEVERITY}} | {{RESOLUTION}} | {{WHO}} | {{TIME}} |

## Pass/Fail Decision

**PASS Criteria:**
- All pre-deployment checks pass
- All deployment execution checks pass
- All post-deployment checks pass
- No critical issues outstanding

**FAIL Criteria:**
- Any required check fails
- Critical issues unresolved
- Rollback initiated

### Decision Record

| Date | Result | Deployed By | Notes |
|------|--------|-------------|-------|
| {{DATE}} | {{RESULT}} | {{WHO}} | {{NOTES}} |

## Sign-off

| Role | Name | Date | Sign-off |
|------|------|------|----------|
| Deployment Lead | {{NAME}} | {{DATE}} | Approved |
| Product Owner | {{NAME}} | {{DATE}} | {{STATUS}} |
| Operations | {{NAME}} | {{DATE}} | {{STATUS}} |

## Related Components

- [[component-framework/checklists/code-review-checklist.md]] - For pre-deployment code quality
- [[component-framework/commands/deploy-commands.md]] - For deployment commands
- [[component-framework/documents/status-report.md]] - For deployment reporting
