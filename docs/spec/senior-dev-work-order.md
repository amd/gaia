---
title: Senior Developer Work Order — Feature/Pipeline-Orchestration-v1
issued_by: software-program-manager
assigned_to: senior-developer
date: 2026-04-07
branch: feature/pipeline-orchestration-v1
priority: P0
milestone: Milestone 1 — Foundation
---

# Senior Developer Work Order

**Issued by:** software-program-manager (pipeline iteration 1)
**Assigned to:** senior-developer
**Branch:** `feature/pipeline-orchestration-v1`
**Date:** 2026-04-07

This work order is the complete, unambiguous instruction set for Milestone 1 of the
feature/pipeline-orchestration-v1 program. Every file path, required content, and
constraint is specified here. When a deliverable references content from the action
plan (`docs/spec/agent-ecosystem-action-plan.md`), that reference is exhaustive — do
not supplement or modify the referenced content.

Read this entire work order before starting any task. The tasks are ordered by
priority; if iteration budget is exhausted, stop at the end of the current priority
tier rather than partially completing the next tier.

---

## Pre-work: Files to Read Before Starting

Before writing a single line of code or documentation, read the following files in
full. Understanding their complete content is a prerequisite for every task below.

1. `C:\Users\amikinka\gaia\docs\spec\agent-ecosystem-design-spec.md` — full file.
   Pay particular attention to:
   - Section 3.2 (frontmatter field reference, all fields)
   - Section 4 (tool-call block syntax — Sections 4.1 through 4.5)
   - Section 5.7 (`_load_md_agent()` implementation spec)
   - Section 8 (Open Questions — you will be closing four of them)

2. `C:\Users\amikinka\gaia\docs\spec\agent-ecosystem-action-plan.md` — full file.
   This contains the complete content for every deliverable. Do not work from memory.

3. `C:\Users\amikinka\gaia\src\gaia\agents\registry.py` — full file.
   Understand the existing `_load_agent()` method completely before adding any code.

4. `C:\Users\amikinka\gaia\src\gaia\agents\base\context.py` — full file.
   Understand `AgentDefinition`, `AgentTriggers`, `AgentCapabilities`,
   `AgentConstraints` dataclass fields exactly as they are defined.

---

## Priority Sequence

If iteration budget requires scope reduction, stop at the end of each tier cleanly.
Do not start a task you cannot complete.

- **P0 — Must complete:** Tasks 1, 2, 3, 4, 5
- **P1 — Complete if possible after P0:** Task 6 (unit tests)
- **P2 — Defer to next iteration if needed:** Task 7 (template library)

---

## Task 1 — Apply 8 Spec Edits to the Design Spec (P0)

**File:** `C:\Users\amikinka\gaia\docs\spec\agent-ecosystem-design-spec.md`

**Read the file first. Then apply each edit as a targeted replacement using Edit tool.
Read the file back after all 8 edits to verify correctness before proceeding.**

Do not restructure sections, add new prose, or alter any text not explicitly specified
in the edit below. These are surgical replacements.

---

### Edit 1.1 — Section 3.2, complexity_range field (Tension 1)

**Locate** the block in Section 3.2 that reads approximately:

```yaml
  complexity_range:                   # Complexity score range [0.0, 1.0] within which
    min: 0.3                          # this agent is eligible. Maps to
    max: 1.0                          # AgentTriggers.complexity_range.
```

**Replace** the entire three-line `complexity_range:` block (with its inline comments)
with:

```yaml
  complexity_range: [0.3, 1.0]        # Complexity score range [min, max] within [0.0, 1.0].
                                      # YAML sequence maps directly to AgentTriggers.
                                      # complexity_range Tuple[float, float]. Do NOT use
                                      # dict format {min: X, max: Y} — that form requires
                                      # a brittle .values() call and is legacy-only.
```

---

### Edit 1.2 — Section 3.4, normative senior-developer.md example (Tension 1)

**Locate** the frontmatter block inside the Section 3.4 normative example. Find the
lines that read:

```yaml
  complexity_range:
    min: 0.3
    max: 1.0
```

**Replace** those three lines with:

```yaml
  complexity_range: [0.3, 1.0]
```

---

### Edit 1.3 — Section 3.2, capabilities field comment (Tension 4)

