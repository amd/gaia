# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from setuptools import setup

tkml_version = "5.0.4"

setup(
    name="gaia",
    version="0.6.3",
    description="GAIA genAI sandbox",
    author="AMD",
    package_dir={"": "src"},
    packages=[
        "gaia",
        "gaia.llm",
        "gaia.agents",
        "gaia.agents.Llm",
        "gaia.agents.Chaty",
        "gaia.agents.Clip",
        "gaia.agents.Example",
        "gaia.agents.Joker",
        "gaia.agents.Maven",
        "gaia.agents.Neo",
        "gaia.agents.Picasso",
        "gaia.interface",
    ],
    install_requires=[
        "aiohttp",
        "fastapi",
        "pydantic==1.10.12",
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
        "pyaudio",
        "openai-whisper",
        "numpy",
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
        "maven": [
            "openai",
            "llama-index-tools-arxiv",
            "llama-index-tools-duckduckgo",
            "llama-index-readers-web",
            "llama-index-readers-papers",
            "llama-index-readers-wikipedia",
            "llama-index-tools-wikipedia",
            "gaia[llamaindex]",
        ],
        "neo": [
            "llama-index-readers-github",
            "gaia[llamaindex]",
        ],
        "notebooks": [
            "jupyter",
            "ipywidgets",
            "openai",
            "wordcloud",
            "arize-phoenix[evals,llama-index]",
            "llama-index-callbacks-arize-phoenix",
            "gaia[clip,maven,neo,llamaindex]",
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
