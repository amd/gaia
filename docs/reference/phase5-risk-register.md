# Phase 5: Risk Register

**Document Type:** Risk Management Plan
**Issued by:** software-program-manager
**Date:** 2026-04-07
**Branch:** feature/pipeline-orchestration-v1
**Phase:** 5 - Domain Analyzer + Agentic Ecosystem Creator

---

## Risk Summary Dashboard

```markdown
## Risk Dashboard — Week X of 4

### Risk Distribution

| Severity | Active | Mitigated | Realized | Closed |
|----------|--------|-----------|----------|--------|
| HIGH     | 3      | 0         | 0        | 0      |
| MEDIUM   | 6      | 0         | 0        | 0      |
| LOW      | 3      | 0         | 0        | 0      |
| **TOTAL**| **12** | **0**     | **0**    | **0**  |

### Top 3 Critical Risks

1. **R5.1** — Template tool-call blocks inconsistent with Section 4 spec (HIGH)
2. **R5.3** — Registry naming collision with PR #720 (HIGH)
3. **R5.2** — _load_md_agent() split logic fails on edge cases (HIGH)

### Risk Burn-down

| Week | Active Risks | Mitigations Completed | Realizations |
|------|--------------|----------------------|--------------|
| 1    | 12           | 0                    | 0            |
| 2    | TBD          | TBD                  | TBD          |
| 3    | TBD          | TBD                  | TBD          |
| 4    | TBD          | TBD                  | TBD          |
```

---

## High-Priority Risks

### R5.1 — Template Tool-Call Blocks Inconsistent with Section 4 Spec

**Category:** Technical Implementation
**Severity:** HIGH
**Probability:** HIGH
**Impact:** HIGH

**Description:**
The senior-developer will author 17 template files and 10 unit tests in a single iteration. Tool-call blocks in agent templates must strictly conform to Section 4 syntax (fenced `tool-call` blocks with `CALL:`, `purpose:`, `capture:`, `IF:/END IF:` patterns). If the senior-developer invents variations — different key names, different fencing style, different IF syntax — those variations will be propagated into every agent generated from those templates.

**Consequences:**
- Generated agents have non-conforming tool-call blocks
- quality-reviewer and pipeline engine depend on canonical syntax
- Drift creates corpus of non-conforming agents requiring retroactive correction
- Spec becomes non-authoritative

**Mitigation Strategy:**
1. quality-reviewer must cross-check every tool-call block in every generated template against Section 4 before Milestone 1 acceptance
2. Work order includes self-review checklist:
   - Is `CALL:` the first key? (Yes / No)
   - Is `purpose:` present? (Yes / No)
   - For MCP calls: does tool name follow `mcp__server__tool-name`? (Yes / No)
   - For conditionals: does `IF:` appear on its own line? (Yes / No)
   - Does `END IF:` appear on its own line? (Yes / No)
3. Create validation checklist in template HTML comment headers

**Mitigation Owner:** senior-developer, quality-reviewer
**Status:** ACTIVE
**Trigger:** Milestone 1 completion

**Contingency Plan:**
If non-conformance discovered post-milestone:
1. Audit all 17 templates against Section 4
2. Create correction script to fix systematic deviations
3. Re-run template generation with corrected templates
4. Add automated syntax validation to test suite

---

### R5.2 — _load_md_agent() Split Logic Fails on Edge-Case Files

**Category:** Technical Implementation
**Severity:** HIGH
**Probability:** MEDIUM
**Impact:** HIGH

**Description:**
The `_load_md_agent()` method splits content on `"\n---\n"` to separate frontmatter from body. Edge cases include:
- Files ending without trailing newline
- Files with `---` horizontal rule in body text
- Files with more than two `---` delimiters
- Files with `---` immediately after closing delimiter (no blank line)

**Consequences:**
- Wrong split produces garbled YAML (parse error)
- Wrong split produces truncated system prompt (silent data loss)
- Agent fails to load with `AgentLoadError`
- Migration of existing agents blocked

**Mitigation Strategy:**
1. Implementation uses explicit `find("\n---\n")` after stripping opening `---\n`
2. Unit tests must include:
   - `test_load_md_agent_body_preserves_special_characters` — body with `---` horizontal rule
   - `test_load_md_agent_crlf_line_endings` — CRLF delimiters
   - `test_load_md_agent_bom_prefix` — BOM-prefixed file
   - `test_load_md_agent_no_trailing_newline` — file ending without newline
3. Error messages must be diagnostic (include file path and specific failure reason)

**Mitigation Owner:** senior-developer, test-engineer
**Status:** ACTIVE
**Trigger:** Unit test execution

**Contingency Plan:**
If split logic fails in production:
1. Add fallback parser with more permissive delimiter detection
2. Log detailed diagnostic info for failed files
3. Provide manual migration path for edge-case files

---

### R5.3 — Registry Naming Collision with PR #720

**Category:** Integration
**Severity:** HIGH
**Probability:** HIGH
**Impact:** MEDIUM

**Description:**
Both PR #720 and Phase 5 have classes named `AgentRegistry` in `src/gaia/agents/registry.py`. After rebase, this creates:
- Import conflicts (which `AgentRegistry` to import?)
- Runtime confusion (which registry is being used?)
- Code review complexity