**Locate** the inline comment on the `capabilities:` field in Section 3.2 that reads:

```
Must use vocabulary defined in src/gaia/core/capabilities.py
```

(It may appear slightly differently — find the sentence that claims capabilities are
validated against capabilities.py.)

**Replace** that specific comment text with:

```
Should use controlled vocabulary. Formal VALID_CAPABILITY_STRINGS constant is Phase 2
work; see action-plan.md Tension 4 resolution.
```

---

### Edit 1.4 — Section 4.4, add Scope Boundary subsection (Tension 2)

**Locate** the end of Section 4.4. Find the "Condition syntax rules" bullet list. The
scope boundary subsection must be inserted immediately after that bullet list (before
whatever follows Section 4.4 — likely Section 4.5 or a horizontal rule).

**Add the following text** at the end of Section 4.4:

```markdown
#### Scope Boundary — Phase 1 vs Phase 2

In Phase 1, `IF:` conditions are LLM-evaluated. The LLM reads the condition as a
natural-language predicate against named variables in its conversational context and
decides whether to execute the enclosed `CALL` block. No parsing, no grammar
validation, no pipeline engine evaluation.

Machine-parseable condition evaluation is explicitly out of scope for this
specification. It is Phase 2 work, gated on the pipeline engine design. The Phase 2
formal grammar must be a separate specification document.

**Do not implement condition parsing in `registry.py` or `_load_md_agent()`.**
```

---

### Edit 1.5 — Section 5.7, _load_md_agent() code fix (Tension 3)

**Locate** the `_load_md_agent()` code block in Section 5.7. Find the line that reads:

```python
    with open(md_file, "r", encoding="utf-8") as f:
```

**Replace** that one line with:

```python
    with open(md_file, "r", encoding="utf-8-sig") as f:  # utf-8-sig strips UTF-8 BOM
```

Then find the line immediately after `content = f.read()` in the same code block and
**insert** after it:

```python
    # Normalize Windows CRLF and bare CR to LF before any string operations
    content = content.replace('\r\n', '\n').replace('\r', '\n')
```

---

### Edit 1.6 — Section 6.2, add two unit tests (Tension 3)

**Locate** the unit test list in Section 6.2 (the list of required tests for
`_load_md_agent()`).

**Add** the following two test names to the list:

- `test_load_md_agent_crlf_line_endings` — verifies a CRLF-encoded `.md` file loads
  correctly (no `AgentLoadError`, correct frontmatter parsed, correct system prompt
  extracted)
- `test_load_md_agent_bom_prefix` — verifies a UTF-8 BOM-prefixed `.md` file loads
  correctly (BOM does not cause the `startswith("---")` check to fail)

---

### Edit 1.7 — Section 6.1 Step 4, capabilities validation language (Tension 4)

**Locate** Step 4 in Section 6.1. Find the text that reads:

```
Standardize capabilities: values against src/gaia/core/capabilities.py vocabulary
```

**Replace** that text with:

```
Align capabilities: values with the vocabulary defined in the existing 18 YAML agent
files; formal VALID_CAPABILITY_STRINGS enforcement is Phase 2 work.
```

---

### Edit 1.8 — Section 8, close four Open Questions

**Locate** Section 8 (Open Questions). Apply the following four targeted replacements.
Each replacement closes one question by appending a resolution line. Locate the exact
question text and append the resolution immediately after it, before the next question
or section boundary.

**Q1 (BOM question):** Find the Q1 block. After the line `**Owner:** Engineering
(registry implementation)`, add:

```
**Resolution:** RESOLVED — Use `utf-8-sig` universally. This is the correct
cross-platform choice for Windows-authored files. See action-plan.md Tension 3.
```

**Q7.1 (complexity_range format):** Find the sub-item under Q7 that discusses
`triggers.complexity_range` and the dict vs tuple inconsistency. After the description,
add:

```
**Resolution:** RESOLVED — List format `[0.3, 1.0]` standardized. `_build_agent_definition()`
handles legacy dict format for backward compatibility. See action-plan.md Tension 1.
```

**Q7.2 (IF: conditions):** Find the sub-item under Q7 that discusses whether IF:
conditions are machine-parseable. After the description, add:

```
**Resolution:** DEFERRED to Phase 2. Conditions are LLM-evaluated in Phase 1. A
formal grammar is a separate specification document. Do not implement condition
parsing in the registry. See action-plan.md Tension 2.
```

