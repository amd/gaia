---
template_id: domain-analysis-checklist
template_type: checklists
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Checklist for analyzing domain boundaries, entities, and requirements
schema_version: "1.0"
---

# Domain Analysis Checklist

## Purpose

This checklist guides the Domain Analyzer through comprehensive domain analysis, ensuring all critical aspects of the domain are understood before proceeding with implementation.

## Checklist Structure

This checklist has three tiers of checks:
- **Required Checks:** Must all pass for domain analysis to be complete
- **Recommended Checks:** Majority should pass (>= 70%)
- **Advisory Checks:** Informational, nice to have

## Required Checks (Must All Pass)

- [ ] **Domain Boundaries Clearly Defined**
  - What is inside the domain
  - What is outside the domain
  - Boundary conditions and edge cases

- [ ] **Key Stakeholders Identified**
  - Primary users of the domain
  - Secondary stakeholders
  - Domain experts available for consultation

- [ ] **Core Terminology Documented**
  - Glossary of domain-specific terms
  - Definitions agreed upon by stakeholders
  - Ambiguous terms flagged for clarification

- [ ] **Existing Systems Catalogued**
  - Current systems operating in the domain
  - System interfaces and integrations
  - Legacy systems and migration considerations

- [ ] **Data Sources Identified**
  - Primary data sources
  - Data owners and custodians
  - Data access methods and restrictions

## Recommended Checks (Majority Should Pass)

- [ ] **Historical Context Captured**
  - How the domain has evolved
  - Past solutions and their outcomes
  - Lessons learned from previous efforts

- [ ] **Regulatory Constraints Noted**
  - Applicable regulations and standards
  - Compliance requirements
  - Audit and reporting obligations

- [ ] **Performance Requirements Quantified**
  - Response time expectations
  - Throughput requirements
  - Scalability needs

- [ ] **Security Requirements Specified**
  - Data classification levels
  - Access control requirements
  - Encryption and privacy needs

- [ ] **Integration Points Mapped**
  - Upstream systems
  - Downstream systems
  - Peer systems

- [ ] **Business Processes Documented**
  - Key workflows in the domain
  - Decision points and gates
  - Exception handling paths

- [ ] **Success Criteria Defined**
  - Measurable outcomes
  - Acceptance criteria
  - Quality gates

## Advisory Checks (Informational)

- [ ] **Industry Best Practices Referenced**
  - Relevant standards
  - Common patterns in the domain
  - Benchmark organizations

- [ ] **Competitive Landscape Analyzed**
  - Similar solutions in market
  - Differentiators
  - Market expectations

- [ ] **Technology Trends Assessed**
  - Emerging technologies relevant to domain
  - Technology adoption considerations
  - Innovation opportunities

- [ ] **Risk Factors Identified**
  - Technical risks
  - Business risks
  - Operational risks

- [ ] **Resource Requirements Estimated**
  - Skill requirements
  - Tool and infrastructure needs
  - Budget considerations

## Domain Analysis Artifacts

| Artifact | Status | Location | Owner |
|----------|--------|----------|-------|
| Domain Glossary | {{STATUS}} | {{PATH}} | {{OWNER}} |
| System Context Diagram | {{STATUS}} | {{PATH}} | {{OWNER}} |
| Stakeholder Map | {{STATUS}} | {{PATH}} | {{OWNER}} |
| Data Flow Diagram | {{STATUS}} | {{PATH}} | {{OWNER}} |

## Pass/Fail Decision

**PASS Criteria:**
- All required checks pass (100%)
- >= 70% of recommended checks pass
- Any failed required checks have approved waivers

**FAIL Criteria:**
- Any required check fails without waiver
- < 70% of recommended checks pass

### Decision Record

| Date | Result | Reviewed By | Notes |
|------|--------|-------------|-------|
| {{DATE}} | {{RESULT}} | {{WHO}} | {{NOTES}} |

## Sign-off

| Role | Name | Date | Sign-off |
|------|------|------|----------|
| Domain Analyst | {{NAME}} | {{DATE}} | Approved |
| Domain Expert | {{NAME}} | {{DATE}} | {{STATUS}} |
| Project Lead | {{NAME}} | {{DATE}} | {{STATUS}} |

## Related Components

- [[component-framework/knowledge/domain-knowledge.md]] - For documenting domain knowledge
- [[component-framework/checklists/workflow-modeling-checklist.md]] - For next phase
- [[component-framework/documents/design-doc.md]] - For design documentation