**Consequences:**
- Import errors at runtime
- Wrong registry used in pipeline code
- Merge conflict resolution errors

**Mitigation Strategy:**
1. Pre-merge coordination: agree on naming convention with itomek
2. Post-rebase: rename our class to `PipelineAgentRegistry` and move to `pipeline/agent_registry.py`
3. Update all imports: `from gaia.pipeline.agent_registry import PipelineAgentRegistry`
4. Document naming convention in code comments

**Mitigation Owner:** software-program-manager, senior-developer
**Status:** ACTIVE
**Trigger:** PR #720 merge

**Contingency Plan:**
If naming collision causes runtime errors:
1. Immediately rename our class with clear prefix (`PipelineAgentRegistry`)
2. Grep for all imports and update
3. Run full test suite to verify no broken imports

---

## Medium-Priority Risks

### R5.4 — Agent ID Collision During Migration

**Category:** Data Migration
**Severity:** MEDIUM
**Probability:** MEDIUM
**Impact:** MEDIUM

**Description:**
During migration period, both `senior-developer.yaml` and `senior-developer.md` may exist in `config/agents/`. Without collision detection, the second file loaded silently overwrites the first.

**Consequences:**
- Non-deterministic agent loading (depends on glob order)
- Silent data loss (one agent definition ignored)
- Debugging complexity (which file is canonical?)

**Mitigation Strategy:**
1. Collision guard in `_load_all_agents()`: if `agent.id` already exists, log warning and skip second file
2. YAML files loaded first (YAML wins during migration)
3. Warning message includes both file paths for operator action
4. Migration guide documents collision resolution

**Mitigation Owner:** senior-developer
**Status:** ACTIVE
**Trigger:** Migration script execution

---

### R5.5 — Milestone 1 Scope Too Large for Single Iteration

**Category:** Resource Management
**Severity:** MEDIUM
**Probability:** MEDIUM
**Impact:** MEDIUM

**Description:**
Milestone 1 requires: 8 spec edits, 2 registry methods + helper, 10 unit tests, 1 agent migration, and 17 template files. For a single senior-developer agent iteration, this is aggressive scope.

**Consequences:**
- Iteration budget exhausted before all deliverables complete
- Template library files (P2) most likely to be cut
- Unit tests (P1) may be deferred

**Mitigation Strategy:**
1. Priority order: P0 (Tasks 1-5) > P1 (Task 6 unit tests) > P2 (Task 7 template library)
2. If scope must be cut, defer template library to next iteration
3. Track deferred items explicitly in completion report
4. software-program-manager monitors iteration budget

**Mitigation Owner:** software-program-manager
**Status:** ACTIVE
**Trigger:** Iteration budget monitoring

---

### R5.6 — Complexity Vocabulary Drift Before VALID_CAPABILITY_STRINGS

**Category:** Technical Debt
**Severity:** MEDIUM
**Probability:** HIGH
**Impact:** MEDIUM

**Description:**
Without enforcement, each agent author invents capability strings independently. After 18 migrations, vocabulary will have inconsistencies (e.g., `full-stack-development` vs `fullstack` vs `full_stack_dev`).

**Consequences:**
- Routing index built from inconsistent strings
- False negatives in `select_agent()` — agent exists but never selected
- Harder to clean up after migration (inconsistencies baked into .md files)

**Mitigation Strategy:**
1. Before migration, extract complete set of capability strings from all 18 YAML files
2. Normalize spelling variations
3. Document canonical list in `capabilities.py` comment block
4. Apply normalized list consistently during migration

**Mitigation Owner:** senior-developer
**Status:** ACTIVE
**Trigger:** Migration script execution

---

### R5.7 — senior-developer.md Prompt Body Deviates from Deliverable 4

**Category:** Quality Assurance
**Severity:** MEDIUM
**Probability:** LOW
**Impact:** MEDIUM

**Description:**
Deliverable 4 content in action plan is the complete, production-ready file. Risk that senior-developer makes "improvement" edits introducing inconsistencies with frontmatter or deviating from Section 4 tool-call syntax.

**Consequences:**
- Proof-of-concept file has errors
- Undermines confidence in .md format
- Requires second iteration

**Mitigation Strategy:**
1. Work order instructs verbatim copy from Deliverable 4
2. If genuine error identified, report as scope change rather than silently fix
3. quality-reviewer verifies file loads with expected field values

**Mitigation Owner:** senior-developer, quality-reviewer
**Status:** ACTIVE
**Trigger:** senior-developer.md creation

---

### R5.8 — Template Library Not Accessible from CI/CD

**Category:** Infrastructure
**Severity:** MEDIUM
**Probability:** MEDIUM
**Impact:** LOW

**Description:**
Template library lives at `/c/Users/antmi/.claude/templates/` — a local user path. CI/CD pipelines run in clean environments without this path.

**Consequences:**
- Tests or build steps referencing template library fail in CI
- Milestone 3 (ecosystem-builder agent) may fail in CI if it reads templates at runtime