**Q7.4 (capabilities.py vocabulary):** Find the sub-item under Q7 that discusses
capability vocabulary enforcement and the capabilities.py file. After the description,
add:

```
**Resolution:** PARTIALLY RESOLVED — File exists but does not contain a vocabulary
list. `VALID_CAPABILITY_STRINGS` is Phase 2 work. Validation step in
`_build_agent_definition()` must be skipped until that constant exists. See
action-plan.md Tension 4.
```

---

## Task 2 — Implement _build_agent_definition() Helper in registry.py (P0)

**File:** `C:\Users\amikinka\gaia\src\gaia\agents\registry.py`

**Read the full file before making any changes.**

### What to add

Add a new private method `_build_agent_definition()` to the `AgentRegistry` class.
This method must contain all field-parsing logic that is currently duplicated inside
`_load_agent()` (lines approximately 186-258).

### Exact implementation requirements

The method signature must be:

```python
def _build_agent_definition(
    self,
    data: dict,
    system_prompt_override: str = None
) -> AgentDefinition:
```

The method body must implement the following logic in this exact order:

1. **Top-level key handling.** Support both nested format (`data["agent"]` key) and
   flat format (fields at top level). Use `agent_data = data.get("agent", data)` to
   handle both.

2. **Triggers parsing.** Extract `triggers_data = agent_data.get("triggers", {})`.
   Then parse `complexity_range` with the dual-format handler:

```python
raw = triggers_data.get("complexity_range", [0.0, 1.0])
if isinstance(raw, dict):
    # Legacy dict format from existing .yaml files — use explicit key lookup,
    # not .values(), to avoid order dependency.
    complexity_range = (
        float(raw.get("min", 0.0)),
        float(raw.get("max", 1.0))
    )
elif isinstance(raw, (list, tuple)) and len(raw) == 2:
    complexity_range = (float(raw[0]), float(raw[1]))
else:
    complexity_range = (0.0, 1.0)
```

3. **system_prompt resolution.** Apply the override if provided; otherwise read from
   `agent_data.get("system_prompt", "")`:

```python
if system_prompt_override is not None:
    system_prompt = system_prompt_override
else:
    system_prompt = agent_data.get("system_prompt", "")
```

4. **All other field parsing** must be identical to the existing logic in `_load_agent()`
   for: `id`, `name`, `version`, `category`, `description`, `model_id`, `enabled`,
   `capabilities`, `tools`, `execution_targets`, `constraints`, `metadata`,
   `conversation_starters`, `color`.

5. **Return** an `AgentDefinition(...)` instance with all parsed fields.

### What NOT to do in Task 2

- Do NOT modify the existing `_load_agent()` method's external behavior.
- Do NOT rename any existing methods.
- Do NOT change the return types of any existing methods.
- After extracting `_build_agent_definition()`, update `_load_agent()` to call
  `_build_agent_definition(data)` instead of repeating the parsing logic inline.
  This refactor must preserve identical behavior for all existing YAML files.

---

## Task 3 — Implement _load_md_agent() in registry.py (P0)

**File:** `C:\Users\amikinka\gaia\src\gaia\agents\registry.py`

Add the following new async method to the `AgentRegistry` class, immediately after
`_load_agent()`. Do not modify `_load_agent()` beyond what Task 2 requires.

### Method signature

```python
async def _load_md_agent(self, md_file: Path) -> AgentDefinition:
```

### Exact implementation requirements

Implement the body as follows. Follow this pseudocode precisely — deviations from
the split logic will cause failures on standard `.md` files.

