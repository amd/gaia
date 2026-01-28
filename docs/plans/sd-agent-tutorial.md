# SD Agent Tutorial: Integrating Multi-Modal Endpoints

## Overview

Create a native SDK mixin for Stable Diffusion image generation, demonstrating how to integrate multi-modal endpoints (image, audio, video) into GAIA agents using the mixin pattern.

**Goal:** A developer can follow this tutorial and understand how to:
1. Create a tool mixin following GAIA SDK patterns
2. Register image generation as agent tools
3. Handle base64 image responses from Lemonade Server
4. Combine mixins with the Agent base class

## Implementation

### SDToolsMixin

**File:** `src/gaia/agents/sd/mixin.py`

The mixin provides three tools:
- `generate_image` - Generate an image from a text prompt
- `list_sd_models` - List available SD models
- `get_generation_history` - Get generations from current session

```python
from gaia.agents.base import Agent
from gaia.agents.sd import SDToolsMixin

class MyImageAgent(Agent, SDToolsMixin):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.init_sd(
            base_url="http://localhost:8000",
            output_dir="./generated_images",
            default_model="SD-Turbo",
        )
        self.register_sd_tools()

    def _get_system_prompt(self) -> str:
        return "You are an image generation assistant. Use generate_image to create images."

# Usage
agent = MyImageAgent()
agent.run("Create an image of a sunset over mountains")
```

### Key Components

| Component | Purpose |
|-----------|---------|
| `init_sd()` | Configure SD endpoint URL, output directory, defaults |
| `register_sd_tools()` | Register tools with the agent |
| `_generate_image()` | Internal method that calls Lemonade Server |
| `_save_image()` | Save base64 response as PNG file |
| `sd_health_check()` | Verify Lemonade Server connectivity |

### Tool Registration Pattern

The mixin follows the standard GAIA pattern:

```python
def register_sd_tools(self) -> None:
    from gaia.agents.base.tools import tool

    @tool(
        atomic=True,
        name="generate_image",
        description="Generate an image from a text prompt...",
        parameters={
            "prompt": {"type": "str", "description": "...", "required": True},
            "model": {"type": "str", "description": "...", "required": False},
        },
    )
    def generate_image(prompt: str, model: str = None) -> Dict[str, Any]:
        return self._generate_image(prompt, model)

    # Register with agent
    if hasattr(self, "register_tool"):
        self.register_tool(generate_image)
```

## Deliverables

### 1. SDK Mixin (Complete)

- [x] `src/gaia/agents/sd/__init__.py` - Package exports
- [x] `src/gaia/agents/sd/mixin.py` - SDToolsMixin implementation

### 2. Example (Complete)

- [x] `examples/sd_agent_example.py` - Minimal working example (~80 lines)

### 3. User Guide

**File:** `docs/guides/sd-tutorial.mdx`

Structure:
1. **Introduction** - What you'll build and learn
2. **Prerequisites** - Lemonade Server with SD model
3. **Quick Start** - Run the example in 2 minutes
4. **Understanding the Mixin** - Code walkthrough
5. **Customization** - Extending for your needs
6. **Next Steps** - Link to full SD Agent plan

#### Prerequisites

```bash
# Ensure Lemonade Server is running with SD model
lemonade-server serve --model SD-Turbo

# Verify SD endpoint is available
curl http://localhost:8000/api/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "test", "model": "SD-Turbo", "size": "512x512"}'
```

#### Quick Start

```bash
# Run the example
python examples/sd_agent_example.py

# Or use programmatically
python -c "
from examples.sd_agent_example import ImageAgent
agent = ImageAgent()
result = agent.run('Create an image of a dragon on a cliff at sunset')
print(result)
"
```

### 4. Playbook

**File:** `docs/playbooks/sd-agent/part-1-mixin.mdx`

#### Part 1: Creating a Tool Mixin (45 min)

1. **Understand the Mixin Pattern**
   - What mixins are and why GAIA uses them
   - How tools are registered via decorators
   - How mixins share state with the agent

2. **Set Up the Mixin Structure**
   ```python
   class SDToolsMixin:
       # State variables
       sd_endpoint: str = "http://localhost:8000/api/v1/images/generations"
       sd_output_dir: Path = Path(".gaia/cache/sd/images")

       def init_sd(self, base_url: str, output_dir: str): ...
       def register_sd_tools(self) -> None: ...
       def _generate_image(self, prompt: str, ...) -> Dict: ...
   ```

3. **Register Tools with @tool Decorator**
   - Define tool parameters and descriptions
   - Use `atomic=True` for single-step operations
   - Access mixin state via `self`

4. **Handle the Lemonade Server Response**
   - Parse JSON response with base64 image
   - Decode and save as PNG
   - Return structured result dict

5. **Add Error Handling**
   - Connection errors (server not running)
   - Timeout errors (generation too slow)
   - HTTP errors (invalid parameters)

6. **Test Your Mixin**
   - Create test agent combining Agent + SDToolsMixin
   - Generate test image
   - Verify output file

#### Exercises

1. **Add JPEG support** - Modify `_save_image()` to support format parameter
2. **Add prompt enhancement tool** - Create `enhance_prompt` tool using LLM
3. **Add batch generation** - Create `generate_batch` tool for multiple images

### 5. Tests

**File:** `tests/unit/test_sd_mixin.py`

