# Master Spec Sheet — Plan Document

> **Author:** Dr. Sarah Kim, Technical Product Strategist & Engineering Lead
> **Date:** 2026-05-08
> **Target Output:** `plans/MASTER-SPEC-SHEET.md`
> **Executor:** software-program-manager agent

---

## 1. PURPOSE

Create a single, authoritative catalog of all 132 pipeline orchestration branches in the GAIA project. The Master Spec Sheet serves as the program-level truth source for merge ordering, dependency tracking, cross-referencing (Issue, PR, Branch, Commit), and live status of every item in the pipeline.

---

## 2. DATA SOURCES

The executor MUST pull from these files in priority order:

| Priority | File | What to Extract |
|----------|------|-----------------|
| 1 | `plans/PR-PLANS-ALL-FINAL.md` | PR-PLAN key, SOURCE_COMMIT, ISSUE_TITLE, ISSUE_BODY, ISSUE_LABELS, BRANCH_NAME, BRANCH_BASE, PR_TITLE, PR_BODY, MERGE_ORDER, DEPENDS_ON, BATCH_WITH |
| 2 | `plans/remaining-plans.json` | Additional entries for commits 88-132 (new batch) — same fields as above, in JSON format |
| 3 | `plans/EXECUTION-TRACKING.md` | Issue numbers, issue URLs, branch push status (OK/PARTIAL), v1/v2 wave attribution |
| 4 | `plans/execution-results.json` | Per-entry status: issue_num, issue_url, branch_ok boolean, status field |
| 5 | GitHub API (fork: `antmikinka/gaia`) | PR URLs and PR open/merged/closed status — resolved at generation time via `gh pr list --repo antmikinka/gaia` |

### Data Source Cross-Reference Key

- PR-PLANS-ALL-FINAL contains entries 1-87 (the original cpp batch)
- remaining-plans.json contains entries 88-132 (the new batch, Wave N)
- EXECUTION-TRACKING.md provides the authoritative issue number mapping
- execution-results.json provides per-entry branch push confirmation

---

## 3. DOCUMENT STRUCTURE

The Master Spec Sheet shall be organized into the following sections:

### 3.1 Front Matter

```markdown
# Master Spec Sheet — Pipeline Orchestration (132 Branches)

> **Fork:** https://github.com/antmikinka/gaia
> **Upstream:** https://github.com/amd/gaia
> **Generated:** <date>
> **Total Entries:** 132
```

### 3.2 Summary Dashboard (Top)

A quick-reference table showing counts by status and by wave:

```markdown
## Summary Dashboard

| Metric | Count |
|--------|-------|
| Total entries | 132 |
| Wave 1 (Foundation) | <count> |
| Wave 2 (Phase 3 Core) | <count> |
| Wave 3 (Phase 4) | <count> |
| Wave 4 (Pipeline Engine) | <count> |
| Wave 5 (Pipeline UI) | <count> |
| Wave 6 (Advanced UI) | <count> |
| Wave 7 (Fixes/Tests/Security) | <count> |
| Wave 8 (Docs/Release/Cleanup) | <count> |
| Wave N (New Entries 88-132) | <count> |
|---|---|
| Status: CREATED | <count> |
| Status: PR_OPEN | <count> |
| Status: PR_MERGED | <count> |
| Status: PENDING | <count> |
```

### 3.3 Wave-Based Grouping (Primary Organization)

Entries are grouped by MERGE_ORDER wave. This is the PRIMARY grouping because merge order reflects the actual execution sequence and dependency chains.

Each wave section contains:

```markdown
## Wave 1 — Foundation (MERGE_ORDER 1-5)

> No dependencies. Independent features. Safe to merge in any order within the wave.

| # | PR-PLAN Key | One-Liner | Category | Merge Order | Depends On | Issue | PR | Branch | Commit | Status |
|---|-------------|-----------|----------|-------------|------------|-------|-----|--------|--------|--------|
| 1 | pdf-bundle-generator | Add PDF bundle generator for 70 doc pages | DOCUMENTATION | 1 | — | [#50](url) | — | [branch](url) | `07b0e88` | CREATED |
| 2 | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |
```

### 3.4 Wave Definitions