```python
async def _load_md_agent(self, md_file: Path) -> AgentDefinition:
    """Load an agent definition from a Markdown file with YAML frontmatter.

    The file format is:
        ---
        <YAML frontmatter>
        ---

        <System prompt body (plain Markdown)>

    Handles Windows CRLF line endings and UTF-8 BOM transparently.
    """
    # utf-8-sig strips the UTF-8 BOM (0xEF 0xBB 0xBF) that Windows tools emit.
    with open(md_file, "r", encoding="utf-8-sig") as f:
        content = f.read()

    # Normalize Windows CRLF and bare CR to LF before any string operations.
    content = content.replace('\r\n', '\n').replace('\r', '\n')

    if not content.startswith("---"):
        raise AgentLoadError(
            f"{md_file.name}: does not begin with YAML frontmatter block (---)"
        )

    # Strip the opening "---\n" before searching for the closing delimiter.
    # This avoids matching the opening --- as both the start and end delimiter.
    if content.startswith("---\n"):
        inner = content[4:]  # remove "---\n"
    else:
        # "---" with no trailing newline at position 0 — degenerate case
        inner = content[3:]

    first_close = inner.find("\n---\n")
    if first_close == -1:
        # Also check for "---" at end of file without trailing newline
        if inner.endswith("\n---"):
            first_close = len(inner) - 4
            frontmatter_text = inner[:first_close]
            system_prompt = ""
        else:
            raise AgentLoadError(
                f"{md_file.name}: no closing --- delimiter for frontmatter block"
            )
    else:
        frontmatter_text = inner[:first_close]
        # Skip the "\n---\n" delimiter (5 characters) to get the body
        system_prompt = inner[first_close + 5:].strip()

    try:
        data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise AgentLoadError(
            f"{md_file.name}: invalid YAML in frontmatter block: {exc}"
        )

    if not data or not isinstance(data, dict):
        raise AgentLoadError(
            f"{md_file.name}: frontmatter block is empty or not a YAML mapping"
        )

    return self._build_agent_definition(
        data=data,
        system_prompt_override=system_prompt if system_prompt else None
    )
```

### What NOT to do in Task 3

- Do NOT implement condition parsing for `IF:` blocks. The `_load_md_agent()` method
  reads the system prompt body as a plain string and stores it in `AgentDefinition.
  system_prompt`. It does not parse, validate, or execute tool-call blocks.
  (See design spec Section 4.4 Scope Boundary.)
- Do NOT add a `yaml` import if one already exists in registry.py — check first.
- Do NOT use `content.split("\n---\n", 2)` naively without first stripping the opening
  `---\n`. That naive split produces `parts[0] = ""` for a file starting with `---\n`,
  meaning `parts[1]` is the frontmatter and `parts[2]` may not exist if there is no
  body. The explicit `find()` approach above handles all edge cases correctly.

---

## Task 4 — Extend _load_all_agents() and Add Collision Guard (P0)

**File:** `C:\Users\amikinka\gaia\src\gaia\agents\registry.py`

### Extension to _load_all_agents()

Locate the `_load_all_agents()` method. Find the lines that glob for agent files (they
currently glob `*.yaml` and `*.yml`). Add `.md` globbing and dispatch to the correct
loader based on file extension:

```python
agent_files = list(self._agents_dir.glob("*.yaml"))
agent_files.extend(self._agents_dir.glob("*.yml"))
agent_files.extend(self._agents_dir.glob("*.md"))  # NEW

for agent_file in agent_files:
    loader = (
        self._load_md_agent
        if agent_file.suffix == ".md"
        else self._load_agent
    )
    try:
        agent = await loader(agent_file)
    except Exception as e:
        logger.error(f"Failed to load agent from {agent_file}: {e}")
        continue

    async with self._lock:
        # Collision guard: if an agent with this ID was already loaded from
        # a different file (e.g., both .yaml and .md exist), log a warning
        # and keep the first-loaded version. YAML files are globbed first,
        # so .yaml wins during the migration period.
        if agent.id in self._agents:
            existing_source = getattr(
                self._agents[agent.id], '_source_file', '<unknown>'
            )
            logger.warning(
                f"Agent ID collision: '{agent.id}' already loaded from "
                f"'{existing_source}'. Skipping '{agent_file.name}'. "
                f"Remove one of the files to resolve this conflict."
            )
            continue
        self._agents[agent.id] = agent
```

Note: The collision guard logs a warning and skips the second file. It does not raise
an exception, because raising would prevent all subsequent agents from loading if any
one agent has a collision. Logging preserves operator visibility without breaking the
load cycle.

### What NOT to do in Task 4

- Do NOT remove the existing `*.yaml` and `*.yml` glob lines. Both must remain.
- Do NOT change the lock acquisition pattern used elsewhere in `_load_all_agents()`.
- Do NOT add watchdog/hot-reload changes in this task. Hot-reload extension for `.md`
  files is deferred to Milestone 2.

---

