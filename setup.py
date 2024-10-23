# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

from setuptools import setup

ipex_version = "2.2.0"

setup(
    name="gaia",
    version="0.4.0",
    description="GAIA genAI sandbox",
    author="AMD",
    package_dir={"": "src"},
    packages=[
        "gaia",
        "gaia.llm",
        "gaia.agents",
        "gaia.agents.Chaty",
        "gaia.agents.Clip",
        "gaia.agents.Datalin",
        "gaia.agents.Example",
        "gaia.agents.Joker",
        "gaia.agents.Maven",
        "gaia.agents.Neo",
        "gaia.agents.Picasso",
        "gaia.interface",
    ],
    install_requires=[
        "turnkeyml[llm-oga-dml]==4.0.3",
        "aiohttp",
        "fastapi",
        "pydantic==1.10.12",
        "uvicorn",
        "transformers",
        "onnx==1.16.0",
        "accelerate",
        "websockets",
        "websocket-client",
        "python-dotenv",
        "torch>=2.0.0,<2.4",
        "torchvision<0.19.0",
        "torchaudio",
        "pyside6",
    ],
    extras_require={
        "dev": [
            "ollama",
            "jupyter",
            "ipywidgets",
            "openai",
            "sentencepiece",
            "pandas",
            "wordcloud",
            "pylint",
            "regex",
        ],
        "clip": [
            "llama_index",
            "llama-cpp-python",
            "youtube_search",
            "google-api-python-client",
            "arize-phoenix[evals,llama-index]",
            "llama-index-callbacks-arize-phoenix",
            "llama-index-llms-llama-cpp",
            "llama-index-tools-arxiv",
            "llama-index-tools-wikipedia",
            "llama-index-tools-duckduckgo",
            "llama-index-readers-web",
            "llama-index-readers-papers",
            "llama-index-readers-github",
            "llama-index-readers-twitter",
            "llama-index-readers-wikipedia",
            "llama-index-llms-groq",
            "llama-index-readers-youtube-transcript",
            "llama-index-embeddings-huggingface",
        ],
        "cuda": [
            "torch @ https://download.pytorch.org/whl/cu118/torch-2.3.1%2Bcu118-cp310-cp310-win_amd64.whl",
            "torchvision @ https://download.pytorch.org/whl/cu118/torchvision-0.18.1%2Bcu118-cp310-cp310-win_amd64.whl",
            "torchaudio @ https://download.pytorch.org/whl/cu118/torchaudio-2.3.1%2Bcu118-cp310-cp310-win_amd64.whl",
        ],
        "test": [
            "pytest",
            "pytest-benchmark",
            "pytest-mock",
            "pytest-asyncio",
            "memory_profiler",
            "matplotlib",
        ]
    },
    classifiers=[],
    entry_points={
        "console_scripts": [
            "gaia = gaia.interface.widget:main",
            "gaia-cli = gaia.cli:main"
        ]
    },
    python_requires=">=3.8, <3.12",
    long_description=open("README.md", "r", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    include_package_data=True,
)
