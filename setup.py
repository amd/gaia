# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import re

from setuptools import setup

with open("src/gaia/version.py", encoding="utf-8") as fp:
    version_content = fp.read()
    version_match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', version_content)
    if not version_match:
        raise ValueError("Unable to find version string in version.py")
    gaia_version = version_match.group(1)

tkml_version = "5.0.4"

setup(
    name="amd-gaia",
    version=gaia_version,
    description="GAIA is a lightweight agent framework designed for the edge and AI PCs.",
    author="AMD",
    url="https://github.com/amd/gaia",
    license="MIT",
    package_dir={"": "src"},
    packages=[
        "gaia",
        "gaia.llm",
        "gaia.llm.providers",
        "gaia.audio",
        "gaia.chat",
        "gaia.database",
        "gaia.talk",
        "gaia.testing",
        "gaia.utils",
        "gaia.apps",
        "gaia.apps.llm",
        "gaia.apps.summarize",
        "gaia.eval",
        "gaia.installer",
        "gaia.rag",
        "gaia.mcp",
        "gaia.mcp.servers",
        "gaia.agents",
        "gaia.agents.base",
        "gaia.agents.blender",
        "gaia.agents.blender.core",
        "gaia.agents.chat",
        "gaia.agents.chat.tools",
        "gaia.agents.docker",
        "gaia.agents.emr",
        "gaia.agents.emr.dashboard",
        "gaia.agents.jira",
        "gaia.agents.code",
        "gaia.agents.code.orchestration",
        "gaia.agents.code.orchestration.factories",
        "gaia.agents.code.orchestration.steps",
        "gaia.agents.code.orchestration.workflows",
        "gaia.agents.code.prompts",
        "gaia.agents.code.tools",
        "gaia.agents.code.validators",
        "gaia.agents.routing",
        "gaia.agents.summarize",
        "gaia.api",
    ],
    package_data={
        "gaia.eval": [
            "webapp/*.json",
            "webapp/*.js",
            "webapp/*.md",
            "webapp/public/*.html",
            "webapp/public/*.css",
            "webapp/public/*.js",
        ],
    },
    install_requires=[
        "openai",
        "pydantic>=2.9.2",
        "transformers",
        "accelerate",
        "python-dotenv",
        "aiohttp",
        "rich",
        "watchdog>=2.1.0",
        "pillow>=9.0.0",
    ],
    extras_require={
        "api": [
            "fastapi>=0.115.0",
            "uvicorn>=0.32.0",
            "python-multipart>=0.0.9",
        ],
        "audio": [
            "torch>=2.0.0,<2.4",
            "torchvision<0.19.0",
            "torchaudio",
        ],
        "blender": [
            "bpy",
        ],
        "mcp": [
            "mcp>=1.1.0",
            "starlette",
            "uvicorn",
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
            "pylint",
            "isort",
            "flake8",
            "autoflake",
            "mypy",
            "bandit",
            "responses",
            "requests",
        ],
        "eval": [
            "anthropic",
            "bs4",
            "scikit-learn>=1.5.0",
            "numpy>=2.0,<2.3.0",
            "pypdf",
        ],
        "talk": [
            "pyaudio",
            "openai-whisper",
            "numpy==1.26.4",
            "kokoro>=0.3.1",
            "soundfile",
            "sounddevice",
        ],
        "youtube": [
            "llama-index-readers-youtube-transcript",
        ],
        "rag": [
            "pypdf",
            "pymupdf>=1.24.0",
            "sentence-transformers",
            "faiss-cpu>=1.7.0",
        ],
        "lint": [
            "black",
            "pylint",
            "isort",
            "flake8",
            "autoflake",
            "mypy",
            "bandit",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    entry_points={
        "console_scripts": [
            "gaia = gaia.cli:main",
            "gaia-cli = gaia.cli:main",
            "gaia-mcp = gaia.mcp.mcp_bridge:main",
            "gaia-mcp-atlassian = gaia.mcp.atlassian_mcp:main",
            "gaia-emr = gaia.agents.emr.cli:main",
            "gaia-code = gaia.agents.code.cli:main",
        ]
    },
    python_requires=">=3.10",
    long_description=open("README.md", "r", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    include_package_data=True,
)