## Task 5 — Write config/agents/senior-developer.md (P0)

**File:** `C:\Users\amikinka\gaia\config\agents\senior-developer.md`

**Write this file with the exact content from Deliverable 4 of the action plan
(`docs/spec/agent-ecosystem-action-plan.md`, lines 675-914).**

Do not make creative modifications to the content. If you identify a genuine error in
the Deliverable 4 content (e.g., a frontmatter field name that does not exist in
`AgentDefinition`), report it as a scope change note in your completion report rather
than silently fixing it.

The file must:
- Begin with `---` on the very first line (no blank line before it)
- Have `complexity_range: [0.3, 1.0]` (list format, not dict format)
- Have a non-empty system prompt body after the closing `---`
- Contain at least the 5 tool-call blocks shown in Deliverable 4 (two in Phase 1,
  one in Phase 2 with an MCP call, one IF conditional in Phase 3, two in Phase 4)
- End with the "Constraints and Safety" section

After writing the file, verify it loads correctly by mentally tracing the
`_load_md_agent()` logic: the `---` split must produce a valid frontmatter block and
a non-empty system prompt string.

**IMPORTANT — tool-call block syntax verification:**
Before writing the file, re-read Section 4 of the design spec. Then verify that every
tool-call block in Deliverable 4 conforms to the Section 4 syntax:
- Fenced with ` ```tool-call ` and ` ``` `
- `CALL:` is the first key
- `purpose:` is present on every CALL block
- `capture:` is present only when a variable name is needed
- `IF:` appears on its own line at the start of the block, `END IF:` appears on its
  own line at the end
- MCP tool names follow the `mcp__server-name__tool-name` pattern

If a tool-call block in Deliverable 4 does not conform, note the discrepancy in your
completion report — do not silently "fix" it, as the action plan is the authoritative
source and any discrepancy must be tracked.

---

## Task 6 — Write Unit Tests for _load_md_agent() (P1)

**File:** Create a new file at `C:\Users\amikinka\gaia\tests\unit\test_load_md_agent.py`

Write the following 10 test functions. Each test must be self-contained (no dependency
on external files — use `tmp_path` pytest fixture to create `.md` files inline).

### Test list

1. **`test_load_md_agent_minimal_frontmatter`**
   Write a minimal valid `.md` file with only the required 8 fields. Assert that
   `_load_md_agent()` returns an `AgentDefinition` with correct `id`, `name`, and
   non-None `triggers`.

2. **`test_load_md_agent_full_frontmatter`**
   Write a complete `.md` file with all optional and required fields. Assert that
   `_load_md_agent()` returns an `AgentDefinition` with every field populated
   (including `model_id`, `color`, `metadata`).

3. **`test_load_md_agent_no_frontmatter`**
   Write a `.md` file that begins with prose (not `---`). Assert that `_load_md_agent()`
   raises `AgentLoadError`.

4. **`test_load_md_agent_missing_required_field`**
   Write a `.md` file missing the `id` field. Assert that `_load_md_agent()` raises
   `AgentLoadError` (or that the `AgentDefinition` has a None or empty `id` — match
   whatever behavior `_build_agent_definition()` implements for missing required fields).

5. **`test_load_md_agent_body_preserves_special_characters`**
   Write a `.md` file whose system prompt body contains: a Markdown horizontal rule
   (`---`) inside the body text, Unicode characters (at minimum one emoji or
   non-ASCII character), and a fenced code block with backticks. Assert that the
   `system_prompt` field in the returned `AgentDefinition` contains all of these
   characters intact and that the horizontal rule inside the body did NOT cause an
   incorrect frontmatter split.

6. **`test_load_md_agent_crlf_line_endings`** [NEW — Tension 3]
   Write a `.md` file content string using `\r\n` as the line separator throughout
   (including the frontmatter delimiters). Write it to a temp file in binary mode
   (`open(path, "wb")`) to prevent Python from converting line endings. Assert that
   `_load_md_agent()` loads it successfully with no `AgentLoadError`.

7. **`test_load_md_agent_bom_prefix`** [NEW — Tension 3]
   Write a `.md` file prefixed with the UTF-8 BOM bytes (`b'\xef\xbb\xbf'`) followed
   by a valid frontmatter-and-body structure. Write it to a temp file in binary mode.
   Assert that `_load_md_agent()` loads it successfully (the BOM does not cause the
   `startswith("---")` check to fail).