| Wave | MERGE_ORDER Range | Label | Description |
|------|-------------------|-------|-------------|
| Wave 1 | 1-5 | Foundation | No dependencies, independent features |
| Wave 2 | 6-15 | Phase 3 Core | Modular architecture, DI, caching, observability |
| Wave 3 | 16-25 | Phase 4 | Health, resilience, data protection, orchestration kernel |
| Wave 4 | 26-40 | Pipeline Engine | Supervisor hierarchy, auto-spawn stages |
| Wave 5 | 41-55 | Pipeline UI | Runner, canvas, wiring, SSE |
| Wave 6 | 56-70 | Advanced UI | Loops, templates, metrics, components |
| Wave 7 | 71-80 | Hardening | Fixes, tests, security |
| Wave 8 | 81-87 | Release | Documentation, release, cleanup |
| Wave N | 1-11 (entries 88-132) | New Batch | Commits from the new batch with their own merge orders |

### 3.5 Category Index (Secondary Reference)

After all wave sections, include a category-based cross-reference:

```markdown
## Category Index

### FEATURE
| # | PR-PLAN Key | Wave | One-Liner | Status |
|---|-------------|------|-----------|--------|

### ARCHITECTURAL_UPGRADE
...

### BUGFIX
...

### DOCUMENTATION
...

### SECURITY
...

### TEST
...

### CHORE
...

### RELEASE
...
```

### 3.6 Dependency Graph Summary

```markdown
## Dependency Graph Summary

> Entries with non-empty DEPENDS_ON. Shows the critical path.

| PR-PLAN Key | Depends On | Wave Gap | Status |
|-------------|------------|----------|--------|
```

### 3.7 Entries Without PRs Yet

```markdown
## Pending PR Creation

> Branches that exist but have no PR opened yet.

| PR-PLAN Key | Branch | Issue | Commit | Wave | Notes |
|-------------|--------|-------|--------|------|-------|
```

---

## 4. TABLE COLUMN DEFINITIONS

### 4.1 Column Specifications

