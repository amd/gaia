# Add Stable Diffusion Image Generation Support

Adds comprehensive SD image generation capabilities to GAIA with support for 4 models, defaulting to fast SD-Turbo for quick iteration.

## Overview

This PR introduces `SDToolsMixin` following GAIA's mixin pattern (similar to `DatabaseMixin`, `RAGToolsMixin`) and integrates SD endpoints into `LemonadeClient`. Includes CLI command, comprehensive docs, unit/integration tests, and CI coverage.

## Features

### ✨ New Capabilities

- **4 SD Models Supported:**
  - `SD-Turbo` - Very fast, 512px, 4 steps (default) ⭐⭐
  - `SDXL-Turbo` - Fast stylized, 512px, 4 steps ⭐⭐⭐
  - `SD-1.5` - General purpose, 512px, 20 steps ⭐⭐⭐
  - `SDXL-Base-1.0` - Photorealistic, 1024px, 20 steps ⭐⭐⭐⭐⭐

- **Auto-Settings:** Model-specific defaults automatically applied (size, steps, CFG scale)
- **CLI Command:** `gaia sd` with interactive and single-prompt modes
- **LemonadeClient Integration:** SD methods added to existing client
- **Agent Mixin:** Easy integration into any GAIA agent

### 🎯 Usage Examples

```bash
# Fast with default (SD-Turbo, ~13s)
gaia sd "a robot assistant"

# Fast stylized generation
gaia sd "cyberpunk city" --sd-model SDXL-Turbo

# Interactive mode
gaia sd -i

# Custom settings
gaia sd "portrait" --sd-model SDXL-Base-1.0 --steps 30 --cfg-scale 10.0
```

### 📝 Programmatic Usage

```python
from gaia.agents.base import Agent
from gaia.agents.sd import SDToolsMixin

class ImageAgent(Agent, SDToolsMixin):
    def __init__(self):
        super().__init__()
        self.init_sd()  # Defaults to SD-Turbo (fast)
        self.register_sd_tools()

    def _get_system_prompt(self):
        return "You generate images. Use generate_image tool."

agent = ImageAgent()
agent.run("Create a photorealistic sunset over mountains")
```

## Testing

### ✅ Comprehensive Test Coverage

- **Unit Tests:** 9 tests in `tests/unit/test_sd_mixin.py` (mocked LemonadeClient)
- **Integration Tests:** 5 tests in `tests/integration/test_sd_integration.py` (real server)
- **CI/CD:** Added `gaia sd --help` validation to test_unit.yml
- **Quality Sweep:** `test_sd_model_sweep.py` generated 18 images across all models

**All tests pass:**
```bash
pytest tests/unit/test_sd_mixin.py -v         # 9/9 passed
pytest tests/integration/test_sd_integration.py -v  # 5/5 passed (requires server)
```

### 📊 Performance Results (from sweep)

| Model | Avg Speed | Quality |
|-------|-----------|---------|
| SD-Turbo | 13.4s | Low |
| SDXL-Turbo | 46.6s (512px), 76s (1024px) | Medium |
| SD-1.5 | 87.8s | Medium |
| **SDXL-Base-1.0** | **320s (512px), 527s (1024px)** | **High (photorealistic)** |

## Documentation

### 📚 Complete Documentation Added

- **User Guide:** `docs/guides/sd.mdx` (223 lines)
  - Quick start, examples, configuration
  - All 4 models documented with speeds/settings
  - Prompt engineering tips

- **CLI Reference:** `docs/reference/cli.mdx` (+42 lines)
  - Complete option reference
  - Examples for all models

- **Navigation:** Added to `docs/docs.json` User Guides section

- **Roadmap:** Updated with SD Agent plan and vertical timeline

- **Example:** `examples/sd_agent_example.py` - Working demo agent

## Known Issues

### ⚠️ Lemonade CFG Scale Incompatibility

**Issue:** Lemonade Server generates incorrect images when `cfg_scale=0.0` (despite HuggingFace docs stating Turbo models were trained with CFG disabled).

**Workaround:** Default to `cfg_scale=1.0` for all Turbo models. Documented in code and user guide.

**Evidence:** Created `test_sdxl_turbo_diffusers.py` to compare with reference HuggingFace implementation.

### 🐌 SDXL-Base-1.0 Performance

SDXL-Base-1.0 at 1024x1024 takes **~9 minutes per image** (20 steps with CFG 7.5). This is expected for photorealistic quality but may be too slow for interactive use. SDXL-Turbo recommended for faster results.

## Files Changed

- **Source:** 5 files (+655 lines)
- **Documentation:** 7 files (+800 lines)
- **Tests:** 3 files (+481 lines)
- **Examples/Tools:** 4 files (+1267 lines)

**Total:** 24 files, +3203 lines, -1062 lines

## Migration Notes

None - this is a new feature with no breaking changes.

## Checklist

- [x] Code follows GAIA mixin pattern (inherits from base, uses `@tool` decorator)
- [x] Unit tests added (9 tests, all passing)
- [x] Integration tests added (5 tests, all passing)
- [x] Documentation complete (user guide + CLI reference)
- [x] CI/CD updated (test_unit.yml validates CLI)
- [x] Examples working (sd_agent_example.py)
- [x] No breaking changes
- [x] Follows AMD copyright headers
- [x] All commits squashed/organized logically

## Related Issues

- Implements SD Agent roadmap item from Q1 2026
- Addresses need for local image generation with AMD NPU acceleration
- Foundation for future SD Agent with VLM evaluation (see `docs/plans/image-agent.mdx`)