```python
"""Unit tests for SDToolsMixin."""

import pytest
from unittest.mock import Mock, patch
from pathlib import Path

from gaia.agents.sd import SDToolsMixin


class TestSDToolsMixin:
    """Test SDToolsMixin functionality."""

    def test_init_sd_creates_output_dir(self, tmp_path):
        """Test that init_sd creates the output directory."""
        mixin = SDToolsMixin()
        output_dir = tmp_path / "images"

        mixin.init_sd(output_dir=str(output_dir))

        assert output_dir.exists()
        assert mixin.sd_output_dir == output_dir

    def test_init_sd_sets_defaults(self):
        """Test that init_sd sets default values."""
        mixin = SDToolsMixin()
        mixin.init_sd()

        assert mixin.sd_default_model == "SD-Turbo"
        assert mixin.sd_default_size == "512x512"
        assert mixin.sd_default_steps == 4

    def test_generate_image_validates_model(self):
        """Test that invalid model returns error."""
        mixin = SDToolsMixin()
        mixin.init_sd()

        result = mixin._generate_image("test prompt", model="InvalidModel")

        assert result["status"] == "error"
        assert "Invalid model" in result["error"]

    def test_generate_image_validates_size(self):
        """Test that invalid size returns error."""
        mixin = SDToolsMixin()
        mixin.init_sd()

        result = mixin._generate_image("test prompt", size="999x999")

        assert result["status"] == "error"
        assert "Invalid size" in result["error"]

    @patch("gaia.agents.sd.mixin.requests.post")
    def test_generate_image_success(self, mock_post, tmp_path):
        """Test successful image generation."""
        # Mock response with base64 PNG
        import base64
        fake_png = base64.b64encode(b"fake png data").decode()
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: {"data": [{"b64_json": fake_png}]},
        )
        mock_post.return_value.raise_for_status = Mock()

        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        result = mixin._generate_image("a test image")

        assert result["status"] == "success"
        assert Path(result["image_path"]).exists()
        assert result["model"] == "SD-Turbo"

    @patch("gaia.agents.sd.mixin.requests.post")
    def test_generate_image_connection_error(self, mock_post):
        """Test handling of connection errors."""
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError()

        mixin = SDToolsMixin()
        mixin.init_sd()

        result = mixin._generate_image("test prompt")

        assert result["status"] == "error"
        assert "Cannot connect" in result["error"]
```

**File:** `tests/integration/test_sd_mixin_integration.py`

```python
"""Integration tests for SDToolsMixin (requires Lemonade Server)."""

import pytest
from pathlib import Path

from gaia.agents.base import Agent
from gaia.agents.sd import SDToolsMixin


class TestImageAgent(Agent, SDToolsMixin):
    """Test agent for integration tests."""

    def __init__(self, output_dir: str, **kwargs):
        super().__init__(**kwargs)
        self.init_sd(output_dir=output_dir)
        self.register_sd_tools()

    def _get_system_prompt(self) -> str:
        return "You generate images."


@pytest.mark.integration
@pytest.mark.requires_lemonade
class TestSDMixinIntegration:
    """Integration tests requiring Lemonade Server."""

    def test_generate_simple_image(self, tmp_path):
        """Test generating a simple image."""
        agent = TestImageAgent(output_dir=str(tmp_path))

        result = agent._generate_image(
            prompt="a simple red circle on white background",
            model="SD-Turbo",
            size="512x512",
        )

        assert result["status"] == "success"
        assert Path(result["image_path"]).exists()
        assert result["generation_time_ms"] > 0

    def test_health_check(self):
        """Test SD endpoint health check."""
        agent = TestImageAgent(output_dir="./test_output")

        health = agent.sd_health_check()

        assert health["status"] == "healthy"
        assert "SD-Turbo" in health["models"]
```

## Acceptance Criteria

- [x] `src/gaia/agents/sd/mixin.py` implements SDToolsMixin
- [x] `examples/sd_agent_example.py` demonstrates usage
- [ ] Unit tests pass without Lemonade Server
- [ ] Integration tests pass with Lemonade Server running
- [ ] User guide explains the mixin pattern
- [ ] Playbook can be completed in 45 minutes

## Architecture

```
src/gaia/agents/sd/
├── __init__.py          # Exports SDToolsMixin
└── mixin.py             # SDToolsMixin implementation
    ├── init_sd()        # Configure endpoint and defaults
    ├── register_sd_tools()  # Register @tool decorated functions
    ├── _generate_image()    # Call Lemonade Server endpoint
    ├── _save_image()        # Save base64 PNG to file
    └── sd_health_check()    # Verify server connectivity
```

## Related

- [SD Agent Full Plan](/plans/image-agent) - Complete SD optimization agent with VLM evaluation
- [Agent System Docs](/sdk/core/agent-system) - Base Agent class reference
- [Tool Decorator Docs](/sdk/core/tools) - @tool decorator usage
- [DatabaseMixin](/spec/database-mixin) - Similar mixin pattern example

## Labels

`sdk`, `mixin`, `image-generation`, `tutorial`, `documentation`

## Estimate

- [x] Mixin implementation: 2 hours (complete)
- [x] Example: 1 hour (complete)
- [ ] User guide: 2 hours
- [ ] Playbook: 3 hours
- [ ] Tests: 2 hours

**Remaining: ~7 hours**
