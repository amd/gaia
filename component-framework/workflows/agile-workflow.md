---
template_id: agile-workflow
template_type: workflows
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Iterative development workflow with incremental delivery and continuous feedback
schema_version: "1.0"
---

# Agile Workflow Pattern

## Purpose

This workflow pattern defines an iterative, incremental approach where work is delivered in short cycles with continuous stakeholder feedback. Best suited for projects with evolving requirements and need for rapid value delivery.

## Workflow Characteristics

- **Flow Direction:** Iterative cycles (sprints/iterations)
- **Feedback Loops:** Continuous stakeholder engagement
- **Documentation:** Just-enough, working software over documentation
- **Change Management:** Embrace change, adapt each iteration

## Iteration Structure

```
Product Backlog
      |
      v
+--------------------------+
|   Sprint Planning        |
|   - Select backlog items |
|   - Define sprint goal   |
|   - Create sprint backlog|
+--------------------------+
      |
      v
+--------------------------+
|   Sprint Execution       |
|   - Daily standups       |
|   - Continuous development|
|   - Ongoing testing      |
|   (Time-boxed: 1-4 weeks)|
+--------------------------+
      |
      v
+--------------------------+
|   Sprint Review          |
|   - Demo increment       |
|   - Gather feedback      |
|   - Update backlog       |
+--------------------------+
      |
      v
+--------------------------+
|   Sprint Retrospective   |
|   - Reflect on process   |
|   - Identify improvements|
|   - Plan adaptations     |
+--------------------------+
      |
      v
  Next Iteration -->
```

## Role Definitions

### Product Owner

**Responsibilities:**
- Maintain product backlog
- Prioritize work items
- Define acceptance criteria
- Accept or reject completed work

### Scrum Master / Team Coach

**Responsibilities:**
- Remove impediments
- Facilitate ceremonies
- Coach team on agile practices
- Protect team from interruptions

### Development Team

**Responsibilities:**
- Self-organize work
- Deliver working increments
- Ensure quality standards
- Cross-functional collaboration

## Artifact Definitions

### Product Backlog

Ordered list of all desired work:
- User stories
- Bug fixes
- Technical debt items
- Enablers and research

**Characteristics:**
- DEEP: Detailed appropriately, Emergent, Estimated, Prioritized

### Sprint Backlog

Selected work for current iteration:
- Stories committed for sprint
- Task breakdown
- Hour/point estimates
- Ownership assignments

### Increment

Working product delivered:
- Potentially shippable
- Meets Definition of Done
- Demonstrable to stakeholders

## Ceremony Patterns

### Sprint Planning

**Duration:** 2-4 hours per week of sprint (e.g., 4 hours for 2-week sprint)

**Agenda:**
1. Product Owner presents prioritized backlog
2. Team asks clarifying questions
3. Team selects items based on capacity
4. Team breaks stories into tasks
5. Team commits to sprint goal

**Output:**
- Sprint goal statement
- Sprint backlog with tasks
- Capacity allocation

### Daily Standup

**Duration:** 15 minutes (strictly time-boxed)

**Agenda:**
1. What did I complete yesterday?
2. What will I work on today?
3. What impediments do I have?

**Output:**
- Shared awareness
- Impediment list
- Daily coordination

### Sprint Review

**Duration:** 1-2 hours per week of sprint

**Agenda:**
1. Demo completed stories
2. Gather stakeholder feedback
3. Discuss market changes
4. Update backlog based on feedback

**Output:**
- Stakeholder feedback
- Updated product backlog
- Release adjustments

### Sprint Retrospective

**Duration:** 1-2 hours per week of sprint

**Agenda:**
1. What went well?
2. What could be improved?
3. What will we commit to change?

**Output:**
- Improvement backlog items
- Team working agreements updates
- Process adaptations

## Definition of Done

Shared understanding of completeness:

**Typical Criteria:**
- [ ] Code complete
- [ ] Unit tests written and passing
- [ ] Integration tests passing
- [ ] Code review completed
- [ ] Acceptance criteria verified
- [ ] Documentation updated
- [ ] No critical defects
- [ ] Deployed to staging

## Tool Invocation Patterns

### Backlog Management

```python
# Get prioritized backlog
backlog = await self.tools.agile.get_backlog(
    product_id="{{PRODUCT_ID}}",
    limit={{ITEM_COUNT}},
    min_priority={{MIN_PRIORITY}}
)

# Update story status
await self.tools.agile.update_story(
    story_id="{{STORY_ID}}",
    status="{{STATUS}}",
    acceptance_status="{{ACCEPTED|REJECTED}}"
)
```

### Sprint Management

```python
# Start new sprint
sprint = await self.tools.agile.start_sprint(
    sprint_goal="{{SPRINT_GOAL}}",
    committed_stories={{STORY_LIST}},
    start_date="{{START_DATE}}",
    end_date="{{END_DATE}}"
)

# Record daily progress
await self.tools.agile.record_standup(
    sprint_id="{{SPRINT_ID}}",
    completed={{COMPLETED_ITEMS}},
    planned={{PLANNED_ITEMS}},
    impediments={{IMPEDEMENT_LIST}}
)

# Complete sprint
await self.tools.agile.complete_sprint(
    sprint_id="{{SPRINT_ID}}",
    completed_stories={{COMPLETED_STORIES}},
    incomplete_stories={{INCOMPLETE_STORIES}}
)
```

### Velocity Tracking

```python
# Calculate team velocity
velocity = await self.tools.agile.calculate_velocity(
    team_id="{{TEAM_ID}}",
    sprint_count={{NUMBER_OF_SPRINTS}}
)

# Forecast completion
forecast = await self.tools.agile.forecast_completion(
    remaining_work={{REMAINING_POINTS}},
    velocity={{AVERAGE_VELOCITY}}
)
```

## When to Use

**Agile is appropriate when:**
- Requirements are expected to evolve
- Rapid value delivery is needed
- Stakeholder feedback is available
- Team is co-located or well-connected
- Product discovery is needed

**Agile may not be suitable when:**
- Requirements are fixed by contract
- Stakeholders unavailable for feedback
- Team lacks agile experience without coaching
- Regulatory environment requires extensive documentation
- Dependencies on external teams are high

## Quality Criteria

- [ ] Working increment delivered each sprint
- [ ] Definition of Done is clear and followed
- [ ] All ceremonies are conducted effectively
- [ ] Backlog is properly prioritized and refined
- [ ] Team velocity is tracked and used for planning
- [ ] Retrospective improvements are implemented

## Related Components

- [[component-framework/workflows/spiral-workflow.md]] - Risk-driven iteration variant
- [[component-framework/tasks/task-priority.md]] - Backlog prioritization
- [[component-framework/tasks/task-tracking.md]] - Sprint tracking
