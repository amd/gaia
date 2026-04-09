---
template_id: memory-template
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for generating memory component files
schema_version: "1.0"
---

# Memory Meta-Template

## Purpose

This meta-template provides the structure for generating memory component files. Memory components define how agents store, retrieve, and update state information during execution, including short-term, long-term, working, and episodic memory.

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{MEMORY_ID}} | Unique memory identifier | Yes | `working-memory` |
| {{MEMORY_NAME}} | Human-readable memory name | Yes | `Working Memory` |
| {{VERSION}} | Memory version (semver) | Yes | `1.0.0` |
| {{MEMORY_TYPE}} | Type of memory | Yes | `short-term`, `long-term`, `working`, `episodic` |
| {{DESCRIPTION}} | Memory purpose | Yes | `Temporary state storage` |
| {{RETENTION_POLICY}} | How long memory persists | Yes | `session`, `persistent` |
| {{CAPACITY_LIMITS}} | Memory capacity constraints | No | See body template |

## Frontmatter Template

```yaml
---
template_id: {{MEMORY_ID}}
template_type: memory
version: {{VERSION}}
created: "{{CREATED_DATE}}"
updated: "{{UPDATED_DATE}}"
maintainer: {{MAINTAINER}}
description: {{DESCRIPTION}}
memory_type: {{MEMORY_TYPE}}
retention_policy: {{RETENTION_POLICY}}
schema_version: "{{SCHEMA_VERSION}}"
---
```

## Memory Body Template

```markdown
# {{MEMORY_NAME}}

## Purpose

[Describe what this memory component stores and when it should be used. Explain the role this memory plays in agent cognition.]

## Memory Identity

| Attribute | Value |
|-----------|-------|
| Memory ID | {{MEMORY_ID}} |
| Type | {{MEMORY_TYPE}} |
| Retention | {{RETENTION_POLICY}} |
| Persistence | [volatile/semi-persistent/persistent] |

## Memory Characteristics

### Capacity

{{CAPACITY_LIMITS}}

| Dimension | Limit | Behavior When Full |
|-----------|-------|-------------------|
| Items | {{MAX_ITEMS}} | [eviction policy] |
| Age | {{MAX_AGE}} | [expiration policy] |
| Size | {{MAX_SIZE}} | [compression policy] |

### Access Patterns

| Pattern | Description | Use Case |
|---------|-------------|----------|
| Read | Retrieve by key/query | State checking |
| Write | Store new information | State updates |
| Update | Modify existing | State refinement |
| Delete | Remove information | Cleanup |

## Data Structure

### Schema

```json
{
  "{{MEMORY_ID}}": {
    "metadata": {
      "created": "timestamp",
      "updated": "timestamp",
      "version": "string"
    },
    "data": {
      "{{FIELD_1}}": "type",
      "{{FIELD_2}}": "type"
    }
  }
}
```

### Fields

| Field | Type | Description | Required |
|-------|------|-------------|----------|
| `{{FIELD_1}}` | string | Description | Yes |
| `{{FIELD_2}}` | dict | Description | No |

## Operations

### Write Operation

**Purpose:** Store new information in memory

**Input:**
```json
{
  "key": "string",
  "value": "any",
  "metadata": {
    "ttl": "optional duration",
    "tags": ["optional", "tags"]
  }
}
```

**Process:**
1. Validate key format
2. Check capacity limits
3. Apply eviction policy if needed
4. Store value with timestamp
5. Return success/failure

**Output:**
```json
{
  "success": boolean,
  "key": "string",
  "evicted": ["list of evicted keys if any"]
}
```

### Read Operation

**Purpose:** Retrieve information from memory

**Input:**
```json
{
  "key": "string" // or
  "query": "string"
}
```

**Process:**
1. Search for key/query match
2. Retrieve value if found
3. Update access timestamp
4. Return value or null

**Output:**
```json
{
  "found": boolean,
  "value": "any",
  "metadata": {...}
}
```

### Update Operation

**Purpose:** Modify existing information

**Input:**
```json
{
  "key": "string",
  "updates": {
    "field": "new_value"
  }
}
```

**Process:**
1. Find existing entry
2. Merge updates with existing
3. Update timestamp
4. Return updated entry

**Output:**
```json
{
  "success": boolean,
  "previous": {...},
  "current": {...}
}
```

### Delete Operation

**Purpose:** Remove information from memory

**Input:**
```json
{
  "key": "string"
}
```

**Process:**
1. Find entry by key
2. Remove from memory
3. Return confirmation

**Output:**
```json
{
  "success": boolean,
  "deleted": "key"
}
```

## Eviction Policies

### Policy 1: {{POLICY_NAME}}

**Description:**
[How this eviction policy works]

**When Applied:**
[Circumstances triggering eviction]

**Algorithm:**
1. Step 1
2. Step 2
3. Step 3

**Example:**
```
Memory full. Evicting: oldest_entry_key
Reason: Not accessed for 10 minutes
```

## Integration with Component Framework

### Components That Read This Memory

- [[component-framework/personas/{{AGENT_NAME}}]] - Agent that reads
- [[component-framework/tasks/{{TASK_NAME}}]] - Task that reads

### Components That Write This Memory

- [[component-framework/personas/{{AGENT_NAME}}]] - Agent that writes
- [[component-framework/workflows/{{WORKFLOW_NAME}}]] - Workflow that writes

### Components That Update This Memory

- [[component-framework/memory/{{RELATED_MEMORY}}]] - Related memory

## Usage Examples

### Example 1: Session State

```
Agent: Writing to working-memory
Key: "current_task"
Value: {"id": "task-123", "status": "in_progress"}

