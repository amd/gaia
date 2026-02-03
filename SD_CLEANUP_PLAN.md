# SD Feature Cleanup Plan

**Status:** Waiting for CI verification before implementing

## Analysis Summary

**Branch Stats:**
- 127 commits on `kalin/sd` branch
- 44 files changed (+5,558 / -964 lines)
- Major additions: SD agent, SD mixin, VLM mixin, installer improvements

**Key Areas of Iteration:**
1. Model selection (4B → 1.7B → 8B)
2. CI workflow fixes (20+ commits)
3. Installer robustness (15+ commits)
4. Test fixes (10+ commits)

---

## 1. Commit History Cleanup

**Issue:** 127 commits with many iterations, fixes, and "lint" commits

**Recommendation:**
- Squash related commits into logical groups before merge:
  - Core SD feature (agent, mixin, VLM integration)
  - Model optimization (8B decision and reasoning)
  - Installer improvements (version detection, silent install, cleanup)
  - CI/CD fixes (workflow updates, test fixes)
  - Documentation updates

**Implementation:**
```bash
git rebase -i origin/main
# Squash into ~5-8 meaningful commits
```

**Benefits:**
- Cleaner git history
- Easier to understand what changed
- Better for future debugging

---

## 2. Code Simplification Opportunities

### 2.1 System Prompt Optimization

**Location:** `src/gaia/agents/sd/agent.py:118-289`

**Current State:**
- 171 lines of system prompt
- Model-specific sections (SDXL-Turbo, SDXL-Base, SD-1.5, SD-Turbo)
- Research references and enhancement strategies

**Opportunities:**
1. Extract model-specific guidance to separate files/constants
2. Use template strings instead of massive concatenation
3. Consider moving research URLs to comments

**Proposed Structure:**
```python
# src/gaia/agents/sd/prompts.py
BASE_GUIDELINES = """..."""
MODEL_SPECIFIC_PROMPTS = {
    "SDXL-Turbo": """...""",
    "SDXL-Base-1.0": """...""",
    # etc
}

# In agent.py
def _get_system_prompt(self):
    model_prompt = MODEL_SPECIFIC_PROMPTS.get(self.config.sd_model, "")
    return BASE_GUIDELINES + model_prompt + WORKFLOW_INSTRUCTIONS
```

**Benefits:**
- More maintainable
- Easier to test individual prompts
- Cleaner separation of concerns

---

### 2.2 Installer Version Logic

**Location:** `src/gaia/installer/init_command.py`

**Current State:**
- Version checking scattered across multiple methods
- Mix of profile-based and manual version checks
- `--skip-lemonade`, `--force-reinstall` interaction complexity

**Opportunities:**
1. Consolidate version decision logic into single method
2. Create decision matrix for all scenarios
3. Add unit tests for version upgrade logic

**Proposed Refactor:**
```python
def _should_upgrade_lemonade(self, current_ver: str) -> Tuple[bool, str]:
    """
    Determine if Lemonade upgrade is needed.

    Returns:
        (should_upgrade, reason)
    """
    # Check profile requirements
    min_required = INIT_PROFILES[self.profile].get("min_lemonade_version")

    # Decision matrix
    if self.force_reinstall:
        return (True, "Force reinstall requested")
    if self.skip_lemonade:
        return (False, "Skip flag set")
    if self.remote:
        return (False, "Remote mode")
    if pkg_version.parse(current_ver) >= pkg_version.parse(min_required):
        return (False, f"v{current_ver} sufficient for profile '{self.profile}'")

    return (True, f"Profile '{self.profile}' requires v{min_required}+")
```

---

### 2.3 Workflow Duplication

**Locations:**
- `.github/workflows/test_sd.yml`
- `.github/workflows/test_lemonade_server.yml`

**Current State:**
- Both have identical "Kill Stuck Processes" step
- Similar gaia init patterns

**Opportunities:**
1. Create shared composite action `.github/actions/cleanup-msi`
2. Create shared composite action `.github/actions/init-with-profile`

**Proposed Structure:**
```yaml
# .github/actions/cleanup-msi/action.yml
name: Cleanup MSI and Lemonade Processes
description: Kills stuck msiexec and lemonade-server processes
runs:
  using: composite
  steps:
    - shell: powershell
      run: |
        # Cleanup script here...

# Usage in workflows:
- uses: ./.github/actions/cleanup-msi
- run: gaia init --profile sd --yes --verbose
```

