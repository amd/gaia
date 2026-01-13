# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Hardware Advisor Agent Example

An interactive agent that helps you determine what size LLM your system can run
based on actual hardware capabilities detected via Lemonade Server.

Usage:
    python examples/hardware_advisor_agent.py

This will start an interactive session where you can ask the agent to:
- Check your system hardware specifications
- Recommend models based on your RAM and GPU
- List available models in the catalog
- Explain what you can run locally
"""

from typing import Any, Dict

from gaia import Agent, tool
from gaia.llm.lemonade_client import LemonadeClient


class HardwareAdvisorAgent(Agent):
    """Agent that advises on LLM capabilities based on your hardware."""

    def __init__(self, **kwargs):
        self.client = LemonadeClient(keep_alive=True)
        super().__init__(**kwargs)
        self.max_steps = 50

    def _get_system_prompt(self) -> str:
        return """You are a hardware advisor for running local LLMs on AMD systems.

Use the available tools to check system hardware and recommend models.
When users ask about LLM capabilities, always check their actual hardware first.
Be helpful and explain your recommendations in plain language.

Available capabilities:
- Check system hardware (RAM, GPU, NPU) via get_hardware_info
- List available models from Lemonade Server via list_available_models
- Recommend models based on hardware specs via recommend_models

Always use tools to get real data - never guess specifications."""

    def _get_gpu_info(self) -> Dict[str, Any]:
        """Detect GPU using OS-native commands."""
        import platform
        import subprocess

        system = platform.system()

        try:
            if system == "Windows":
                # Use PowerShell Get-WmiObject (wmic is deprecated on Windows 11)
                ps_command = (
                    "Get-WmiObject Win32_VideoController | "
                    "Select-Object Name,AdapterRAM | "
                    "ConvertTo-Csv -NoTypeInformation"
                )
                result = subprocess.run(
                    ["powershell", "-Command", ps_command],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    lines = [
                        l.strip()
                        for l in result.stdout.strip().split("\n")
                        if l.strip()
                    ]
                    # CSV format: "Name","AdapterRAM"
                    for line in lines[1:]:  # Skip header
                        # Remove quotes and split
                        line = line.replace('"', "")
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                name = parts[0].strip()
                                adapter_ram = (
                                    int(parts[1]) if parts[1].strip().isdigit() else 0
                                )
                                if name and len(name) > 0:
                                    return {
                                        "name": name,
                                        "memory_mb": (
                                            adapter_ram // (1024 * 1024)
                                            if adapter_ram > 0
                                            else 0
                                        ),
                                    }
                            except (ValueError, IndexError):
                                continue

            elif system == "Linux":
                # Use lspci to find VGA devices
                result = subprocess.run(
                    ["lspci"], capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.split("\n"):
                        if "VGA compatible controller" in line:
                            # Extract GPU name after the colon
                            parts = line.split(":", 2)
                            if len(parts) >= 3:
                                return {
                                    "name": parts[2].strip(),
                                    "memory_mb": 0,  # Memory not available via lspci
                                }

        except Exception as e:
            # Debug output
            print(f"GPU detection error: {e}")

        return {"name": "Not detected", "memory_mb": 0}

    def _register_tools(self):
        client = self.client
        agent = self

        @tool
        def get_hardware_info() -> Dict[str, Any]:
            """Get detailed system hardware information including RAM, GPU, and NPU."""
            try:
                # Use Lemonade Server's system info API for basic info
                info = client.get_system_info()

                # Parse RAM (format: "32.0 GB")
                ram_str = info.get("Physical Memory", "0 GB")
                ram_gb = float(ram_str.split()[0]) if ram_str else 0

                # Detect GPU
                gpu_info = agent._get_gpu_info()
                gpu_name = gpu_info.get("name", "Not detected")
                gpu_available = gpu_name != "Not detected"
                gpu_memory_mb = gpu_info.get("memory_mb", 0)
                gpu_memory_gb = (
                    round(gpu_memory_mb / 1024, 2) if gpu_memory_mb > 0 else 0
                )

                # Get NPU information from Lemonade
                devices = info.get("devices", {})
                npu_info = devices.get("npu", {})
                npu_available = npu_info.get("available", False)
                npu_name = (
                    npu_info.get("name", "Not detected")
                    if npu_available
                    else "Not detected"
                )

                return {
                    "success": True,
                    "os": info.get("OS Version", "Unknown"),
                    "processor": info.get("Processor", "Unknown"),
                    "ram_gb": ram_gb,
                    "gpu": {
                        "name": gpu_name,
                        "memory_mb": gpu_memory_mb,
                        "memory_gb": gpu_memory_gb,
                        "available": gpu_available,
                    },
                    "npu": {"name": npu_name, "available": npu_available},
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                    "message": "Failed to get hardware information from Lemonade Server",
                }

        @tool
        def list_available_models() -> Dict[str, Any]:
            """List all models available in the catalog with their sizes and download status."""
            try:
                # Fetch model catalog from Lemonade Server
                response = client.list_models(show_all=True)
                models_data = response.get("data", [])

                # Enrich each model with size information
                enriched_models = []
                for model in models_data:
                    model_id = model.get("id", "")

                    # Get size estimate for this model
                    model_info = client.get_model_info(model_id)
                    size_gb = model_info.get("size_gb", 0)

                    enriched_models.append(
                        {
                            "id": model_id,
                            "name": model.get("name", model_id),
                            "size_gb": size_gb,
                            "downloaded": model.get("downloaded", False),
                            "labels": model.get("labels", []),
                        }
                    )

                # Sort by size (largest first)
                enriched_models.sort(key=lambda m: m["size_gb"], reverse=True)

                return {
                    "success": True,
                    "models": enriched_models,
                    "count": len(enriched_models),
                    "message": f"Found {len(enriched_models)} models in catalog",
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                    "message": "Failed to fetch models from Lemonade Server",
                }

        @tool
        def recommend_models(ram_gb: float, gpu_memory_mb: int = 0) -> Dict[str, Any]:
            """Recommend models based on available system memory.

            Args:
                ram_gb: Available system RAM in GB
                gpu_memory_mb: Available GPU memory in MB (0 if no GPU)

            Returns:
                Dictionary with model recommendations that fit in available memory
            """
            try:
                # Get all available models
                models_result = list_available_models()
                if not models_result.get("success"):
                    return models_result  # Propagate error

                all_models = models_result.get("models", [])

                # Calculate maximum safe model size
                # Rule: Model size should be < 70% of available RAM (30% overhead for inference)
                max_model_size_gb = ram_gb * 0.7

                # Filter models that fit in memory
                fitting_models = [
                    model
                    for model in all_models
                    if model["size_gb"] <= max_model_size_gb and model["size_gb"] > 0
                ]

                # Add recommendation metadata
                for model in fitting_models:
                    # Estimate actual runtime memory needed (model size + ~30% overhead)
                    model["estimated_runtime_gb"] = round(model["size_gb"] * 1.3, 2)
                    model["fits_in_ram"] = model["estimated_runtime_gb"] <= ram_gb

                    # Check GPU fit if GPU available
                    if gpu_memory_mb > 0:
                        gpu_memory_gb = gpu_memory_mb / 1024
                        model["fits_in_gpu"] = model["size_gb"] <= (gpu_memory_gb * 0.9)

                # Sort by size (largest = most capable)
                fitting_models.sort(key=lambda m: m["size_gb"], reverse=True)

                return {
                    "success": True,
                    "recommendations": fitting_models,
                    "total_fitting_models": len(fitting_models),
                    "constraints": {
                        "available_ram_gb": ram_gb,
                        "available_gpu_mb": gpu_memory_mb,
                        "max_model_size_gb": round(max_model_size_gb, 2),
                        "safety_margin_percent": 30,
                    },
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                    "message": "Failed to generate model recommendations",
                }


def main():
    """Run the Hardware Advisor Agent interactively."""
    print("=" * 60)
    print("Hardware Advisor Agent")
    print("=" * 60)
    print("\nHi! I can help you figure out what size LLM your system can run.")
    print("\nTry asking:")
    print("  - 'What size LLM can I run?'")
    print("  - 'Show me my system specs'")
    print("  - 'What models are available?'")
    print("  - 'Can I run a 30B model?'")
    print("\nType 'quit', 'exit', or 'q' to stop.\n")

    # Create agent (uses local Lemonade server by default)
    try:
        agent = HardwareAdvisorAgent()
        print("Agent ready!\n")
    except Exception as e:
        print(f"Error initializing agent: {e}")
        print("\nMake sure Lemonade server is running.")
        print("GAIA will start it automatically on first use.")
        return

    # Interactive loop
    while True:
        try:
            user_input = input("You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            # Process the query (agent prints the output)
            agent.process_query(user_input)
            print()  # Add spacing

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()
