---
template_id: waterfall-workflow
template_type: workflows
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Sequential phase progression workflow with strict stage gates
schema_version: "1.0"
---

# Waterfall Workflow Pattern

## Purpose

This workflow pattern defines a sequential, phase-gated approach where each phase must complete before the next begins. Best suited for well-understood problems with stable requirements.

## Workflow Characteristics

- **Flow Direction:** Strictly sequential (no iteration)
- **Phase Gates:** Formal review before phase transition
- **Documentation:** Heavy emphasis on upfront specification
- **Change Management:** Formal change control process

## Phase Structure

```
Phase 1: Requirements
    |
    v
[Requirements Gate] --Approved?--> Phase 2: Design
    |                                    |
  Reject                                 v
    |                             [Design Gate] --Approved?--> Phase 3: Implementation
    |                                    |
    +------------------------------------+
                                         v
Phase 6: Maintenance
    ^
    |
[Deployment Gate] --Approved?--> Phase 5: Testing
    |                                    ^
    |                                    |
  Reject                                 |
    |                                    |
    +------------------+-----------------+
                       |
                       v
Phase 4: Verification <-------+
```

## Phase Definitions

### Phase 1: Requirements Analysis

**Purpose:** Capture and document all requirements before design begins.

**Activities:**
1. Gather stakeholder requirements
2. Document functional specifications
3. Define acceptance criteria
4. Identify constraints and assumptions

**Entry Criteria:**
- [ ] Project charter approved
- [ ] Stakeholders identified
- [ ] Initial scope defined

**Exit Criteria:**
- [ ] Requirements document complete
- [ ] All requirements traceable
- [ ] Stakeholder sign-off obtained
- [ ] Requirements baseline established

**Artifacts Produced:**
- Requirements specification document
- Use case descriptions
- Acceptance criteria matrix

### Phase 2: System Design

**Purpose:** Create detailed design that satisfies all requirements.

**Activities:**
1. Architectural design
2. Component specification
3. Interface definitions
4. Database schema design

**Entry Criteria:**
- [ ] Requirements phase complete
- [ ] Requirements baseline approved

**Exit Criteria:**
- [ ] Design document complete
- [ ] Architecture reviewed
- [ ] Design patterns selected
- [ ] Technical approach validated

**Artifacts Produced:**
- System architecture document
- Component specifications
- Interface control documents
- Database design

### Phase 3: Implementation

**Purpose:** Build the system according to design specifications.

**Activities:**
1. Code development
2. Unit testing
3. Code review
4. Integration preparation

**Entry Criteria:**
- [ ] Design phase complete
- [ ] Development environment ready
- [ ] Implementation plan approved

**Exit Criteria:**
- [ ] All components implemented
- [ ] Unit tests passing
- [ ] Code review complete
- [ ] Integration build successful

**Artifacts Produced:**
- Source code
- Unit test results
- Code review reports
- Build artifacts

### Phase 4: Verification

**Purpose:** Verify the implementation matches design specifications.

**Activities:**
1. Integration testing
2. System verification
3. Traceability analysis
4. Defect tracking

**Entry Criteria:**
- [ ] Implementation phase complete
- [ ] Test environment ready
- [ ] Test cases prepared

**Exit Criteria:**
- [ ] All verification tests passed
- [ ] Defects resolved or documented
- [ ] Traceability matrix complete

**Artifacts Produced:**
- Verification test reports
- Defect logs
- Traceability matrix

### Phase 5: Validation (Testing)

**Purpose:** Validate the system meets user requirements.

**Activities:**
1. User acceptance testing
2. Performance validation
3. Security testing
4. Compliance verification

**Entry Criteria:**
- [ ] Verification phase complete
- [ ] System ready for UAT
- [ ] Test data prepared

**Exit Criteria:**
- [ ] UAT passed
- [ ] Performance criteria met
- [ ] Security approved
- [ ] Compliance verified

**Artifacts Produced:**
- UAT results
- Performance reports
- Security assessment
- Compliance certification

### Phase 6: Deployment & Maintenance

**Purpose:** Deploy to production and maintain ongoing operations.

**Activities:**
1. Production deployment
2. User training
3. Operational handover
4. Ongoing maintenance

**Entry Criteria:**
- [ ] Validation phase complete
- [ ] Production environment ready
- [ ] Training materials ready

**Exit Criteria:**
- [ ] System deployed
- [ ] Users trained
- [ ] Operations team briefed
- [ ] Support processes active

**Artifacts Produced:**
- Deployment documentation
- Training materials
- Operations手册
- Maintenance schedule

## Phase Gate Review Process

### Gate Review Steps

```
1. Phase Completion Report Submitted
   |
   v
2. Artifact Review (completeness check)
   |
   v
3. Quality Review (standards compliance)
   |
   v
4. Stakeholder Review (acceptance)
   |
   v
5. Gate Decision:
   - Approve: Proceed to next phase
   - Conditional Approve: Proceed with specific actions
   - Reject: Rework required, re-review needed
```

### Gate Review Checklist

- [ ] All phase artifacts complete
- [ ] Artifacts meet quality standards
- [ ] Exit criteria satisfied
- [ ] Stakeholders agree to proceed
- [ ] Risks for next phase identified

## Tool Invocation Patterns

### Phase Completion Recording

```python
# Record phase completion
completion = await self.tools.workflow.record_phase_complete(
    phase="{{PHASE_NAME}}",
    artifacts={{ARTIFACT_LIST}},
    exit_criteria={{CRITERIA_STATUS}}
)

# Request gate review
review = await self.tools.workflow.request_gate_review(
    phase="{{PHASE_NAME}}",
    reviewers={{REVIEWER_LIST}},
    scheduled_date="{{REVIEW_DATE}}"
)
```

### Gate Decision Processing

```python
# Process gate decision
decision = await self.tools.workflow.process_gate_decision(
    gate_id="{{GATE_ID}}",
    decision="{{APPROVE|REJECT|CONDITIONAL}}",
    conditions={{CONDITIONS}},
    next_phase="{{NEXT_PHASE}}"
)

# Handle rejection
if decision == "REJECT":
    rework_plan = await self.tools.workflow.create_rework_plan(
        phase="{{PHASE_NAME}}",
        deficiencies={{DEFICIENCIES}},
        required_actions={{ACTIONS}}
    )
```

## When to Use

**Waterfall is appropriate when:**
- Requirements are well-understood and stable
- Technology is proven and familiar
- Project is short and simple
- Regulatory compliance requires documentation
- Team is experienced with the domain

**Waterfall is NOT appropriate when:**
- Requirements are uncertain or evolving
- Technology is new or unproven
- Project is complex or high-risk
- Rapid delivery is required
- Innovation is needed

## Quality Criteria

- [ ] Each phase has clearly defined entry/exit criteria
- [ ] Gate reviews are conducted with appropriate stakeholders
- [ ] Artifacts meet quality standards before phase transition
- [ ] Change control process is followed for baseline changes
- [ ] Traceability is maintained from requirements to delivery

## Related Components

- [[component-framework/workflows/v-model-workflow.md]] - Enhanced verification variant
- [[component-framework/checklists/deployment-checklist.md]] - Deployment phase checklist
- [[component-framework/documents/design-doc.md]] - Design phase artifact