**Benefits:**
- DRY principle
- Single source of truth for cleanup logic
- Easier to update cleanup behavior

---

## 3. Testing Improvements

### 3.1 Missing Test Coverage

**Gaps Identified:**
- [ ] Test story text file creation (new feature)
- [ ] Test create_story_from_last_image with optional image_path
- [ ] Test model display in console output
- [ ] Test profile-specific version upgrade logic
- [ ] Test silent installer with various scenarios

**Proposed Additions:**
```python
# tests/unit/test_sd_agent.py (new file)
def test_story_file_creation(tmp_path):
    """Test that story is saved to .txt file"""
    # ...

def test_create_story_with_image_path():
    """Test optional image_path parameter"""
    # ...

# tests/integration/test_init_command.py
def test_profile_version_requirements():
    """Test SD requires v9.2.0, others work with v9.0.4"""
    # ...
```

---

### 3.2 Test Organization

**Current State:**
- Integration tests in `tests/integration/test_sd_integration.py` (196 lines)
- Unit tests in `tests/unit/test_sd_mixin.py` (299 lines)
- Model sweep in `tests/test_sd_model_sweep.py` (281 lines)

**Opportunities:**
- Group by feature area (mixin, agent, CLI)
- Add test for each new init_command feature
- Document what each test validates

---

## 4. Documentation Consolidation

### 4.1 Model Size Discrepancies

**Fixed During Session:**
- Old: "~8.4GB" → Correct: "~15GB"
- Updated in multiple files

**Verification Needed:**
- [ ] Check all docs mention correct sizes
- [ ] Verify `gaia init --help` shows 15GB
- [ ] Ensure consistency across playbooks, guides, SDK docs

---

### 4.2 Documentation Completeness

**Files Updated:**
- `docs/guides/sd.mdx` ✅
- `docs/playbooks/sd-agent/index.mdx` ✅
- `docs/sdk/mixins/tool-mixins.mdx` ✅
- `docs/reference/cli.mdx` ✅

**Gaps to Review:**
- [ ] Add troubleshooting section for MSI installation issues
- [ ] Document --skip-lemonade and when to use it
- [ ] Add section on profile version requirements
- [ ] Document story text file feature

---

## 5. Code Quality and Consistency

### 5.1 Error Handling Patterns

**Observations:**
- Some methods return `{"status": "error", "error": "..."}` (SD mixin)
- Some raise exceptions (installer)
- Some return `InstallResult` objects

**Recommendation:**
- Document error handling strategy for each layer
- Ensure consistency within each module
- SD mixin pattern is good (dict results for tools)
- Installer pattern is good (InstallResult for operations)

---

### 5.2 Import Cleanup

**Fixed During Session:**
- Removed unused `os` import from sd/agent.py
- Added missing imports for cross-platform support

**Verification:**
- [x] All imports validated in lint.py
- [x] SD/VLM added to critical imports

---

## 6. Configuration Management

### 6.1 Profile Definitions

**Location:** `src/gaia/installer/init_command.py:39-80`

**Current State:** ✅ Well-structured
```python
INIT_PROFILES = {
    "sd": {
        "description": "...",
        "models": [...],
        "approx_size": "~15 GB",
        "min_lemonade_version": "9.2.0",
    },
    # ...
}
```

**Recommendations:**
- [x] Already centralized ✅
- [ ] Consider adding `max_steps` default per profile
- [ ] Add `ctx_size` requirement per profile

---

### 6.2 SDAgentConfig

**Location:** `src/gaia/agents/sd/agent.py:22-46`

**Current State:**
```python
@dataclass
class SDAgentConfig:
    sd_model: str = "SDXL-Turbo"
    output_dir: str = ".gaia/cache/sd/images"
    model_id: str = "Qwen3-8B-GGUF"
    max_steps: int = 10
    ctx_size: int = 8192
    # ... 12 total fields
```

**Recommendation:**
- Already clean with dataclass
- Good defaults
- Consider validation in `__post_init__` if adding more complexity

---

## 7. Workflow Simplification

### 7.1 Current Workflow Complexity

**SD Test Workflow Steps:**
1. Checkout
2. Kill stuck processes (MSI + lemonade-server)
3. Setup Python
4. Initialize SD (gaia init --profile sd --yes --verbose)
5. Run tests
6. Cleanup

**Opportunities:**
- [ ] Extract "Kill Stuck Processes" to composite action
- [ ] Consider caching models between runs (may already work via HuggingFace cache)
- [ ] Add timeout annotations to each step

---

