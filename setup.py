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
        "gaia.schedule",
        "gaia.ui",
        "gaia.ui.routers",
        "gaia.ui.email_sidecar",
        "gaia.database",
        "gaia.talk",
        "gaia.testing",
        "gaia.utils",
        "gaia.apps",
        "gaia.apps.docker",
        "gaia.apps.jira",
        "gaia.apps.llm",
        "gaia.apps.summarize",
        "gaia.apps.summarize.templates",
        "gaia.eval",
        "gaia.installer",
        "gaia.hub",
        "gaia.rag",
        "gaia.mcp",
        "gaia.mcp.client",
        "gaia.mcp.client.transports",
        "gaia.mcp.servers",
        "gaia.agents",
        "gaia.agents.base",
        "gaia.agents.tools",
        "gaia.agents.builder",
        "gaia.agents.code_index",
        "gaia.agents.code_index.tools",
        "gaia.governance",
        "gaia.sd",
        "gaia.vlm",
        "gaia.api",
        "gaia.filesystem",
        "gaia.scratchpad",
        "gaia.web",
        "gaia.code_index",
        "gaia.apps.webui",
        "gaia.connectors",
        "gaia.connectors.catalog",
        "gaia.connectors.providers",
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
        # Browser-mode Agent UI bundle. Recursive globs in package_data are
        # unreliable across setuptools versions, so we list shallow patterns
        # here and back them up with `recursive-include` in MANIFEST.in. The
        # CI verifier (util/verify_wheel_dist.py) enforces that the wheel
        # actually contains these entries before publish.
        "gaia.apps.webui": [
            "dist/index.html",
            "dist/*.svg",
            "dist/*.png",
            "dist/*.ico",
            "dist/*.webmanifest",
            "dist/*.json",
            "dist/*.txt",
            "dist/assets/*",
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
        "requests",
        "beautifulsoup4",
        "watchdog>=2.1.0",
        "pillow>=9.0.0",
        # Cron-based scheduler (issue #892): apscheduler drives the daemon;
        # tomli/tomli-w read+write ~/.gaia/schedules.toml (tomllib is stdlib on 3.11+).
        "apscheduler>=3.10.0",
        "tomli-w>=1.0.0",
        "tomli>=2.0.0; python_version < '3.11'",
        # Required by the `gaia-mcp` bridge (base console_script), which parses
        # multipart uploads via python_multipart at import time. Base — not an
        # extra — so a plain `pip install amd-gaia` ships a working gaia-mcp.
        "python-multipart>=0.0.9",
        # gaia connectors is a base CLI command; keyring is its OS credential store (OAuth tokens #915). #1621
        "keyring>=24.0.0,<26.0.0",
        "tavily-python>=0.5.0",
    ],
    extras_require={
        "image": [
            "term-image>=0.7.0,<0.8",
        ],
        "api": [
            "fastapi>=0.115.0",
            "uvicorn>=0.32.0",
            "python-multipart>=0.0.9",
            # [api] auto-mounts the gaia-agent-email REST router (openai_server.py),
            # whose import chain reaches gaia.connectors.store -> `import keyring`
            # at module load AND at request time (connected_mailbox_providers).
            # Declare it so `amd-gaia[api]` + gaia-agent-email starts & serves triage
            # with zero manual installs. Same pin as [ui]/[dev]. See #1617.
            "keyring>=24.0.0,<26.0.0",
        ],
        "ui": [
            "fastapi>=0.115.0",
            "uvicorn>=0.32.0",
            "python-multipart>=0.0.9",
            "httpx>=0.27.0",
            "psutil>=5.9.0",
            # OAuth connections (issue #915): keyring stores refresh tokens in
            # the OS credential store (macOS Keychain, Windows DPAPI, Linux
            # SecretService). Pinned upper bound per supply-chain advisory.
            "keyring>=24.0.0,<26.0.0",
            # RAG runtime deps — gaia.rag.sdk uses faiss/pypdf/pymupdf/numpy and
            # embeds via Lemonade (NOT sentence-transformers). See #845.
            # Version specifiers match the standalone "rag" extra.
            "faiss-cpu>=1.7.0",
            "numpy>=1.24.0",
            "pymupdf>=1.24.0",
            "pypdf",
            "python-pptx>=0.6.21",
            "python-docx>=1.1.0",
            # Memory cross-encoder reranker (gaia.agents.base.memory) — optional
            # at runtime (graceful degradation) but bundled with "ui" so the
            # full chat experience gets reranking out of the box. NOT a RAG dep.
            "sentence-transformers",
            "safetensors",
            # torch is pinned lower-bound only. The "audio" extra caps
            # torch<2.4 because torchvision<0.19 / torchaudio require it,
            # but "ui" ships neither — capping here would force resolver
            # downgrades for users with torch 2.5+ already installed.
            "torch>=2.0.0",
        ],
        "audio": [
            "torch>=2.0.0,<2.13",
            "torchvision<0.28.0",
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
        "telegram": [
            "python-telegram-bot>=20.3",
        ],
        "litellm": [
            "litellm>=1.35.0,<2.0",
        ],
        "dev": [
            "pytest",
            "pytest-cov",
            "pytest-benchmark",
            "pytest-mock",
            "pytest-asyncio",
            "pytest-xdist",
            "pytest-rerunfailures",
            "pyfakefs",
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
            # gaia.connectors runtime deps surfaced in [dev] so that
            # `pip install -e ".[dev]"` is sufficient to run the unit suite
            # without pulling in the much heavier [ui] extra (faiss, torch).
            "httpx>=0.27.0,<0.29.0",
            "respx>=0.21.0,<0.24.0",
            "keyring>=24.0.0,<26.0.0",
            # Tokenizer proxy for the tool-prompt cost harness (#1448,
            # gaia.eval.tool_cost) so the budget test can count tokens.
            "tiktoken>=0.7.0,<1.0.0",
        ],
        "eval": [
            "anthropic",
            "bs4",
            "scikit-learn>=1.5.0",
            "numpy>=2.0,<2.3.0",
            "pypdf",
            "reportlab",
            # Tool-prompt cost measurement (#1448): tiktoken cl100k_base proxy.
            "tiktoken>=0.7.0,<1.0.0",
        ],
        "talk": [
            "sounddevice",
            "openai-whisper",
            "kokoro>=0.3.1",
            "soundfile",
            "psutil",
            "pip",  # Required: spacy model download needs pip in venv (uv omits it)
        ],
        "youtube": [
            "llama-index-readers-youtube-transcript",
        ],
        "rag": [
            # RAG embeds via Lemonade, not sentence-transformers — do NOT add it
            # here. It is only needed for the optional memory reranker (see "ui").
            "faiss-cpu>=1.7.0",
            "numpy>=1.24.0",
            "pymupdf>=1.24.0",
            "pypdf",
            "python-pptx>=0.6.21",
            "python-docx>=1.1.0",
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
        # Agent Hub packaging/publishing toolchain (issues #1093, #1179):
        # 'gaia agent pack' shells out to 'python -m build', 'gaia agent publish'
        # to 'twine upload'. Kept out of [dev] so the unit suite stays lean.
        # install with 'pip install "amd-gaia[publish]"' to package an agent.
        "publish": [
            "build>=1.0.0",
            "twine>=5.0.0",
        ],
        # Standalone AMD production agents (issues #1102, #1179). Each agent
        # ships as a separate 'gaia-agent-<id>' wheel that depends on this
        # framework wheel; 'amd-gaia[agents]' installs all migrated agents at
        # once. Add an entry here when each agent's wheel is first published.
        "agent-summarize": ["gaia-agent-summarize"],
        "agent-sd": ["gaia-agent-sd"],
        "agent-fileio": ["gaia-agent-fileio"],
        "agent-docker": ["gaia-agent-docker"],
        "agent-jira": ["gaia-agent-jira"],
        "agent-blender": ["gaia-agent-blender"],
        "agent-emr": ["gaia-agent-emr"],
        "agent-code": ["gaia-agent-code"],
        "agent-connectors-demo": ["gaia-agent-connectors-demo"],
        "agent-analyst": ["gaia-agent-analyst"],
        "agent-browser": ["gaia-agent-browser"],
        "agent-docqa": ["gaia-agent-docqa"],
        "agent-routing": ["gaia-agent-routing"],
        "agent-email": ["gaia-agent-email"],
        "agent-chat": ["gaia-agent-chat"],
        "agents": [
            "gaia-agent-summarize",
            "gaia-agent-sd",
            "gaia-agent-fileio",
            "gaia-agent-docker",
            "gaia-agent-jira",
            "gaia-agent-blender",
            "gaia-agent-emr",
            "gaia-agent-code",
            "gaia-agent-connectors-demo",
            "gaia-agent-analyst",
            "gaia-agent-browser",
            "gaia-agent-docqa",
            "gaia-agent-routing",
            "gaia-agent-email",
            "gaia-agent-chat",
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
        ]
    },
    python_requires=">=3.10",
    long_description=open("README.md", "r", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    include_package_data=True,
)
