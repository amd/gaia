---
template_id: spiral-workflow
template_type: workflows
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Risk-driven iterative approach with progressive refinement through cycles
schema_version: "1.0"
---

# Spiral Workflow Pattern

## Purpose

This workflow pattern defines a risk-driven iterative approach where each cycle addresses the highest remaining risks while progressively refining the solution. Best suited for large, complex, high-risk projects requiring systematic risk management.

## Workflow Characteristics

- **Flow Direction:** Spiral cycles (increasing refinement)
- **Decision Points:** Risk assessment at each quadrant
- **Documentation:** Risk-focused, evolves with each cycle
- **Change Management:** Risk-based decision making

## Spiral Quadrants

```
                    Cycle N
                +-------------+
                |             |
    Plan Next   |   Quadrant |   Develop &
    Iteration   |   IV       |   Verify
    +-----------|            |-----------+
                |            |
                +------------+
                |            |
    Quadrant I  |            |  Quadrant III
    Determine   |   Center   |  Engineering
    Objectives  |   Start    |  & Testing
    +---------->|            |<----------+
                |            |
                |            |
    +-----------|            |-----------+
    Risk        |   Quadrant |   Customer
    Analysis    |   II       |   Evaluation
    +-----------|            |-----------+
                |             |
                +-------------+
                    Cycle 1
```

## Quadrant Definitions

### Quadrant I: Determine Objectives

**Purpose:** Establish goals and constraints for the current cycle.

**Activities:**
1. Review overall project objectives
2. Define cycle-specific goals
3. Identify success criteria
4. Establish constraints and assumptions

**Key Questions:**
- What are we trying to achieve this cycle?
- What does success look like?
- What constraints must we work within?
- What assumptions are we making?

**Artifacts:**
- Cycle objectives document
- Success criteria definition
- Constraint log

### Quadrant II: Risk Analysis & Resolution

**Purpose:** Identify and address the highest-priority risks.

**Activities:**
1. Identify potential risks
2. Assess probability and impact
3. Prioritize risks
4. Develop risk resolution strategies
5. Execute risk mitigation

**Risk Categories:**
- Technical risks (technology, architecture, integration)
- Schedule risks (dependencies, resource availability)
- Cost risks (budget, resource costs)
- Requirements risks (stability, clarity, feasibility)

**Risk Matrix:**
| Probability | Low Impact | Medium Impact | High Impact |
|-------------|------------|---------------|-------------|
| High        | Monitor    | Mitigate      | Resolve Now |
| Medium      | Monitor    | Monitor       | Mitigate    |
| Low         | Accept     | Monitor       | Monitor     |

**Artifacts:**
- Risk register
- Risk analysis report
- Risk resolution plans
- Mitigation results

### Quadrant III: Development & Verification

**Purpose:** Build and verify the solution for this cycle.

**Activities:**
1. Design solution components
2. Implement solution
3. Test and verify
4. Validate against objectives

**Approach:**
- Select development model appropriate to risks addressed
- Apply verification methods based on risk level
- Document as needed for risk compliance

**Artifacts:**
- Design documentation
- Implementation artifacts
- Test results
- Verification report

### Quadrant IV: Customer Evaluation

**Purpose:** Obtain stakeholder feedback and plan next cycle.

**Activities:**
1. Present cycle results to stakeholders
2. Gather feedback
3. Evaluate against objectives
4. Decide: continue, pivot, or stop
5. Plan next cycle

**Decision Criteria:**
- Have objectives been met?
- Are risks sufficiently reduced?
- Is the solution viable?
- Should we proceed to next cycle?

**Artifacts:**
- Stakeholder feedback
- Cycle evaluation report
- Go/no-go decision
- Next cycle plan

## Cycle Planning

### Cycle 1: Proof of Concept

**Focus:** Highest technical risks
**Duration:** Short (2-4 weeks)
**Outcome:** Risk reduction, feasibility proof

### Cycle 2: Prototype

**Focus:** Architecture and key functionality
**Duration:** Medium (4-8 weeks)
**Outcome:** Working prototype, architecture validation

### Cycle 3: Incremental Development

**Focus:** Core functionality expansion
**Duration:** Medium (6-10 weeks)
**Outcome:** Substantial working system

### Cycle 4: Production Preparation

**Focus:** Completeness, quality, deployment
**Duration:** Based on remaining work
**Outcome:** Production-ready system

## Tool Invocation Patterns

### Risk Register Management

```python
# Identify and record risks
risks = await self.tools.risk.identify(
    cycle_id="{{CYCLE_ID}}",
    categories=["{{RISK_CATEGORIES}}"]
)

# Assess risk priority
assessment = await self.tools.risk.assess(
    risk_id="{{RISK_ID}}",
    probability={{PROBABILITY}},
    impact={{IMPACT}},
    urgency={{URGENCY}}
)

# Track risk resolution
resolution = await self.tools.risk.track_resolution(
    risk_id="{{RISK_ID}}",
    strategy="{{STRATEGY}}",
    status="{{STATUS}}",
    remaining_exposure={{EXPOSURE}}
)
```

### Cycle Management

```python
# Start new spiral cycle
cycle = await self.tools.spiral.start_cycle(
    cycle_number={{CYCLE_NUMBER}},
    objectives={{OBJECTIVES}},
    target_risks={{RISK_LIST}},
    duration="{{DURATION}}"
)

# Complete cycle
completion = await self.tools.spiral.complete_cycle(
    cycle_id="{{CYCLE_ID}}",
    objectives_met={{OBJECTIVES_STATUS}},
    risks_resolved={{RESOLVED_RISKS}},
    deliverables={{DELIVERABLES}}
)

# Plan next cycle
next_cycle = await self.tools.spiral.plan_next_cycle(
    previous_cycle="{{CYCLE_ID}}",
    remaining_risks={{REMAINING_RISKS}},
    stakeholder_feedback={{FEEDBACK}}
)
```

### Stakeholder Evaluation

```python
# Gather stakeholder feedback
feedback = await self.tools.spiral.collect_feedback(
    cycle_id="{{CYCLE_ID}}",
    stakeholders={{STAKEHOLDER_LIST}},
    evaluation_criteria={{CRITERIA}}
)

# Make go/no-go decision
decision = await self.tools.spiral.make_cycle_decision(
    cycle_id="{{CYCLE_ID}}",
    feedback={{FEEDBACK}},
    objectives_status={{STATUS}},
    risk_status={{RISK_STATUS}}
)
```

## When to Use

**Spiral is appropriate when:**
- Project is large and complex
- Significant technical or business risks exist
- Requirements are uncertain but discoverable
- Stakeholders can participate in evaluations
- Risk management is critical

**Spiral is NOT appropriate when:**
- Project is simple and low-risk
- Requirements are fixed and well-understood
- Stakeholders unavailable for cycle reviews
- Time is extremely limited
- Team lacks risk management experience

## Quality Criteria

- [ ] Each cycle addresses highest-priority risks
- [ ] Risk analysis is thorough and documented
- [ ] Stakeholder evaluation occurs each cycle
- [ ] Cycle objectives are clear and measurable
- [ ] Go/no-go decisions are made explicitly
- [ ] Risk exposure decreases over cycles

## Related Components

- [[component-framework/workflows/agile-workflow.md]] - Iterative development variant
- [[component-framework/workflows/waterfall-workflow.md]] - Sequential alternative
- [[component-framework/checklists/domain-analysis-checklist.md]] - Risk identification support