### 7.2 Lemonade Smoke Test

**Current:** Uses `gaia init --profile minimal`

**Recommendation:**
- Already simplified ✅
- Consider renaming to "Init Command Integration Test" (tests gaia init, not just Lemonade)

---

## 8. Performance Optimizations

### 8.1 Model Loading

**Current Behavior:**
- SD agent loads LLM model in `__init__` before Agent base class
- Prevents context size warnings

**Potential Issue:**
- If user doesn't use SD features, model still loaded

**Recommendation:**
- Current approach is correct for eager initialization
- Lazy loading would complicate the code
- Keep as-is

---

### 8.2 Story File I/O

**Location:** `src/gaia/agents/sd/agent.py:355-375`

**Current:**
```python
with open(story_path, "w", encoding="utf-8") as f:
    f.write("=" * 80 + "\n")
    f.write("STORY\n")
    # ...
```

**Opportunities:**
- Use template or format string
- Add error handling for file write failures
- Consider making format configurable (markdown, txt, json)

**Proposed:**
```python
def _save_story_file(self, story_path, story_text, description):
    """Save story and description to formatted text file."""
    template = """
{sep}
STORY
{sep}

{story}

{sep}
IMAGE DESCRIPTION
{sep}

{description}
""".strip()

    content = template.format(
        sep="=" * 80,
        story=story_text,
        description=description
    )

    try:
        with open(story_path, "w", encoding="utf-8") as f:
            f.write(content)
    except IOError as e:
        logger.warning(f"Failed to save story file: {e}")
        # Continue anyway - story is in tool result
```

---

## 9. Logging Improvements

### 9.1 MSI Install Logging

**Current:** Adds verbose logging (`/l*v`) to all MSI operations

**Recommendation:**
- Only add verbose logging in `--verbose` mode
- Reduces file I/O in normal operation

**Implementation:**
```python
if self.verbose or silent:  # Always log silent installs for debugging
    cmd.extend(["/l*v", str(msi_log)])
```

---

### 9.2 Debug vs Info Logging

**Found During Session:**
- Some log.info should be log.debug for CI quietness
- Already fixed for MSI command logging

**Verification:**
- [x] Review all log.info calls added in this branch
- [ ] Ensure appropriate log levels throughout

---

## 10. Edge Cases and Error Recovery

### 10.1 Model Download Retry

**Location:** `src/gaia/installer/init_command.py:930-979`

**Current:** Auto-detects validation errors and retries

**Recommendation:**
- Already well-implemented ✅
- Consider adding retry limit (currently 1 retry)
- Add exponential backoff if implementing more retries

---

### 10.2 Server Startup in CI

**Location:** `src/gaia/installer/init_command.py:790-833`

**Current:** Auto-starts server with 30s timeout

**Recommendations:**
- Add retry logic if first start fails
- Check for port conflicts before starting
- Better error messages if startup fails

---

## 11. Documentation Tasks

### 11.1 Add Troubleshooting Guides

**Files to Update:**
- `docs/reference/troubleshooting.mdx`
- `docs/guides/sd.mdx`

**Topics to Cover:**
1. MSI installation issues (based on what we learned)
   - "Another installation in progress" (error 1618)
   - How to manually clean up stuck processes
   - How to check MSI log files

2. Model download failures
   - Size mismatch errors
   - Auto-retry behavior
   - Manual recovery steps

3. Version compatibility
   - Why SD requires v9.2.0
   - How to check version
   - How to upgrade manually

---

### 11.2 Update Examples

**File:** `examples/sd_agent_example.py`

**Recommendations:**
- [x] Updated docstring ✅
- [ ] Add example showing story file feature
- [ ] Show VLM integration example
- [ ] Document the 8B model choice

---

## 12. Testing Strategy

### 12.1 Integration Test Enhancements

**File:** `tests/integration/test_sd_integration.py`

**Current Coverage:**
- ✅ Basic image generation
- ✅ Model validation
- ✅ Health checks
- ✅ History tracking

**Missing:**
- [ ] Story generation (VLM integration)
- [ ] Story file verification
- [ ] Model display in output
- [ ] Error recovery (corrupted downloads)

---

### 12.2 Unit Test Completeness

**File:** `tests/unit/test_sd_mixin.py`

**Good Coverage:**
- ✅ Generate image variations
- ✅ Connection errors
- ✅ Invalid parameters
- ✅ History tracking

**Could Add:**
- [ ] Test all 4 SD models
- [ ] Test prompt enhancement patterns
- [ ] Test file path handling (cross-platform)