8. **`test_load_md_agent_complexity_range_list`** [NEW — Tension 1]
   Write a `.md` file with `complexity_range: [0.3, 1.0]` (list format). Assert that
   the returned `AgentDefinition.triggers.complexity_range` equals the Python tuple
   `(0.3, 1.0)`.

9. **`test_load_md_agent_complexity_range_dict`** [NEW — Tension 1, backward compat]
   Write a `.md` file with:
   ```yaml
   complexity_range:
     min: 0.2
     max: 0.8
   ```
   Assert that the returned `AgentDefinition.triggers.complexity_range` equals the
   Python tuple `(0.2, 0.8)`. This verifies that the legacy dict format is still
   handled correctly even in `.md` files (backward compatibility for any `.md` files
   that might use dict format during migration).

10. **`test_registry_discovers_both_yaml_and_md`**
    Create a test that sets up a temp directory with one `*.yaml` agent file and one
    `*.md` agent file (with different agent IDs). Call `_load_all_agents()` against
    that directory. Assert that both agents are present in the registry and that both
    were loaded without error.

### What NOT to do in Task 6

- Do NOT write tests that require a live GAIA server, LLM, or network connection.
- Do NOT hardcode file paths. Use `tmp_path` for all temporary files.
- Do NOT import modules from outside `src/gaia/` and `tests/` directories.
- Do NOT write a test for the collision guard in this task (that test belongs in a
  separate integration test file). Document it as a known gap in your completion
  report.

---

## Task 7 — Create Template Library at /c/Users/amikinka/.claude/templates/ (P2)

**Location:** `/c/Users/amikinka/.claude/templates/`

This task creates 17 files. The complete specification for every file is in
`docs/spec/agent-ecosystem-action-plan.md`, Deliverable 2 (lines 263-556).

Read Deliverable 2 in full before starting this task.

### File creation order (recommended)

1. `README.md` — write this first so you have the variable index visible while
   writing the other templates.
2. `agents/agent-minimal.md`
3. `agents/agent-full.md`
4. `agents/agent-tool-calling.md`
5. `agents/agent-mcp-consumer.md`
6. `agents/agent-pipeline-stage.md`
7. `components/command.md`
8. `components/task.md`
9. `components/checklist.md`
10. `components/knowledge-base.md`
11. `components/utility.md`
12. `pipeline/domain-analysis-output.md`
13. `pipeline/workflow-model.md`
14. `pipeline/loom-topology.md`
15. `pipeline/ecosystem-manifest.md`
16. `meta/ecosystem-handoff.md`
17. `meta/agent-stub.yaml`

### Universal requirements for all template files

Every template file must begin with an HTML comment block (before any Markdown
content) that declares:

```html
<!--
Template: <filename>
Purpose: <one-line purpose>
Produced artifact: <what this template generates when instantiated>
Used by: <pipeline stage(s) or agent(s) that use this template>
Spec section: <section of agent-ecosystem-design-spec.md that this implements>
Variables:
  - {{VAR_NAME}} (type): description
  - {{?OPTIONAL_VAR}} (type, optional): description
This comment block must be stripped when the template is instantiated.
-->
```

Every `{{VARIABLE}}` in the template body must be declared in this comment block.
No undeclared variables.

### Critical requirements for agent template files (agents/ directory)

All five agent templates (`agent-minimal.md`, `agent-full.md`, `agent-tool-calling.md`,
`agent-mcp-consumer.md`, `agent-pipeline-stage.md`) must:

1. Be valid `.md` files that `_load_md_agent()` can parse. This means:
   - Begin with `---` (after the HTML comment)
   - Have a closing `---` delimiter
   - Have valid YAML frontmatter between the delimiters (with variables as
     placeholder values — e.g., `id: "{{AGENT_ID}}"`)
   - Have a Markdown body after the closing `---`

2. **Use list format for complexity_range:** `complexity_range: [{{COMPLEXITY_MIN}}, {{COMPLEXITY_MAX}}]`
   Never use dict format in any template.

