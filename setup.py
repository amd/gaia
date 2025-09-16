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
    description="GAIA is a lightweight agent framework designed for the edge and AI PCs.",
    author="AMD",
    package_dir={"": "src"},
    packages=[
        "gaia",
        "gaia.llm",
        "gaia.audio",
        "gaia.chat",
        "gaia.talk", 
        "gaia.apps",
        "gaia.apps.llm",
        "gaia.apps.summarize",
        "gaia.eval",
        "gaia.mcp",
        "gaia.agents",
        "gaia.agents.base",
        "gaia.agents.blender",
        "gaia.agents.blender.core",
        "gaia.agents.jira",
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
    ],
    extras_require={
        "audio": [
            "torch>=2.0.0,<2.4",
            "torchvision<0.19.0",
            "torchaudio",
        ],
        "blender": [
            "bpy",
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
            "responses",
            "requests",
        ],
        "eval" : [
            "anthropic",
            "bs4",
            "scikit-learn",
            "numpy",
            "pypdf",
        ],
        "talk":[
            "pyaudio",
            "openai-whisper",
            "numpy==1.26.4",
            "kokoro>=0.3.1",
            "soundfile",
            "sounddevice",
        ],
        "youtube": [
            "llama-index-readers-youtube-transcript",
        ]
    },
    classifiers=[],
    entry_points={
        "console_scripts": [
            "gaia = gaia.cli:main",
            "gaia-cli = gaia.cli:main",
            "gaia-mcp = gaia.mcp.mcp_bridge:main",
            "gaia-mcp-atlassian = gaia.mcp.atlassian_mcp:main",
        ]
    },
    python_requires=">=3.8, <3.13",
    long_description=open("README.md", "r", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    include_package_data=True,
)
