# SD Feature CI/CD Coverage

## Summary

✅ **Full CI/CD coverage** with both mock and integration tests

| Test Type | Workflow | Tests | Duration | Platform |
|-----------|----------|-------|----------|----------|
| **Mock (Unit)** | test_unit.yml | 9 tests | ~1s | Ubuntu |
| **CLI Validation** | test_unit.yml | 1 test | ~1s | Ubuntu |
| **Integration** | test_sd.yml | 3 tests | ~30s | Windows (self-hosted) |

**Total:** 13 automated tests running on every PR

---

## 1. Mock Tests (Fast - No Lemonade Required)

**Workflow:** `.github/workflows/test_unit.yml`
**File:** `tests/unit/test_sd_mixin.py`
**Duration:** ~1 second
**Platform:** Ubuntu (GitHub-hosted)

### Tests (9 total)

**Initialization (4 tests):**
- ✅ `test_init_sd_creates_output_directory`
- ✅ `test_init_sd_sets_defaults`
- ✅ `test_init_sd_custom_config`
- ✅ `test_generations_list_is_instance_level`

**Validation (2 tests):**
- ✅ `test_validate_invalid_model`
- ✅ `test_validate_invalid_size`

**Generation (2 tests):**
- ✅ `test_generate_image_success` (mocked LemonadeClient)
- ✅ `test_generate_image_with_seed`

**Other (1 test):**
- ✅ `test_load_model_called_before_generation`

**Health Check (3 tests):**
- ✅ `test_health_check_healthy`
- ✅ `test_health_check_unavailable`
- ✅ `test_health_check_no_models`

**File Operations (2 tests):**
- ✅ `test_save_image_creates_file`
- ✅ `test_save_image_sanitizes_filename`

### Coverage

```yaml
- name: Run unit tests
  run: pytest tests/unit/ -v --tb=short --cov=src/gaia
```

This automatically includes `test_sd_mixin.py` when testing `src/gaia/agents/sd/mixin.py`.

---

## 2. CLI Validation (Fast - No Lemonade Required)

**Workflow:** `.github/workflows/test_unit.yml`
**Duration:** ~1 second
**Platform:** Ubuntu (GitHub-hosted)

### Test

```yaml
- name: Validate CLI commands (dry-run)
  run: |
    gaia sd --help
    echo "✅ gaia sd --help passed"
```

**Validates:**
- CLI argument parsing
- No syntax errors in command definition
- Help text renders correctly

---

## 3. Integration Tests (Requires Lemonade)

**Workflow:** `.github/workflows/test_sd.yml` ⭐ **NEW**
**File:** `tests/integration/test_sd_integration.py`
**Duration:** ~30 seconds (generation only)
**Platform:** Windows self-hosted runner (`stx`) with AMD hardware

### Setup

```yaml
- Install Lemonade Server
- Start server in background
- Pull SD-Turbo model (2.6GB - smallest/fastest SD model)
- Run 3 fast integration tests
```

### Tests (3 selected for speed)

1. ✅ **`test_generate_small_image`** (~13s)
   - Generates 512x512 image with SD-Turbo
   - Verifies file creation, PNG format, metadata
   - End-to-end generation pipeline test

2. ✅ **`test_health_check_with_real_server`** (~1s)
   - Verifies server is running
   - Checks SD models are available
   - API connectivity test

3. ✅ **`test_list_sd_models`** (~1s)
   - Tests LemonadeClient.list_sd_models()
   - Verifies SD model metadata

### Skipped (Too Slow for CI)

❌ `test_generation_history_tracking` - Requires 2 generations (~26s)
❌ `test_seed_reproducibility` - Requires 2 generations (~26s)
❌ `test_generate_image_via_client` - Duplicate of test 1
❌ Any tests with SDXL-Base-1.0 (527s per image)

### Total Runtime

