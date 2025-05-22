# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from setuptools import setup
import re

with open("src/gaia/version.py", encoding="utf-8") as fp:
    version_content = fp.read()
    version_match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', version_content)
    if not version_match:
        raise ValueError("Unable to find version string in version.py")
    gaia_version = version_match.group(1)

tkml_version = "5.0.4"

setup(
    name="gaia",
    version=gaia_version,
    description="GAIA is an AI assistant framework that provides a user-friendly interface for interacting with large language models and AI agents",
    author="AMD",
    package_dir={"": "src"},
    packages=[
        "gaia",
        "gaia.llm",
        "gaia.audio",
        "gaia.agents",
        "gaia.audio",
        "gaia.agents.Llm",
        "gaia.agents.Chaty",
        "gaia.agents.Clip",
        "gaia.agents.Joker",
        "gaia.agents.Rag",
        "gaia.agents.Blender",
        "gaia.interface",
    ],
    package_data={
        "gaia.interface": ["*.json", "img/*"],
    },
    install_requires=[
        "aiohttp",
        "fastapi",
        "pydantic>=2.9.2",
        "uvicorn>=0.15.0",
        "transformers",
        "accelerate",
        "websockets",
        "websocket-client",
        "python-dotenv",
        "torch>=2.0.0,<2.4",
        "torchvision<0.19.0",
        "torchaudio",
        "pyside6",
        "ollama",
        "onnxconverter-common",
    ],
    extras_require={
        "dml": [
            f"turnkeyml[llm-oga-igpu]=={tkml_version}",
        ],
        "npu": [
            f"turnkeyml[llm-oga-npu]=={tkml_version}",
        ],
        "hybrid": [
            f"turnkeyml[llm-oga-hybrid]=={tkml_version}",
        ],
        "llamaindex": [
            "llama_index",
            "llama-index-embeddings-huggingface",
        ],
        "joker": [
            "gaia[llamaindex]",
        ],
        "clip": [
            "youtube_search",
            "google-api-python-client",
            "llama-index-readers-youtube-transcript",
            "gaia[llamaindex]",
        ],
        "rag": [
            "gaia[llamaindex]",
        ],
        "blender": [
            "openai",
            "rich",
            "bpy",
        ],
        "notebooks": [
            "jupyter",
            "ipywidgets",
            "openai",
            "wordcloud",
            "arize-phoenix[evals,llama-index]",
            "llama-index-callbacks-arize-phoenix",
            "gaia[clip,llamaindex]",
        ],
        "cuda": [
            "torch @ https://download.pytorch.org/whl/cu118/torch-2.3.1%2Bcu118-cp310-cp310-win_amd64.whl",
            "torchvision @ https://download.pytorch.org/whl/cu118/torchvision-0.18.1%2Bcu118-cp310-cp310-win_amd64.whl",
            "torchaudio @ https://download.pytorch.org/whl/cu118/torchaudio-2.3.1%2Bcu118-cp310-cp310-win_amd64.whl",
        ],
        "dev": [
            "pytest",
            "pytest-benchmark",
            "pytest-mock",
            "pytest-asyncio",
            "memory_profiler",
            "matplotlib",
            "adjustText",
            "plotly",
            "black",
        ],
        "eval" : [
            "anthropic",
        ],
        "talk":[
            "pyaudio",
            "openai-whisper",
            "numpy==1.26.4",
            "kokoro>=0.3.1",
            "soundfile",
            "sounddevice",
            "soundfile",
        ]
    },
    classifiers=[],
    entry_points={
        "console_scripts": [
            "gaia = gaia.interface.widget:main",
            "gaia-cli = gaia.cli:main",
        ]
    },
    python_requires=">=3.8, <3.12",
    long_description=open("README.md", "r", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    include_package_data=True,
)