| Column | Name | Source | Format | Example |
|--------|------|--------|--------|---------|
| **#** | Sequential number | Generated | Integer | 1, 2, 3... 132 |
| **PR-PLAN Key** | Unique identifier | PR-PLANS-ALL-FINAL `PR-PLAN:` | kebab-case string | `pdf-bundle-generator` |
| **One-Liner** | Concise summary | Derived from ISSUE_TITLE (first clause, max 80 chars) | Short sentence | Add PDF bundle generator for 70 doc pages |
| **Category** | Classification | Derived from ISSUE_LABELS + ISSUE_TITLE (see §5) | Enum value | DOCUMENTATION |
| **Merge Order** | Dependency wave number | PR-PLANS-ALL-FINAL `MERGE_ORDER:` | Integer 1-87 | 1 |
| **Depends On** | Upstream dependencies | PR-PLANS-ALL-FINAL `DEPENDS_ON:` | Comma-separated keys or `—` | `pr-core-orchestration-kernel` |
| **Issue** | GitHub Issue | EXECUTION-TRACKING + execution-results.json | `[#NN](full_url)` | [#50](https://github.com/antmikinka/gaia/issues/50) |
| **PR** | Pull Request | GitHub API `gh pr list` | `[#NN](full_url)` or `—` | [#37](https://github.com/antmikinka/gaia/pull/37) |
| **Branch** | Fork branch URL | Fork URL + BRANCH_NAME | `[name](full_url)` | [pr-pdf-bundle-generator](https://github.com/antmikinka/gaia/tree/pr-pdf-bundle-generator) |
| **Commit** | Source commit SHA | PR-PLANS-ALL-FINAL `SOURCE_COMMIT:` | `` `SHA` `` | `` `07b0e88` `` |
| **Status** | Current state | Computed (see §6) | Enum | CREATED |

---

## 5. CATEGORY ASSIGNMENT RULES

The executor MUST classify each entry using these rules, applied in priority order (first match wins):

| Priority | Rule | Category |
|----------|------|----------|
| 1 | ISSUE_LABELS contains "security" | SECURITY |
| 2 | ISSUE_TITLE starts with "Release" or ISSUE_LABELS contains "release" | RELEASE |
| 3 | ISSUE_LABELS contains "documentation" or ISSUE_TITLE starts with "docs:" or "Add ... guide" or "Update ... matrix" | DOCUMENTATION |
| 4 | ISSUE_LABELS contains "testing" or ISSUE_TITLE starts with "test(" or "Add ... test" | TEST |
| 5 | ISSUE_TITLE starts with "fix(" or ISSUE_LABELS contains "bugfix" or "fix" | BUGFIX |
| 6 | ISSUE_TITLE starts with "feat(" or ISSUE_LABELS contains "feature" | FEATURE |
| 7 | ISSUE_TITLE starts with "refactor(" or contains "modular architecture" or "upgrade" | ARCHITECTURAL_UPGRADE |
| 8 | ISSUE_LABELS contains "chore" or ISSUE_TITLE starts with "chore(" | CHORE |

### Category Verification

After automatic classification, the executor SHOULD verify:
- SECURITY entries: Any mention of "vulnerability", "race condition", "path traversal", "TOCTOU"
- ARCHITECTURAL_UPGRADE entries: Any mention of "modular", "refactor", "DI", "dependency injection", "architecture"
- FEATURE entries: New functionality, engines, APIs, UI components

---

## 6. STATUS DETERMINATION LOGIC

The executor MUST determine status using this decision tree:

```
1. Query GitHub: does a PR exist for this branch?
   ├── YES — PR exists
   │   ├── Is PR merged? → Status = PR_MERGED
   │   └── Is PR open?   → Status = PR_OPEN
   │
   └── NO — No PR exists
       ├── Does branch exist on fork (branch_ok=true in execution-results.json)?
       │   └── Status = CREATED
       │
       └── Does branch NOT exist (branch_ok=false or status=PARTIAL)?
           ├── Issue exists → Status = PENDING
           └── No issue → Status = PENDING
```

### Status Enum

| Value | Meaning |
|-------|---------|
| PR_MERGED | Pull request has been merged to main |
| PR_OPEN | Pull request is open, awaiting review/merge |
| CREATED | Branch exists on fork, issue exists, but no PR opened yet |
| PENDING | Neither branch nor PR exists yet (only issue or nothing) |

---

## 7. HANDLING ENTRIES WITHOUT PRs

### 7.1 Current State Assessment

Based on EXECUTION-TRACKING.md and execution-results.json:

- **110 branches pushed** (22 pre-existing + 19 v1 + 91 v2)
- **1 partial** (merge-upstream-main — N/A branch)
- **~22 entries** from the original batch that had issues created in v1 but branches never pushed (issues #50-#121 minus the 19 that succeeded)
- **PR creation status unknown** — the executor MUST query GitHub API to determine which branches have PRs

### 7.2 For Entries With Issues Only (No Branch)

| Field | Value |
|-------|-------|
| Issue | Include with link |
| PR | Show `—` (dash) |
| Branch | Show `—` (dash) |
| Commit | Include SHA |
| Status | PENDING |

### 7.3 For Entries With Branch But No PR

| Field | Value |
|-------|-------|
| Issue | Include with link |
| PR | Show `—` (dash) |
| Branch | Include with link to fork |
| Commit | Include SHA |
| Status | CREATED |

### 7.4 For the Merge-Commit Entry (N/A Branch)

The entry `merge-upstream-main` (Issue #173) has branch name "N/A (merge commit)". Handle as:

| Field | Value |
|-------|-------|
| Branch | `N/A — merge commit` |
| Status | PENDING (or PR_MERGED if the merge already happened) |
| Notes | Add a footnote explaining this is a merge commit, not a feature branch |

---

## 8. BATCH_WITH METADATA

Entries with non-empty BATCH_WITH fields should include a note in the wave section header:

```markdown
### Batch Group: pdf-bundle-generator, runtime-artifact-exclusions, docs-debt-cleanup
> These three entries can be merged together as they are independent documentation-only changes.
```

---

## 9. EXECUTION STEPS FOR software-program-manager

The software-program-manager agent should execute in this order:

### Step 1: Parse All PR Plans
Read `plans/PR-PLANS-ALL-FINAL.md` and extract all 87 entries (PR-PLAN blocks separated by `================================================================================`). Parse each field: PR-PLAN, SOURCE_COMMIT, ISSUE_TITLE, ISSUE_BODY, ISSUE_LABELS, BRANCH_NAME, BRANCH_BASE, PR_TITLE, PR_BODY, MERGE_ORDER, DEPENDS_ON, BATCH_WITH.

### Step 2: Parse Remaining Plans
Read `plans/remaining-plans.json` and extract all entries (commits 88-132). Map JSON fields to the same schema as Step 1.

### Step 3: Reconcile Issue Numbers
Read `plans/EXECUTION-TRACKING.md` and `plans/execution-results.json` to map each PR-PLAN key to its issue number and branch_ok status. Build a lookup table: `PR-PLAN key -> {issue_num, issue_url, branch_ok, status}`.

### Step 4: Query GitHub for PR Status
Run `gh pr list --repo antmikinka/gaia --state all --json number,headRefName,title,state,mergedAt` to get all PRs. Map branches to PR numbers. Build a lookup: `branch_name -> {pr_num, pr_url, state, merged}`.

### Step 5: Classify Categories
Apply the category assignment rules (§5) to each entry.

### Step 6: Determine Status
Apply the status determination logic (§6) to each entry.

### Step 7: Generate One-Liners
Extract one-liner from ISSUE_TITLE by:
- Taking the text after the conventional commit prefix (feat:, docs:, fix:, test:, etc.)
- Truncating to 80 characters max
- Converting to sentence case if needed

### Step 8: Build the Document
Generate `plans/MASTER-SPEC-SHEET.md` with:
1. Front matter with fork URL, date, totals
2. Summary Dashboard table
3. Wave sections (1-8, then N) with data tables
4. Category Index
5. Dependency Graph Summary
6. Pending PR Creation section
7. Footnotes for special cases

### Step 9: Verify
- Count all entries = 132
- Verify every PR-PLAN key from source files appears exactly once
- Verify no duplicate commit SHAs
- Verify wave groupings match MERGE_ORDER ranges
- Verify dependency references use correct PR-PLAN keys

---

## 10. OUTPUT FILE SPECIFICATION

| Property | Value |
|----------|-------|
| File Path | `C:\Users\antmi\gaia\plans\MASTER-SPEC-SHEET.md` |
| Format | Markdown (GitHub-flavored) |
| Encoding | UTF-8 |
| Max line length | 120 characters (for table readability) |
| Table alignment | Left-aligned columns |
| Links | Full HTTPS URLs to github.com/antmikinka/gaia |

---

## 11. ENTRY COUNT VERIFICATION

The executor MUST verify the final document contains exactly 132 data rows (excluding headers):

- 87 entries from `PR-PLANS-ALL-FINAL.md`
- 45 entries from `remaining-plans.json` (commits 88-132)
- Total: 132

If the count differs, the executor MUST:
1. Identify missing or duplicate keys
2. Report the discrepancy
3. NOT generate an incomplete document

---

## 12. FOOTNOTES AND SPECIAL CASES

The document should include a footnotes section at the bottom for:

1. **v1 orphan issues:** Issues #50-#121 (minus 19 with branches) exist but have no pushed branches — marked PENDING with note
2. **Merge commit entry:** `merge-upstream-main` has no branch — special handling documented
3. **Wave N entries:** Entries 88-132 have their own MERGE_ORDER (1-11) which is independent of Waves 1-8 — these are grouped separately
4. **Batch entries:** Entries sharing BATCH_WITH can be merged together; note which are batch-compatible

---

## 13. APPENDIX: Expected Wave Entry Counts (Pre-Calculated from Source)

Based on PR-PLANS-ALL-FINAL.md MERGE_ORDER distribution:

| Wave | MERGE_ORDER Range | Expected Count | Notes |
|------|-------------------|-----------------|-------|
| Wave 1 | 1-5 | ~10 | Foundation, doc-only, independent |
| Wave 2 | 6-15 | ~10 | Phase 3 core |
| Wave 3 | 16-25 | ~10 | Phase 4 |
| Wave 4 | 26-40 | ~15 | Pipeline engine |
| Wave 5 | 41-55 | ~15 | Pipeline UI |
| Wave 6 | 56-70 | ~15 | Advanced UI |
| Wave 7 | 71-80 | ~10 | Hardening |
| Wave 8 | 81-87 | ~7 | Release/cleanup |
| Wave N | 1-11 (new) | ~45 | New batch entries |

> These are estimates. The executor MUST count actual entries from source files.

---

*End of Plan — Ready for execution by software-program-manager agent.*