**Mitigation Strategy:**
1. Template path configurable via `GAIA_TEMPLATE_LIBRARY_PATH` environment variable
2. For Milestone 1, document note only (templates not used at runtime)
3. For Milestone 3, add env var configuration to ecosystem-builder prompt
4. CI configuration sets `GAIA_TEMPLATE_LIBRARY_PATH` to repo-relative path

**Mitigation Owner:** senior-developer
**Status:** ACTIVE
**Trigger:** Milestone 3 planning

---

### R5.9 — CRLF/BOM Fix Insufficient for All Windows Edge Cases

**Category:** Technical Implementation
**Severity:** MEDIUM
**Probability:** LOW
**Impact:** HIGH

**Description:**
Windows tools emit various file formats: CRLF, bare CR, UTF-8 BOM, UTF-16 LE BOM, mixed line endings. Current fix handles CRLF and UTF-8 BOM only.

**Consequences:**
- Some Windows-authored files fail to load
- Migration blocked for files from specific editors
- User frustration

**Mitigation Strategy:**
1. Test fixtures must include Notepad-saved files, BOM-prefixed files, mixed line endings
2. Consider using `io.open()` with `newline=''` for maximum compatibility
3. Error messages should suggest file format fixes

**Mitigation Owner:** test-engineer, senior-developer
**Status:** ACTIVE
**Trigger:** Unit test execution

---

## Low-Priority Risks

### R5.10 — .values() Ordering Assumption Breaks Legacy Dict Format

**Category:** Technical Implementation
**Severity:** LOW
**Probability:** LOW
**Impact:** HIGH

**Description:**
Legacy dict format `{min: 0.3, max: 1.0}` parsed via `.values()` relies on dict ordering. Python 3.7+ guarantees insertion order, but YAML parsing order may vary.

**Consequences:**
- Agent with `{min: 0.3, max: 1.0}` loads as `(1.0, 0.3)` — reversed tuple
- Agent never matches any task (complexity score always less than "min")

**Mitigation Strategy:**
1. Use explicit `raw.get("min")` and `raw.get("max")` instead of `.values()`
2. Document in code comment why explicit lookup is required

**Mitigation Owner:** senior-developer
**Status:** ACTIVE
**Trigger:** `_build_agent_definition()` implementation

---

### R5.11 — Template Staleness as Spec Evolves

**Category:** Maintenance
**Severity:** LOW
**Probability:** HIGH
**Impact:** MEDIUM

**Description:**
Spec and templates maintained separately. Spec change adding required frontmatter field won't automatically update templates.

**Consequences:**
- Ecosystem Builder generates agent files from stale templates
- Generated files missing new required field
- Files fail to load

**Mitigation Strategy:**
1. Section 9.5 documents maintenance obligation: template updates required in same commit as spec changes
2. Create validation script (`scripts/validate_templates.py`) checking templates against required field list
3. Template README tracks corresponding spec sections

**Mitigation Owner:** technical-writer-expert
**Status:** ACTIVE
**Trigger:** Spec change commits

---

### R5.12 — Stage Agents Require More Iteration Budget Than Allocated

**Category:** Resource Management
**Severity:** LOW
**Probability:** MEDIUM
**Impact:** MEDIUM

**Description:**
Week 3 allocates 4 hours per stage agent (4 agents in 4 days). Each agent requires:
- Full prompt body with 5+ phases
- 2+ tool-call blocks per phase
- Tool-call syntax validation
- Load testing via `_load_md_agent()`

**Consequences:**
- Stage agents incomplete at iteration end
- Domain Analyzer and Workflow Modeler (P0) prioritized over Loom Builder and Ecosystem Builder (P1)
- Phase 5 timeline extended

**Mitigation Strategy:**
1. Priority order: Domain Analyzer > Workflow Modeler > Loom Builder > Ecosystem Builder
2. Track progress daily
3. If behind, defer P2 agents to next iteration

**Mitigation Owner:** software-program-manager
**Status:** ACTIVE
**Trigger:** Week 3 daily standup

---

## Risk Monitoring Plan

### Weekly Risk Review

**When:** End of each week (Friday)
**Who:** software-program-manager, testing-quality-specialist
**Agenda:**
1. Review active risks — any probability/impact changes?
2. Review mitigations — completed? effective?
3. Review realizations — any risks materialized?
4. Identify new risks — any emerging concerns?

### Risk Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Active HIGH risks | < 3 | Count at week end |
| Mitigation completion rate | > 80% | Mitigations completed / planned |
| Risk realization rate | 0% | Realizations / active risks |

### Escalation Triggers

| Trigger | Escalation Path |
|---------|-----------------|
| HIGH risk realized | Immediate escalation to @kovtcharov-amd |
| > 3 HIGH risks active for 2+ weeks | Escalate to software-program-manager |
| Mitigation completion rate < 50% | Escalate to planning-analysis-strategist |

---

## Contact

**Risk Owner:** software-program-manager
**Review Cadence:** Weekly (Friday)
**Escalation:** @kovtcharov-amd

---

**Document Status:** ACTIVE
**Next Review:** End of Week 1

---

**END OF PHASE 5 RISK REGISTER**