3. **Use Section 4-conformant tool-call blocks.** For `agent-tool-calling.md`,
   `agent-mcp-consumer.md`, and `agent-pipeline-stage.md`, the tool-call blocks
   in the prompt body must exactly follow the syntax from Section 4 of the design
   spec. Before writing any tool-call block, re-read Section 4. Use the following
   self-check:
   - Is `CALL:` the first key? (Yes / No)
   - Is `purpose:` present? (Yes / No)
   - For MCP calls: does the tool name follow `mcp__server-name__tool-name`? (Yes / No)
   - For conditionals: does `IF:` appear on its own line at the start? (Yes / No)
   - Does `END IF:` appear on its own line at the end? (Yes / No)
   All five must be "Yes" for every tool-call block.

### requirements for README.md

`/c/Users/amikinka/.claude/templates/README.md` must contain:

1. **Library overview** — what the library is, who uses it, where it lives.
2. **Directory tree** — the complete annotated tree matching the structure in Section
   9.2 of the design spec (added by the software-program-manager).
3. **Variable naming convention** — explanation of `{{REQUIRED}}` vs `{{?OPTIONAL}}`
   syntax.
4. **Template selection guide** — decision tree: which agent template to use based on
   agent type (pipeline stage node, MCP-heavy, tool-calling, specialist, placeholder).
5. **Complete variable index** — a table of every variable across all 17 templates,
   with type, definition, and which templates use it. This table is the canonical
   reference; it must match Section 9.4 of the design spec.
6. **Maintenance obligation** — a section stating that when the design spec changes,
   the author of the spec change must update the affected templates in the same
   authoring session.
7. **Version history** — a table with at minimum one entry: `v1.0.0 | 2026-04-07 |
   Initial template library created by senior-developer per Milestone 1 work order`.

### What NOT to do in Task 7

- Do NOT create subdirectories that are not in the specified tree. The tree is:
  `/c/Users/amikinka/.claude/templates/` with exactly four subdirectories:
  `agents/`, `components/`, `pipeline/`, `meta/`.
- Do NOT use Windows-style path separators in template content. All file path
  references inside templates must use forward slashes.
- Do NOT write actual Python code in the template files (they are Markdown/YAML
  templates, not source code).
- Do NOT commit the template library to the GAIA repository. It lives at a
  local user path, not inside the repo.

---

## Constraints That Apply to All Tasks

### What you must NEVER do

1. **Do NOT modify the existing `_load_agent()` method's signature or return type.**
   Task 2 refactors its internals (by extracting `_build_agent_definition()`), but
   the method's external interface must remain identical. All existing code that calls
   `_load_agent()` must continue to work without changes.

2. **Do NOT remove or rename `_load_agent()`.** The 18 existing YAML agents still
   use the YAML loader. Both methods must coexist.

3. **Do NOT implement condition parsing for `IF:` blocks anywhere.** This is
   explicitly out of scope per design spec Section 4.4 Scope Boundary (which you
   will have added in Task 1, Edit 1.4).

4. **Do NOT write a migration script** (`scripts/migrate_agents_yaml_to_md.py`) in
   this iteration. That is Milestone 2 work. If you complete all P0 and P1 tasks
   early and want to start P2, proceed to Task 7 (template library), not the
   migration script.

5. **Do NOT delete any existing `.yaml` agent files** in `config/agents/`. The YAML
   files are the production source until Milestone 2 migration is complete and
   verified.

6. **Do NOT modify `src/gaia/core/capabilities.py`** to add `VALID_CAPABILITY_STRINGS`.
   That is Milestone 2 work and blocked on vocabulary normalization of all 18 YAML
   files.

7. **Do NOT push to remote or create a pull request.** The software-program-manager
   will coordinate merge timing after quality-reviewer sign-off.

8. **Do NOT add emojis to any file you write or edit.** The project style guideline
   prohibits emojis in generated content.

---

## Completion Report Format

When all assigned tasks are complete (or when iteration budget is exhausted), produce
a completion report with the following sections. Write the report as your final output.