Agent: Reading from working-memory
Query: "current_task"
Result: {"id": "task-123", "status": "in_progress"}
```

### Example 2: Context Tracking

```
Agent: Updating working-memory
Key: "conversation_context"
Updates: {"turn_count": 5, "last_topic": "code_review"}

Agent: Memory state after update
{
  "conversation_context": {
    "turn_count": 5,
    "last_topic": "code_review",
    "updated": "2026-04-07T10:30:00Z"
  }
}
```

## Memory Lifecycle

### Creation

[When and how this memory is created]

**Trigger:** [Event that creates memory]
**Initial State:** [Default values]

### Maintenance

[How memory is maintained during use]

**Periodic Tasks:**
- Task 1
- Task 2

### Cleanup

[When and how memory is cleaned up]

**Trigger:** [Event that triggers cleanup]
**Cleanup Process:**
1. Step 1
2. Step 2

## Related Memories

- [[component-framework/memory/{{RELATED_MEMORY}}]] - Related memory

## Quality Indicators

High-quality memory management demonstrates:
- [ ] Keys are consistent and discoverable
- [ ] Values are properly typed
- [ ] Eviction policies are fair
- [ ] Access patterns are efficient
- [ ] Cleanup is thorough

## References

- [[component-framework/templates/component-template.md]] - Component template
- [[component-framework/memory/short-term-memory.md]] - Short-term memory example
- [[component-framework/memory/working-memory.md]] - Working memory example
```

## Generation Instructions

### Step 1: Define Memory Purpose

Articulate:
1. What information this memory stores
2. Why this memory is needed
3. What type of memory (short-term, long-term, working, episodic)

### Step 2: Specify Memory Characteristics

Define:
- Capacity limits (items, age, size)
- Access patterns (read, write, update, delete)
- Retention policy (session, persistent)

### Step 3: Define Data Structure

Create:
- JSON schema for memory data
- Field definitions with types
- Metadata structure

### Step 4: Implement Operations

Document:
- Write operation with input/output
- Read operation with input/output
- Update operation with input/output
- Delete operation with input/output

### Step 5: Define Eviction Policies

Specify:
- When eviction is triggered
- Which policy to use
- How eviction is performed

### Step 6: Validate Generated Memory

```python
# Load and validate the generated memory component
loader = ComponentLoader()
memory = loader.load_component(f"memory/{{MEMORY_ID}}.md")
errors = loader.validate_component(f"memory/{{MEMORY_ID}}.md")
if errors:
    print(f"Validation errors: {errors}")

# Verify memory structure
assert memory['frontmatter']['memory_type'] == '{{MEMORY_TYPE}}'
assert memory['content'] != ""
```

## Quality Checklist

- [ ] Frontmatter has all required fields
- [ ] `template_type` is set to `memory`
- [ ] `memory_type` is valid
- [ ] Data structure is well-defined
- [ ] Operations are complete
- [ ] Eviction policies are specified
- [ ] Integration is documented
- [ ] Examples demonstrate usage
- [ ] Related memories are correctly linked

## Related Components

- [[component-framework/templates/component-template.md]] - Component generation template
- [[component-framework/memory/short-term-memory.md]] - Short-term memory example
- [[component-framework/memory/working-memory.md]] - Working memory example
- [[component-framework/memory/long-term-memory.md]] - Long-term memory example
