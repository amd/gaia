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
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.logger import get_logger

logger = get_logger(__name__)


class SDToolsMixin:
    """
    Mixin providing Stable Diffusion image generation tools.

    Tools provided:
    - generate_image: Generate an image from a text prompt
    - list_sd_models: List available SD models
    - get_generation_history: Get recent generations from this session

    Attributes:
        sd_client: LemonadeClient instance for API calls
        sd_output_dir: Directory to save generated images
        sd_default_model: Default model (SD-Turbo or SDXL-Turbo)
        sd_generations: List of generations from this session
    """

    # Supported configurations (class constants)
    SD_MODELS = ["SD-Turbo", "SDXL-Turbo"]
    SD_SIZES = ["512x512", "768x768", "1024x1024"]

    # Instance state (initialized by init_sd)
    sd_client: LemonadeClient
    sd_output_dir: Path
    sd_default_model: str
    sd_default_size: str
    sd_default_steps: int
    sd_generations: List[Dict[str, Any]]

    def init_sd(
        self,
        base_url: str = "http://localhost:8000",
        output_dir: Optional[str] = None,
        default_model: str = "SDXL-Turbo",
        default_size: str = "1024x1024",
        default_steps: int = 4,
    ) -> None:
        """
        Initialize SD tools configuration. Must be called before using SD tools.

        Args:
            base_url: Lemonade Server base URL
            output_dir: Directory to save generated images (default: .gaia/cache/sd/images)
            default_model: Default SD model (SD-Turbo or SDXL-Turbo)
            default_size: Default image size (512x512, 768x768, 1024x1024)
            default_steps: Default inference steps (4 for Turbo models)

        Example:
            self.init_sd(
                base_url="http://localhost:8000",
                output_dir="./my_images",
                default_model="SDXL-Turbo",
            )
        """
        # Create LemonadeClient for API calls
        self.sd_client = LemonadeClient(base_url=base_url, verbose=False)

        self.sd_output_dir = Path(output_dir) if output_dir else Path(".gaia/cache/sd/images")
        self.sd_output_dir.mkdir(parents=True, exist_ok=True)

        self.sd_default_model = default_model
        self.sd_default_size = default_size
        self.sd_default_steps = default_steps
        self.sd_generations = []  # Instance-level list for session history

        logger.info(f"SD tools initialized: endpoint={self.sd_client.base_url}/images/generations, output={self.sd_output_dir}")

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
        import time

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

        logger.info(f"Generating image: prompt='{prompt[:50]}...', model={model}, size={size}")

        try:
            # Ensure model is loaded before generation
            logger.info(f"Loading SD model: {model}")
            try:
                self.sd_client.load_model(model, auto_download=True, prompt=False, timeout=600)
            except LemonadeClientError as e:
                # Model might already be loaded, continue
                if "already loaded" not in str(e).lower():
                    logger.warning(f"Model load warning: {e}")

            start_time = time.time()

            # Use LemonadeClient to generate image
            response = self.sd_client.generate_image(
                prompt=prompt,
                model=model,
                size=size,
                steps=steps,
                seed=seed,
                timeout=120,
            )

            generation_time_ms = int((time.time() - start_time) * 1000)

            # Parse response
            image_b64 = response["data"][0]["b64_json"]
            image_bytes = base64.b64decode(image_b64)

            # Generate filename and save
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

        except LemonadeClientError as e:
            error_msg = str(e)
            if "Connection" in error_msg or "connect" in error_msg.lower():
                error_msg = f"Cannot connect to Lemonade Server. Is it running?"
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
            # Use LemonadeClient to list SD models
            sd_models = self.sd_client.list_sd_models()
            if sd_models:
                return {
                    "status": "healthy",
                    "endpoint": f"{self.sd_client.base_url}/images/generations",
                    "models": [m["id"] for m in sd_models],
                    "output_dir": str(self.sd_output_dir),
                }
            else:
                return {
                    "status": "unavailable",
                    "endpoint": f"{self.sd_client.base_url}/images/generations",
                    "error": "No SD models available. Download with: lemonade-server serve --model SD-Turbo",
                }
        except LemonadeClientError as e:
            return {
                "status": "unavailable",
                "endpoint": f"{self.sd_client.base_url}/images/generations",
                "error": str(e),
            }
        except Exception:
            return {
                "status": "unavailable",
                "endpoint": f"{self.sd_client.base_url}/images/generations",
                "error": "Cannot connect to Lemonade Server",
            }
