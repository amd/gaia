---
template_id: command-template
template_type: templates
version: 1.0.0
created: "2026-04-07"
maintainer: pipeline-engine
description: Meta-template for generating command definition files
schema_version: "1.0"
---

# Command Meta-Template

## Purpose

This meta-template provides the structure for generating command definition files. Commands define executable operations that agents can invoke, including shell commands, build operations, and tool invocations.

## Template Variables

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| {{COMMAND_ID}} | Unique command identifier | Yes | `build-command` |
| {{COMMAND_NAME}} | Human-readable command name | Yes | `Build Command` |
| {{VERSION}} | Command version (semver) | Yes | `1.0.0` |
| {{COMMAND_TYPE}} | Type of command | Yes | `shell`, `build`, `test` |
| {{DESCRIPTION}} | Command purpose | Yes | `Executes build process` |
| {{COMMAND_SYNTAX}} | Command syntax pattern | Yes | `npm run build` |
| {{PARAMETERS}} | Command parameters | Yes | See body template |
| {{EXECUTION_STEPS}} | Step-by-step execution | Yes | See body template |

## Frontmatter Template

```yaml
---
template_id: {{COMMAND_ID}}
template_type: commands
version: {{VERSION}}
created: "{{CREATED_DATE}}"
updated: "{{UPDATED_DATE}}"
maintainer: {{MAINTAINER}}
description: {{DESCRIPTION}}
command_type: {{COMMAND_TYPE}}
schema_version: "{{SCHEMA_VERSION}}"
---
```

## Command Body Template

```markdown
# {{COMMAND_NAME}}

## Purpose

[Describe what this command does and when it should be used. Explain the command's role in the development workflow.]

## Command Identity

| Attribute | Value |
|-----------|-------|
| Command ID | {{COMMAND_ID}} |
| Type | {{COMMAND_TYPE}} |
| Syntax | `{{COMMAND_SYNTAX}}` |
| Platform | [cross-platform/Windows/Linux/macOS] |

## Command Syntax

```bash
{{COMMAND_SYNTAX}} [options] [arguments]
```

### Options

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--output` | `-o` | Output directory | `./dist` |
| `--verbose` | `-v` | Enable verbose output | `false` |
| `--watch` | `-w` | Watch mode | `false` |

### Arguments

| Argument | Required | Description | Example |
|----------|----------|-------------|---------|
| `target` | Yes | Build target | `app`, `lib` |
| `config` | No | Configuration file | `config.json` |

## Parameters

{{PARAMETERS}}

| Parameter | Type | Required | Description | Validation |
|-----------|------|----------|-------------|------------|
| `--param1` | string | Yes | Description | Min length 1 |
| `--param2` | int | No | Description | Range: 1-100 |

## Execution Steps

{{EXECUTION_STEPS}}

### Step 1: {{STEP_1_NAME}}

**Purpose:** [Why this step exists]

**Actions:**
1. Action 1
2. Action 2

**Expected Output:**
```
{{STEP_1_OUTPUT}}
```

### Step 2: {{STEP_2_NAME}}

**Purpose:** [Why this step exists]

**Actions:**
1. Action 1
2. Action 2

**Expected Output:**
```
{{STEP_2_OUTPUT}}
```

## Preconditions

Before executing this command, ensure:
- [ ] precondition 1
- [ ] precondition 2
- [ ] precondition 3

## Output Format

### Standard Output

[Describe what the command outputs on success]

```
{{SUCCESS_OUTPUT}}
```

### Error Output

[Describe error output format]

```
{{ERROR_OUTPUT}}
```

## Error Handling

### Common Errors

| Error Code | Message | Cause | Resolution |
|------------|---------|-------|------------|
| E001 | File not found | Missing input file | Verify file exists |
| E002 | Permission denied | Insufficient permissions | Run as admin |
| E003 | Invalid configuration | Malformed config | Check config syntax |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
| 3 | Configuration error |

## Examples

### Example 1: Basic Usage

```bash
{{EXAMPLE_COMMAND_1}}
```

**Expected Output:**
```
{{EXAMPLE_OUTPUT_1}}
```

### Example 2: Advanced Usage

```bash
{{EXAMPLE_COMMAND_2}}
```

**Expected Output:**
```
{{EXAMPLE_OUTPUT_2}}
```

### Example 3: Error Case

```bash
{{EXAMPLE_COMMAND_3}}
```

**Expected Error:**
```
{{EXAMPLE_ERROR_3}}
```

## Integration with Component Framework

### Components Created

This command may create:
- `documents/build-log.md` - Build execution log

### Components Updated

This command may update:
- `tasks/task-tracking.md` - Task status
- `memory/working-memory.md` - Execution state

## Related Commands

- `[[component-framework/commands/{{RELATED_COMMAND}}]]` - Related command

## Quality Checklist

- [ ] Command syntax is correct and complete
- [ ] All parameters are documented with types
- [ ] Execution steps are clear and sequential
- [ ] Error handling covers common cases
- [ ] Examples demonstrate typical usage
- [ ] Component integration is documented

## References

- [[component-framework/templates/component-template.md]] - Component template
- [[component-framework/commands/shell-commands.md]] - Shell commands reference
```

## Generation Instructions

### Step 1: Define Command Purpose

Articulate:
1. What operation the command performs
2. When it should be invoked
3. What type of command (shell, build, test, etc.)

### Step 2: Specify Syntax

Define:
- Base command syntax
- Options with defaults
- Required and optional arguments

### Step 3: Document Parameters

For each parameter:
- Specify type (string, int, boolean, etc.)
- Indicate if required
- Provide clear description
- Add validation rules if applicable

### Step 4: Write Execution Steps

Document:
- Each step in order
- Purpose of each step
- Expected output per step

### Step 5: Validate Generated Command

```python
# Load and validate the generated command
loader = ComponentLoader()
command = loader.load_component(f"commands/{{COMMAND_ID}}.md")
errors = loader.validate_component(f"commands/{{COMMAND_ID}}.md")
if errors:
    print(f"Validation errors: {errors}")

# Verify command structure
assert command['frontmatter']['command_type'] == '{{COMMAND_TYPE}}'
assert command['content'] != ""
```

## Quality Checklist

- [ ] Frontmatter has all required fields
- [ ] `template_type` is set to `commands`
- [ ] `command_type` is valid
- [ ] Command syntax is executable
- [ ] Parameters are fully documented
- [ ] Execution steps are complete
- [ ] Error handling is comprehensive
- [ ] Examples are valid and tested

## Related Components

- [[component-framework/templates/component-template.md]] - Component generation template
- [[component-framework/commands/shell-commands.md]] - Shell commands reference
- [[component-framework/commands/build-commands.md]] - Build commands reference
- [[component-framework/templates/task-template.md]] - Task template for command workflows
