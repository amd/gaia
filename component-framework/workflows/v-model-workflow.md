---
template_id: v-model-workflow
template_type: workflows
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Verification and validation workflow with parallel test development
schema_version: "1.0"
---

# V-Model Workflow Pattern

## Purpose

This workflow pattern defines a systematic approach where verification activities (left side) are balanced with validation activities (right side), with test planning occurring in parallel with development. Best suited for safety-critical systems and regulated environments.

## Workflow Characteristics

- **Flow Direction:** V-shaped with parallel test development
- **Phase Gates:** Formal verification before validation
- **Documentation:** Comprehensive test artifacts
- **Change Management:** Impact analysis on tests and requirements

## V-Model Structure

```
Requirements          Acceptance
Specification         Testing
      |                    ^
      |                    |
      v                    |
System Design        System Testing
      |                    ^
      |                    |
      v                    |
Architecture          Integration
Design                Testing
      |                    ^
      |                    |
      v                    |
Module Design        Unit Testing
      |                    ^
      |                    |
      +--------+-----------+
               |
          Implementation
```

## Left Side: Specification & Design

### Requirements Analysis

**Purpose:** Define what the system must do.

**Activities:**
1. Elicit stakeholder requirements
2. Document functional requirements
3. Define non-functional requirements
4. Create requirements traceability matrix

**Entry Criteria:**
- [ ] Stakeholder needs identified
- [ ] Project scope defined

**Exit Criteria:**
- [ ] Requirements specification complete
- [ ] Requirements reviewed and approved
- [ ] Acceptance test plan initiated

**Artifacts:**
- Requirements specification
- Use case documents
- Acceptance test plan (draft)

### System Design

**Purpose:** Define system architecture and components.

**Activities:**
1. Define system architecture
2. Identify subsystems
3. Define interfaces
4. Allocate requirements to components

**Entry Criteria:**
- [ ] Requirements approved
- [ ] Architecture team assigned

**Exit Criteria:**
- [ ] System design complete
- [ ] Architecture reviewed
- [ ] System test plan updated

**Artifacts:**
- System architecture document
- Interface specifications
- System test plan (draft)

### Architecture Design

**Purpose:** Define detailed architecture and component interactions.

**Activities:**
1. Design component architecture
2. Define communication protocols
3. Specify data models
4. Design security architecture

**Entry Criteria:**
- [ ] System design approved
- [ ] Architecture patterns selected

**Exit Criteria:**
- [ ] Architecture design complete
- [ ] Integration test plan updated

**Artifacts:**
- Architecture design document
- Component specifications
- Integration test plan (draft)

### Module Design

**Purpose:** Define detailed module specifications.

**Activities:**
1. Design module interfaces
2. Define algorithms
3. Specify data structures
4. Create detailed design documents

**Entry Criteria:**
- [ ] Architecture design approved
- [ ] Development team assigned

**Exit Criteria:**
- [ ] Module designs complete
- [ ] Designs reviewed
- [ ] Unit test plan updated

**Artifacts:**
- Module design documents
- Algorithm specifications
- Unit test plan (complete)

## Bottom: Implementation

### Implementation & Unit Testing

**Purpose:** Build and test individual modules.

**Activities:**
1. Implement modules per design
2. Write and execute unit tests
3. Fix defects
4. Prepare for integration

**Entry Criteria:**
- [ ] Module designs approved
- [ ] Development environment ready
- [ ] Unit test plan complete

**Exit Criteria:**
- [ ] All modules implemented
- [ ] Unit tests passing
- [ ] Code review complete
- [ ] Modules ready for integration

**Artifacts:**
- Source code
- Unit test results
- Code review reports

## Right Side: Integration & Validation

### Integration Testing

**Purpose:** Verify component interactions.

**Activities:**
1. Integrate components per architecture
2. Execute integration tests
3. Verify interfaces
4. Fix integration defects

**Entry Criteria:**
- [ ] Modules implemented and unit tested
- [ ] Integration environment ready
- [ ] Integration test plan approved

**Exit Criteria:**
- [ ] All components integrated
- [ ] Integration tests passing
- [ ] Interface compliance verified

