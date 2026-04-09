# Phase 5: Senior Developer Work Order — Milestone 1

**Document Type:** Work Order
**Issued by:** software-program-manager
**Assigned to:** senior-developer
**Date:** 2026-04-07
**Branch:** feature/pipeline-orchestration-v1
**Priority:** P0
**Milestone:** Milestone 1 — Foundation

---

## Work Order Summary

This work order assigns Milestone 1 (Foundation) tasks from the Phase 5 implementation plan to the senior-developer agent. Completion of these tasks unblocks all subsequent Phase 5 work.

**Tasks Assigned:** 5 P0 tasks (Week 1)
**Estimated Effort:** 13 hours
**Target Completion:** End of pipeline iteration
**Quality Gate:** All tasks verified by quality-reviewer before Milestone 2 begins

---

## Pre-Work: Files to Read

Before starting any task, read the following files in full:

1. `C:\Users\antmi\gaia\docs\spec\agent-ecosystem-design-spec.md` — complete file
2. `C:\Users\antmi\gaia\docs\spec\agent-ecosystem-action-plan.md` — complete file (Deliverables 1, 3, 4)
3. `C:\Users\antmi\gaia\src\gaia\agents\registry.py` — complete file
4. `C:\Users\antmi\gaia\src\gaia\agents\base\context.py` — complete file
5. `C:\Users\antmi\gaia\docs\reference\phase5-implementation-plan.md` — this plan

---

## Task List

### Task PH5-W1-D1: Apply 8 Spec Edits

**File:** `C:\Users\antmi\gaia\docs\spec\agent-ecosystem-design-spec.md`

**Instructions:**

Read the file first, then apply each edit as a targeted replacement. Do not restructure sections or add prose not specified in the edit.

**Edit 1.1 — Section 3.2, complexity_range field:**
Locate the block in Section 3.2 that reads:
```yaml
  complexity_range:                   # Complexity score range [0.0, 1.0] within which
    min: 0.3                          # this agent is eligible. Maps to
    max: 1.0                          # AgentTriggers.complexity_range.
```
Replace with:
```yaml
  complexity_range: [0.3, 1.0]        # Complexity score range [min, max] within [0.0, 1.0].
                                      # YAML sequence maps directly to AgentTriggers.
                                      # complexity_range Tuple[float, float]. Do NOT use
                                      # dict format {min: X, max: Y} — that form requires
                                      # a brittle .values() call and is legacy-only.
```

**Edit 1.2 — Section 3.4, senior-developer.md example:**
Locate the frontmatter in Section 3.4 and find:
```yaml
  complexity_range:
    min: 0.3
    max: 1.0
```
Replace with:
```yaml
  complexity_range: [0.3, 1.0]
```

**Edit 1.3 — Section 3.2, capabilities field comment:**
Find the comment "Must use vocabulary defined in src/gaia/core/capabilities.py" and replace with:
"Should use controlled vocabulary. Formal VALID_CAPABILITY_STRINGS constant is Phase 2 work; see action-plan.md Tension 4 resolution."

**Edit 1.4 — Section 4.4, add Scope Boundary subsection:**
After the "Condition syntax rules" bullet list in Section 4.4, add:
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

**Edit 1.5 — Section 5.7, _load_md_agent() code fix:**
Find `with open(md_file, "r", encoding="utf-8") as f:` and replace with:
`with open(md_file, "r", encoding="utf-8-sig") as f:  # utf-8-sig strips UTF-8 BOM`

Add after `content = f.read()`:
```python
    # Normalize Windows CRLF and bare CR to LF before any string operations
    content = content.replace('\r\n', '\n').replace('\r', '\n')
```

**Edit 1.6 — Section 6.2, add two unit tests:**
Add to the unit test list:
- `test_load_md_agent_crlf_line_endings` — verifies CRLF-encoded .md file loads correctly
- `test_load_md_agent_bom_prefix` — verifies UTF-8 BOM-prefixed .md file loads correctly

**Edit 1.7 — Section 6.1 Step 4, capabilities validation:**
Replace "Standardize capabilities: values against src/gaia/core/capabilities.py vocabulary" with:
"Align capabilities: values with the vocabulary defined in the existing 18 YAML agent files; formal VALID_CAPABILITY_STRINGS enforcement is Phase 2 work."

