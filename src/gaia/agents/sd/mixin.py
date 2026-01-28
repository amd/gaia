"""
SDToolsMixin - Stable Diffusion image generation tools for GAIA agents.

Provides tools to generate images using the Lemonade Server SD endpoint.
Supports SD-Turbo and SDXL-Turbo models with AMD NPU/GPU acceleration.

Example:
    from gaia.agents.base import Agent
    from gaia.agents.sd import SDToolsMixin

    class MyImageAgent(Agent, SDToolsMixin):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.init_sd()  # Initialize with defaults
            self.register_sd_tools()

        def _get_system_prompt(self) -> str:
            return '''You are an image generation assistant.
            Use generate_image to create images from text descriptions.'''

    # Usage
    agent = MyImageAgent()
    agent.run("Create an image of a sunset over mountains")
"""

import base64
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class SDToolsMixin:
    """
    Mixin providing Stable Diffusion image generation tools.

    Tools provided:
    - generate_image: Generate an image from a text prompt
    - list_sd_models: List available SD models
    - get_generation_history: Get recent generations from this session

    Attributes:
        sd_endpoint: URL to Lemonade Server SD API
        sd_output_dir: Directory to save generated images
        sd_default_model: Default model (SD-Turbo or SDXL-Turbo)
        sd_generations: List of generations from this session
    """

    # Mixin state (set by init_sd)
    sd_endpoint: str = "http://localhost:8000/api/v1/images/generations"
    sd_output_dir: Path = Path(".gaia/cache/sd/images")
    sd_default_model: str = "SD-Turbo"
    sd_default_size: str = "512x512"
    sd_default_steps: int = 4
    sd_generations: List[Dict[str, Any]] = []

    # Supported configurations
    SD_MODELS = ["SD-Turbo", "SDXL-Turbo"]
    SD_SIZES = ["512x512", "768x768", "1024x1024"]

    def init_sd(
        self,
        base_url: str = "http://localhost:8000",
        output_dir: Optional[str] = None,
        default_model: str = "SD-Turbo",
        default_size: str = "512x512",
        default_steps: int = 4,
    ) -> None:
        """
        Initialize SD tools configuration.

        Args:
            base_url: Lemonade Server base URL
            output_dir: Directory to save generated images (default: .gaia/cache/sd/images)
            default_model: Default SD model (SD-Turbo or SDXL-Turbo)
            default_size: Default image size (512x512, 768x768, 1024x1024)
            default_steps: Default inference steps (4 for Turbo models)
        """
        self.sd_endpoint = f"{base_url}/api/v1/images/generations"
        self.sd_output_dir = Path(output_dir) if output_dir else Path(".gaia/cache/sd/images")
        self.sd_output_dir.mkdir(parents=True, exist_ok=True)

        self.sd_default_model = default_model
        self.sd_default_size = default_size
        self.sd_default_steps = default_steps
        self.sd_generations = []

        logger.info(f"SD tools initialized: endpoint={self.sd_endpoint}, output={self.sd_output_dir}")

    def register_sd_tools(self) -> None:
        """Register Stable Diffusion image generation tools."""
        from gaia.agents.base.tools import tool

        @tool(
            atomic=True,
            name="generate_image",
            description="Generate an image from a text prompt using Stable Diffusion. "
            "Returns the path to the saved image file.",
            parameters={
                "prompt": {
                    "type": "str",
                    "description": "Text description of the image to generate. Be detailed for best results.",
                    "required": True,
                },
                "model": {
                    "type": "str",
                    "description": "SD model to use: SD-Turbo (fast, 512px) or SDXL-Turbo (quality, 1024px)",
                    "required": False,
                },
                "size": {
                    "type": "str",
                    "description": "Image dimensions: 512x512, 768x768, or 1024x1024",
                    "required": False,
                },
                "steps": {
                    "type": "int",
                    "description": "Inference steps (4 recommended for Turbo models)",
                    "required": False,
                },
                "seed": {
                    "type": "int",
                    "description": "Random seed for reproducibility (optional)",
                    "required": False,
                },
            },
        )
        def generate_image(
            prompt: str,
            model: Optional[str] = None,
            size: Optional[str] = None,
            steps: Optional[int] = None,
            seed: Optional[int] = None,
        ) -> Dict[str, Any]:
            """Generate an image from a text prompt using Stable Diffusion."""
            return self._generate_image(prompt, model, size, steps, seed)

        @tool(
            atomic=True,
            name="list_sd_models",
            description="List available Stable Diffusion models and their characteristics.",
        )
        def list_sd_models() -> Dict[str, Any]:
            """List available SD models."""
            return {
                "models": [
                    {
                        "name": "SD-Turbo",
                        "description": "Fast generation, optimized for 512x512",
                        "recommended_steps": 4,
                        "recommended_size": "512x512",
                    },
                    {
                        "name": "SDXL-Turbo",
                        "description": "Higher quality, optimized for 1024x1024",
                        "recommended_steps": 4,
                        "recommended_size": "1024x1024",
                    },
                ],
                "default_model": self.sd_default_model,
            }

        @tool(
            atomic=True,
            name="get_generation_history",
            description="Get the history of images generated in this session.",
            parameters={
                "limit": {
                    "type": "int",
                    "description": "Maximum number of generations to return (default: 10)",
                    "required": False,
                }
            },
        )
        def get_generation_history(limit: int = 10) -> Dict[str, Any]:
            """Get recent generations from this session."""
            recent = self.sd_generations[-limit:] if self.sd_generations else []
            return {
                "total_generations": len(self.sd_generations),
                "showing": len(recent),
                "generations": recent,
            }

        # Register tools with the agent
        if hasattr(self, "register_tool"):
            self.register_tool(generate_image)
            self.register_tool(list_sd_models)
            self.register_tool(get_generation_history)

    def _generate_image(
        self,
        prompt: str,
        model: Optional[str] = None,
        size: Optional[str] = None,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Internal method to generate an image via Lemonade Server SD endpoint.

        Args:
            prompt: Text description of the image
            model: SD model (defaults to sd_default_model)
            size: Image size (defaults to sd_default_size)
            steps: Inference steps (defaults to sd_default_steps)
            seed: Random seed for reproducibility

        Returns:
            Dict with image_path, prompt, model, size, seed, and generation_time_ms
        """
        # Apply defaults
        model = model or self.sd_default_model
        size = size or self.sd_default_size
        steps = steps or self.sd_default_steps

        # Validate parameters
        if model not in self.SD_MODELS:
            return {
                "status": "error",
                "error": f"Invalid model '{model}'. Choose from: {self.SD_MODELS}",
            }
        if size not in self.SD_SIZES:
            return {
                "status": "error",
                "error": f"Invalid size '{size}'. Choose from: {self.SD_SIZES}",
            }

        # Build request payload
        payload = {
            "prompt": prompt,
            "model": model,
            "size": size,
            "n": 1,
            "response_format": "b64_json",
        }
        if seed is not None:
            payload["seed"] = seed

        logger.info(f"Generating image: prompt='{prompt[:50]}...', model={model}, size={size}")

        try:
            import time

            start_time = time.time()

            # Call Lemonade Server SD endpoint
            response = requests.post(
                self.sd_endpoint,
                json=payload,
                timeout=120,  # SD generation can take time
            )
            response.raise_for_status()

            generation_time_ms = int((time.time() - start_time) * 1000)

            # Parse response
            data = response.json()
            image_b64 = data["data"][0]["b64_json"]
            image_bytes = base64.b64decode(image_b64)

            # Generate filename
            image_path = self._save_image(prompt, image_bytes, model)

            # Compute hash for deduplication
            image_hash = hashlib.sha256(image_bytes).hexdigest()[:16]

            # Build result
            result = {
                "status": "success",
                "image_path": str(image_path),
                "prompt": prompt,
                "model": model,
                "size": size,
                "steps": steps,
                "seed": seed,
                "image_hash": image_hash,
                "generation_time_ms": generation_time_ms,
            }

            # Track in session history
            self.sd_generations.append(
                {
                    **result,
                    "created_at": datetime.now().isoformat(),
                }
            )

            logger.info(f"Image generated: {image_path} ({generation_time_ms}ms)")
            return result

        except requests.exceptions.ConnectionError:
            error_msg = f"Cannot connect to Lemonade Server at {self.sd_endpoint}. Is it running?"
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}

        except requests.exceptions.Timeout:
            error_msg = "Image generation timed out after 120 seconds"
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}

        except requests.exceptions.HTTPError as e:
            error_msg = f"SD endpoint error: {e.response.status_code} - {e.response.text}"
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}

        except Exception as e:
            error_msg = f"Image generation failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {"status": "error", "error": error_msg}

    def _save_image(self, prompt: str, image_bytes: bytes, model: str) -> Path:
        """
        Save image bytes to file with generated filename.

        Args:
            prompt: Original prompt (used for filename)
            image_bytes: PNG image data
            model: Model used (included in filename)

        Returns:
            Path to saved image file
        """
        # Create safe filename from prompt
        safe_prompt = re.sub(r"[^\w\s-]", "", prompt[:40]).strip().replace(" ", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_prompt}_{model}_{timestamp}.png"

        image_path = self.sd_output_dir / filename
        image_path.write_bytes(image_bytes)

        return image_path

    def sd_health_check(self) -> Dict[str, Any]:
        """
        Check if Lemonade Server SD endpoint is available.

        Returns:
            Dict with status, endpoint, and available models
        """
        try:
            # Simple health check - try to reach the server
            response = requests.get(
                self.sd_endpoint.replace("/api/v1/images/generations", "/health"),
                timeout=5,
            )
            if response.ok:
                return {
                    "status": "healthy",
                    "endpoint": self.sd_endpoint,
                    "models": self.SD_MODELS,
                    "output_dir": str(self.sd_output_dir),
                }
        except Exception:
            pass

        return {
            "status": "unavailable",
            "endpoint": self.sd_endpoint,
            "error": "Cannot connect to Lemonade Server",
        }