**Artifacts:**
- Integration test results
- Interface compliance report
- Updated integration documentation

### System Testing

**Purpose:** Verify complete system meets design specifications.

**Activities:**
1. Deploy complete system
2. Execute system tests
3. Verify all requirements
4. Document system behavior

**Entry Criteria:**
- [ ] Integration complete
- [ ] System deployed in test environment
- [ ] System test plan approved

**Exit Criteria:**
- [ ] System tests passing
- [ ] All requirements verified
- [ ] Performance criteria met

**Artifacts:**
- System test results
- Requirements verification matrix
- System test summary report

### Acceptance Testing

**Purpose:** Validate system meets user needs.

**Activities:**
1. Deploy system in acceptance environment
2. Execute acceptance tests with users
3. Validate business requirements
4. Obtain user sign-off

**Entry Criteria:**
- [ ] System testing complete
- [ ] Acceptance environment ready
- [ ] Users available for testing

**Exit Criteria:**
- [ ] Acceptance tests passing
- [ ] User sign-off obtained
- [ ] Business requirements validated

**Artifacts:**
- Acceptance test results
- User sign-off document
- Acceptance summary report

## Traceability Matrix

Maintain bidirectional traceability:

```
Requirement -> Design -> Code -> Test -> Result
     |           |       |       |        |
     +-----------+-------+-------+--------+
                Traceability Chain
```

### Traceability Requirements

- [ ] Each requirement traced to design element
- [ ] Each design element traced to code
- [ ] Each code module traced to tests
- [ ] Each test traced to requirement
- [ ] Gaps identified and addressed

## Tool Invocation Patterns

### Traceability Management

```python
# Create traceability link
await self.tools.vmodel.create_trace(
    source_id="{{SOURCE_ID}}",
    source_type="{{SOURCE_TYPE}}",
    target_id="{{TARGET_ID}}",
    target_type="{{TARGET_TYPE}}"
)

# Verify traceability completeness
coverage = await self.tools.vmodel.verify_traceability(
    requirement_ids={{REQUIREMENT_LIST}},
    required_links=["{{LINK_TYPES}}"]
)

# Identify gaps
gaps = await self.tools.vmodel.identify_gaps(
    traceability_matrix={{TRACE_MATRIX}}
)
```

### Test Plan Management

```python
# Update test plan from specification
test_plan = await self.tools.vmodel.create_test_plan(
    specification_id="{{SPEC_ID}}",
    test_level="{{TEST_LEVEL}}",
    templates={{TEST_TEMPLATES}}
)

# Link tests to requirements
links = await self.tools.vmodel.link_tests_to_requirements(
    test_ids={{TEST_LIST}},
    requirement_ids={{REQUIREMENT_LIST}}
)
```

### Verification & Validation

```python
# Execute verification
verification = await self.tools.vmodel.verify(
    artifact="{{ARTIFACT_ID}}",
    criteria="{{VERIFICATION_CRITERIA}}",
    method="{{VERIFICATION_METHOD}}"
)

# Execute validation
validation = await self.tools.vmodel.validate(
    system="{{SYSTEM_ID}}",
    user_needs={{USER_NEEDS}},
    acceptance_criteria="{{CRITERIA}}"
)
```

## When to Use

**V-Model is appropriate when:**
- Safety-critical systems
- Regulatory compliance required
- Requirements are stable and well-defined
- Comprehensive testing is mandated
- Traceability is required

**V-Model is NOT appropriate when:**
- Requirements are highly volatile
- Rapid prototyping needed
- Agile delivery expected
- Limited documentation acceptable
- Innovation/exploration required

## Quality Criteria

- [ ] Test plans created in parallel with specifications
- [ ] Traceability is complete and current
- [ ] Verification precedes validation
- [ ] All requirements are tested
- [ ] Defects are traced to source
- [ ] Sign-offs obtained at each level

## Related Components

- [[component-framework/workflows/waterfall-workflow.md]] - Sequential variant
- [[component-framework/checklists/deployment-checklist.md]] - Deployment verification
- [[component-framework/documents/design-doc.md]] - Design specification