**Edit 1.8 — Section 8, close four Open Questions:**
Add resolutions to Q1, Q7.1, Q7.2, and Q7.4 as specified in action-plan.md Deliverable 1.

**Acceptance Criteria:**
- All 8 edits applied exactly as specified
- Read back the file to verify no unintended changes
- complexity_range uses list format in both Section 3.2 and 3.4

**Estimated Effort:** 2 hours

---

### Task PH5-W1-D2: Extract _build_agent_definition()

**File:** `C:\Users\antmi\gaia\src\gaia\agents\registry.py`

**Instructions:**

Add a new private method `_build_agent_definition()` to the `AgentRegistry` class. This method must contain all field-parsing logic currently in `_load_agent()`.

**Method Signature:**
```python
def _build_agent_definition(
    self,
    data: dict,
    system_prompt_override: str = None
) -> AgentDefinition:
```

**Implementation Requirements:**

1. Handle both nested (`data["agent"]`) and flat formats:
   ```python
   agent_data = data.get("agent", data)
   ```

2. Parse triggers with dual-format complexity_range handler:
   ```python
   triggers_data = agent_data.get("triggers", {})
   raw = triggers_data.get("complexity_range", [0.0, 1.0])
   if isinstance(raw, dict):
       # Legacy dict format — use explicit key lookup
       complexity_range = (
           float(raw.get("min", 0.0)),
           float(raw.get("max", 1.0))
       )
   elif isinstance(raw, (list, tuple)) and len(raw) == 2:
       complexity_range = (float(raw[0]), float(raw[1]))
   else:
       complexity_range = (0.0, 1.0)
   ```

3. Resolve system_prompt with override:
   ```python
   if system_prompt_override is not None:
       system_prompt = system_prompt_override
   else:
       system_prompt = agent_data.get("system_prompt", "")
   ```

4. Parse all other fields identically to existing `_load_agent()` logic

5. Return `AgentDefinition(...)` with all parsed fields

**After adding the method:**
Update `_load_agent()` to call `_build_agent_definition(data)` instead of inline parsing.

**Acceptance Criteria:**
- `_build_agent_definition()` exists with correct signature
- Handles both dict and list complexity_range formats
- `_load_agent()` calls `_build_agent_definition()` (no duplicated logic)
- Existing behavior unchanged (backward compatible)

**Estimated Effort:** 3 hours

---

### Task PH5-W1-D3: Implement _load_md_agent()

**File:** `C:\Users\antmi\gaia\src\gaia\agents\registry.py`

**Instructions:**

Add the following async method to `AgentRegistry` immediately after `_load_agent()`:

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
    # utf-8-sig strips the UTF-8 BOM that Windows tools emit
    with open(md_file, "r", encoding="utf-8-sig") as f:
        content = f.read()

    # Normalize Windows CRLF and bare CR to LF
    content = content.replace('\r\n', '\n').replace('\r', '\n')

    if not content.startswith("---"):
        raise AgentLoadError(
            f"{md_file.name}: does not begin with YAML frontmatter block (---)"
        )

    # Strip the opening "---\n" before searching for closing delimiter
    if content.startswith("---\n"):
        inner = content[4:]  # remove "---\n"
    else:
        inner = content[3:]

    first_close = inner.find("\n---\n")
    if first_close == -1:
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
        system_prompt = inner[first_close + 5:].strip()  # skip "\n---\n"

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

**Acceptance Criteria:**
- Method signature matches exactly
- Uses `utf-8-sig` encoding
- CRLF normalization before string operations
- Correct split logic using `find()` (not naive `split()`)
- Calls `_build_agent_definition()` to parse fields
- Raises `AgentLoadError` for invalid files

**Estimated Effort:** 4 hours

---

### Task PH5-W1-D4: Extend _load_all_agents()

**File:** `C:\Users\antmi\gaia\src\gaia\agents\registry.py`

**Instructions:**

Locate the `_load_all_agents()` method. Find the lines that glob for agent files and modify:

```python
# Original:
agent_files = list(self._agents_dir.glob("*.yaml"))
agent_files.extend(self._agents_dir.glob("*.yml"))

# Modified:
agent_files = list(self._agents_dir.glob("*.yaml"))
agent_files.extend(self._agents_dir.glob("*.yml"))
agent_files.extend(self._agents_dir.glob("*.md"))  # NEW
```

Then update the loading loop:

```python
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
        # Collision guard: skip if agent ID already loaded
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

**Acceptance Criteria:**
- Glob includes `*.md` files
- Dispatches to correct loader based on suffix
- Collision guard logs warning and skips second file
- YAML files loaded first (YAML wins during migration)

**Estimated Effort:** 2 hours

---

### Task PH5-W1-D5: Write senior-developer.md

**File:** `C:\Users\antmi\gaia\config\agents\senior-developer.md`

**Instructions:**

Write this file with the exact content from action-plan.md Deliverable 4 (lines 675-914). Do not make creative modifications.

**Key Requirements:**
- Begins with `---` on first line (no blank line before)
- Uses `complexity_range: [0.3, 1.0]` (list format)
- Non-empty system prompt body after closing `---`
- Contains 5 tool-call blocks (2 in Phase 1, 1 MCP in Phase 2, 1 IF conditional in Phase 3, 2 in Phase 4)
- Ends with "Constraints and Safety" section

**Tool-Call Block Verification:**
Before writing, re-read Section 4 of the design spec. Verify every tool-call block conforms:
- Fenced with ` ```tool-call ` and ` ``` `
- `CALL:` is first key
- `purpose:` present on every block
- `capture:` present when variable needed
- `IF:` on its own line at start, `END IF:` on its own line at end
- MCP tool names follow `mcp__server-name__tool-name` pattern

**Acceptance Criteria:**
- File loads via `_load_md_agent()` without errors
- `complexity_range` parses to `(0.3, 1.0)`
- Non-empty `system_prompt` extracted
- All tool-call blocks conform to Section 4 syntax

**Estimated Effort:** 2 hours

---

## Constraints

### What You Must NEVER Do

1. Do NOT modify `_load_agent()` signature or return type
2. Do NOT remove or rename `_load_agent()` — YAML agents still use it
3. Do NOT implement condition parsing for `IF:` blocks (out of scope per spec Section 4.4)
4. Do NOT write migration script — that is Milestone 2 work
5. Do NOT delete existing `.yaml` agent files
6. Do NOT modify `src/gaia/core/capabilities.py` (VALID_CAPABILITY_STRINGS is Milestone 2)
7. Do NOT push to remote or create PR — quality-reviewer must sign off first
8. Do NOT add emojis to any file

---

## Completion Report Format

When all tasks are complete, produce a completion report:

```markdown
## Completion Report — Phase 5 Milestone 1

### Tasks Completed

| Task | Status | Files Modified | Lines Added |
|------|--------|----------------|-------------|
| PH5-W1-D1 | COMPLETE | 1 | N/A |
| PH5-W1-D2 | COMPLETE | 1 | ~80 |
| PH5-W1-D3 | COMPLETE | 1 | ~70 |
| PH5-W1-D4 | COMPLETE | 1 | ~30 |
| PH5-W1-D5 | COMPLETE | 1 | ~250 |

### Verification Steps Completed

- [ ] Read back all edited spec sections after PH5-W1-D1
- [ ] Verified _load_agent() behavior unchanged after PH5-W1-D2
- [ ] Traced _load_md_agent() split logic against senior-developer.md
- [ ] Verified senior-developer.md complexity_range is [0.3, 1.0]
- [ ] Verified all tool-call blocks in senior-developer.md conform to Section 4
- [ ] All new methods have type hints

### Open Items for Quality Reviewer

- [List anything requiring specific review attention]
```

---

## Escalation Path

If you encounter blockers:
1. First: Document the blocker in your completion report
2. Second: Escalate to software-program-manager
3. Third: Escalate to @kovtcharov-amd if architecture decision required

---

**Work Order Issued By:** software-program-manager
**Pipeline Iteration:** 1 of 5
**Milestone:** 1 — Foundation
**Next Milestone Owner:** quality-reviewer (verification)

---

**END OF SENIOR DEVELOPER WORK ORDER**