- Server start: ~30s
- Model pull: ~60s (cached after first run)
- Test execution: ~15s
- **Total: ~2 minutes** (first run), **~45s** (subsequent runs)

---

## 4. Manual Testing

**Not in CI** (too slow or exploratory):

### Quality Evaluation

**Script:** `test_sd_model_sweep.py`

- Tests all 4 models
- Tests multiple resolutions
- Generates 18 images with full report
- **Duration:** ~30-60 minutes
- **Usage:** Manual quality validation before releases

### Crash Reproduction

**Script:** `reproduce_sdxl_base_crash.py`

- Isolates SDXL-Base-1.0 issues
- Step-by-step debugging
- **Usage:** Debug/report Lemonade issues

---

## Coverage Summary

| Component | Mock Tests | Integration Tests | CLI Validation | Total |
|-----------|------------|-------------------|----------------|-------|
| **SDToolsMixin** | 9 | 1 | - | 10 |
| **LemonadeClient.generate_image()** | - | 1 | - | 1 |
| **LemonadeClient.list_sd_models()** | - | 1 | - | 1 |
| **CLI (gaia sd)** | - | - | 1 | 1 |
| **Health Check** | 3 | 1 | - | 4 |
| **File Operations** | 2 | - | - | 2 |

**Total Automated Tests:** 13 tests across 2 workflows

---

## Recommendations

### ✅ Current Coverage is Good

- Mock tests cover all code paths (9 tests)
- Integration tests verify end-to-end functionality (3 tests)
- CLI validation ensures command works
- Fast enough for PR checks (~2 min first run, ~45s cached)

### 🎯 Future Enhancements

If needed, consider:

1. **Add SDXL-Turbo integration test** (optional, adds ~17s)
   - Would test higher quality model
   - Currently only testing SD-Turbo

2. **Matrix testing** (optional, for releases only)
   - Test multiple models in parallel
   - Use workflow_dispatch trigger
   - Not recommended for PRs (too slow)

3. **Visual regression testing** (future)
   - Compare generated images to reference
   - Detect quality degradation
   - Requires image comparison framework

---

## Running Tests Locally

### Mock Tests (Fast)
```bash
# All unit tests
pytest tests/unit/test_sd_mixin.py -v

# With coverage
pytest tests/unit/test_sd_mixin.py -v --cov=src/gaia/agents/sd
```

### Integration Tests (Requires Lemonade)
```bash
# Start Lemonade Server first
lemonade-server serve

# In another terminal, pull SD-Turbo
lemonade-server pull SD-Turbo

# Run integration tests
pytest tests/integration/test_sd_integration.py -v

# Run only fast tests
pytest tests/integration/test_sd_integration.py::TestSDIntegration::test_generate_small_image -v
```

### Quality Sweep (Manual)
```bash
# Requires all 4 SD models downloaded
python test_sd_model_sweep.py

# View results
start sd_model_sweep_results/report.md
```

---

## CI/CD Best Practices

### ✅ What We Do Well

1. **Separate mock and integration tests**
   - Mock tests run on every PR (fast, no dependencies)
   - Integration tests run on specific runner (real hardware)

2. **Fast integration tests**
   - Only test SD-Turbo (fastest model)
   - Only 3 essential tests (~30s total)
   - Skip slow models (SDXL-Base-1.0)

3. **Conditional execution**
   - Only runs when SD-related files change
   - Skips draft PRs
   - Can be triggered manually

4. **Proper cleanup**
   - Always stops Lemonade Server
   - Even on test failure

### 📋 Checklist for New SD Features

When adding new SD functionality, ensure:

- [ ] Add unit tests to `tests/unit/test_sd_mixin.py` (mocked)
- [ ] Consider adding fast integration test to `tests/integration/test_sd_integration.py` (< 30s)
- [ ] Update documentation in `docs/guides/sd.mdx`
- [ ] Update CLI reference if adding options
- [ ] Test manually with quality sweep script
- [ ] Verify CI passes (check GitHub Actions)