```
## Completion Report — Senior Developer Work Order (Milestone 1)

### Tasks Completed

| Task | Status | Files written | Lines added | Notes |
|------|--------|---------------|-------------|-------|
| Task 1 — 8 spec edits | COMPLETE / PARTIAL / DEFERRED | 1 | N | ... |
| Task 2 — _build_agent_definition() | ... | ... | ... | ... |
| Task 3 — _load_md_agent() | ... | ... | ... | ... |
| Task 4 — _load_all_agents() extension | ... | ... | ... | ... |
| Task 5 — senior-developer.md | ... | ... | ... | ... |
| Task 6 — unit tests | ... | ... | ... | ... |
| Task 7 — template library | ... | ... | ... | ... |

### Scope Changes

[List any deliverable that deviates from this work order, including the reason.
If a tool-call block in Deliverable 4 does not conform to Section 4 syntax, list it here.]

### Deferred Items

[List any tasks not completed due to scope or iteration budget, with a clear
statement of what remains for the next iteration.]

### Verification Steps Completed

- [ ] Read back all edited spec sections after Task 1 — confirmed no unintended changes
- [ ] Traced _load_md_agent() split logic against senior-developer.md content — confirmed correct split
- [ ] Verified senior-developer.md complexity_range is list format [0.3, 1.0]
- [ ] Verified every tool-call block in senior-developer.md conforms to Section 4
- [ ] Verified _load_agent() behavior unchanged after _build_agent_definition() extraction
- [ ] All unit tests pass (if Task 6 completed)
- [ ] Template library README.md variable index matches Section 9.4 of design spec (if Task 7 completed)

### Open Items for Quality Reviewer

[List anything the quality-reviewer should specifically check beyond the standard
verification checklist in the action plan.]
```

---

## Reference: Key File Locations

| File | Purpose |
|------|---------|
| `C:\Users\amikinka\gaia\docs\spec\agent-ecosystem-design-spec.md` | Primary spec — 8 edits applied here |
| `C:\Users\amikinka\gaia\docs\spec\agent-ecosystem-action-plan.md` | Action plan — all deliverable content |
| `C:\Users\amikinka\gaia\docs\spec\senior-dev-work-order.md` | This file |
| `C:\Users\amikinka\gaia\src\gaia\agents\registry.py` | Registry — add `_build_agent_definition()`, `_load_md_agent()`, extend `_load_all_agents()` |
| `C:\Users\amikinka\gaia\src\gaia\agents\base\context.py` | Dataclass definitions — read-only reference |
| `C:\Users\amikinka\gaia\src\gaia\core\capabilities.py` | Read-only in this iteration — do not modify |
| `C:\Users\amikinka\gaia\config\agents\senior-developer.md` | Create new — proof-of-concept .md agent |
| `C:\Users\amikinka\gaia\tests\unit\test_load_md_agent.py` | Create new — 10 unit tests |
| `/c/Users/amikinka/.claude/templates/README.md` | Create new — template library index |
| `/c/Users/amikinka/.claude/templates/agents/agent-minimal.md` | Create new |
| `/c/Users/amikinka/.claude/templates/agents/agent-full.md` | Create new |
| `/c/Users/amikinka/.claude/templates/agents/agent-tool-calling.md` | Create new |
| `/c/Users/amikinka/.claude/templates/agents/agent-mcp-consumer.md` | Create new |
| `/c/Users/amikinka/.claude/templates/agents/agent-pipeline-stage.md` | Create new |
| `/c/Users/amikinka/.claude/templates/components/command.md` | Create new |
| `/c/Users/amikinka/.claude/templates/components/task.md` | Create new |
| `/c/Users/amikinka/.claude/templates/components/checklist.md` | Create new |
| `/c/Users/amikinka/.claude/templates/components/knowledge-base.md` | Create new |
| `/c/Users/amikinka/.claude/templates/components/utility.md` | Create new |
| `/c/Users/amikinka/.claude/templates/pipeline/domain-analysis-output.md` | Create new |
| `/c/Users/amikinka/.claude/templates/pipeline/workflow-model.md` | Create new |
| `/c/Users/amikinka/.claude/templates/pipeline/loom-topology.md` | Create new |
| `/c/Users/amikinka/.claude/templates/pipeline/ecosystem-manifest.md` | Create new |
| `/c/Users/amikinka/.claude/templates/meta/ecosystem-handoff.md` | Create new |
| `/c/Users/amikinka/.claude/templates/meta/agent-stub.yaml` | Create new |

---

*Work order issued by software-program-manager*
*Pipeline iteration 1 of 5 agents*
*Milestone: 1 — Foundation*
*Next milestone owner: quality-reviewer (verification of Milestone 1 deliverables)*