---

## 13. CI/CD Optimization

### 13.1 Workflow Efficiency

**Current Timing (estimated):**
- MSI install: ~10s (when working)
- Model download (first run): ~15 min (15GB)
- Model download (cached): ~5s (validation only)
- SD tests: ~1-2 min

**Opportunities:**
1. Ensure HuggingFace cache is preserved between runs (check runner config)
2. Run lint in parallel with other fast checks
3. Skip model verification in CI (trust download checksums)

---

### 13.2 Composite Actions

**Create:**
```yaml
# .github/actions/cleanup-runner/action.yml
# Shared cleanup logic for all Windows workflows

# .github/actions/init-gaia-profile/action.yml
# Parameterized gaia init with profile
inputs:
  profile:
    required: true
  verbose:
    default: 'true'
```

---

## 14. Future Enhancements (Defer to Later)

**Not for this PR, but document as follow-ups:**

1. **Prompt Caching**
   - Cache enhanced prompts to avoid LLM call on identical inputs
   - Save to .gaia/cache/sd/prompts.json

2. **Batch Generation**
   - Support generating multiple variations in single call
   - `gaia sd "robot kitten" --count 5`

3. **Image History Browser**
   - CLI command to browse generation history
   - `gaia sd history --last 10`

4. **VLM Model Selection**
   - Make VLM model configurable (currently hardcoded Qwen3-VL-4B)
   - Support different VLMs for different use cases

5. **Advanced Prompt Engineering**
   - Negative prompts support
   - LoRA/embedding support
   - Scheduler selection

---

## 15. Cleanup Priority Matrix

### High Priority (Before Merge)
1. ✅ Fix MSI hang (kill lemonade-server processes) - **DONE**
2. ✅ All lint checks passing - **DONE**
3. ✅ Cross-platform file opening - **DONE**
4. ✅ Remove unused parameters - **DONE**
5. [ ] Squash commits into logical groups
6. [ ] Verify all documentation is accurate

### Medium Priority (Can be Follow-up PR)
1. [ ] Extract system prompt to separate file
2. [ ] Create composite GitHub actions
3. [ ] Add missing test coverage (story files, VLM integration)
4. [ ] Consolidate version upgrade logic
5. [ ] Add troubleshooting documentation

### Low Priority (Future)
1. [ ] Prompt caching
2. [ ] Batch generation
3. [ ] History browser
4. [ ] Configurable VLM model

---

## 16. Verification Checklist

**Before Squashing/Merging:**
- [ ] CI passes all checks (currently waiting)
- [ ] Local testing: `gaia sd "create a robot kitten with story"`
- [ ] Local testing: `gaia init --profile sd` (fresh install)
- [ ] Documentation review (all model references, sizes correct)
- [ ] No commented-out code
- [ ] All TODOs addressed or documented

**After Squashing:**
- [ ] Commit messages are descriptive
- [ ] Each commit is self-contained
- [ ] No "lint" or "fix" commits in final history

---

## 17. Estimated Cleanup Effort

**Commit Squashing:** 30-45 minutes
**System Prompt Refactor:** 1-2 hours
**Test Coverage Additions:** 2-3 hours
**Composite Actions:** 1 hour
**Documentation Updates:** 1-2 hours

**Total:** 5-8 hours for complete cleanup

**Recommendation:**
- Do commit squashing now (before merge)
- Defer code refactoring to follow-up PRs (not blocking)
- Add tests in follow-up (feature works, tests are insurance)

---

## 18. Risk Assessment

### What Could Break During Cleanup

**Low Risk:**
- Commit squashing (doesn't change code)
- Documentation updates
- Adding tests

**Medium Risk:**
- Extracting system prompt (could break prompt behavior)
- Refactoring installer logic (complex state machine)

**Recommendation:**
- Save aggressive refactoring for after merge
- Focus on non-invasive cleanup now

---

## Next Steps

1. **Wait for CI to pass** with MSI fix
2. **Verify locally:** `gaia init --profile sd --yes` works on fresh machine
3. **Review this plan** and decide which items to tackle before merge
4. **Squash commits** into logical groups (recommended)
5. **Final documentation pass**
6. **Merge!**
7. Create follow-up PRs for bigger refactorings

---

## Notes

- The 8B model decision is solid (justified by better instruction following)
- Story text file feature is valuable
- Silent installer with auto-upgrade is robust
- Cross-platform support is complete
- Most cleanup can be deferred to follow-ups
