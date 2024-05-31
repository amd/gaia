from setuptools import setup

ipex_version = "2.2.0"

setup(
    name="gaia",
    version="0.1.0",
    description="GAIA genAI sandbox",
    author="Kalin Ovtcharov",
    author_email="kalin.ovtcharov@amd.com",
    package_dir={"": "src"},
    packages=[
        "gaia",
    ],
    install_requires=[
        "botbuilder-integration-aiohttp>=4.14.0",
        "botbuilder-core",
        "asyncio",
        "aiohttp",
        "fastapi",
        "pydantic",
        "uvicorn",
        "openai",
        "transformers",
        "torch",
        "pandas",
        "onnx",
        "accelerate",
        "split",
        "llama_index",
        "websockets",
        "websocket-client",
        "wordcloud",
        "python-dotenv",
        "torch",
        "torchvision",
        "torchaudio",
        # "torch @ https://download.pytorch.org/whl/cu118/torch-1.13.1%2Bcu118-cp38-cp38-linux_x86_64.whl",
        # "torchvision @ https://download.pytorch.org/whl/cu118/torchvision-0.14.1%2Bcu118-cp38-cp38-linux_x86_64.whl",
        # "torchaudio @ https://download.pytorch.org/whl/cu118/torchaudio-0.13.1%2Bcu118-cp38-cp38-linux_x86_64.whl",
    ],
    extras_require={
        "clipy": [
            "llama-index-readers-youtube-transcript",
        ],
        "joker": [
            "llama-index-readers-github",
            "llama-index-embeddings-huggingface",
        ],
        "neo": [
            "llama-index-readers-github",
            "llama-index-embeddings-huggingface",
        ]
    },
    classifiers=[],
    entry_points={
        "console_scripts": [
            "gaia=gaia:gaiacli",
        ]
    },
    python_requires=">=3.8, <3.11",
    long_description=open("README.md", "r", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
)
