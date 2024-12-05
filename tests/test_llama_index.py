# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
import time
import multiprocessing
from llama_index.core import (
    VectorStoreIndex,
    Document,
    Settings,
)

from gaia.cli import GaiaCliClient
from gaia.agents.agent import Agent
from gaia.llm.llama_index_local import LocalLLM


def test_query_engine():
    text = """
From fairest creatures we desire increase,
That thereby beauty's rose might never die,
But as the riper should by time decease,
His tender heir might bear his memory:
But thou contracted to thine own bright eyes,
Feed'st thy light's flame with self-substantial fuel,
Making a famine where abundance lies,
Thyself thy foe, to thy sweet self too cruel:
Thou that art now the world's fresh ornament,
And only herald to the gaudy spring,
Within thine own bud buriest thy content,
And, tender churl, mak'st waste in niggarding:
  Pity the world, or else this glutton be,
  To eat the world's due, by the grave and thee.
"""
    agent = Agent(cli_mode=True)

    Settings.llm = LocalLLM(
        prompt_llm_server=agent.prompt_llm_server, stream_to_ui=agent.stream_to_ui
    )
    Settings.embed_model = "local:BAAI/bge-small-en-v1.5"
    Settings.chunk_size = 128
    Settings.chunk_overlap = 16
    similarity_top = 3

    vector_index = VectorStoreIndex.from_documents(
        [Document(text=text)], show_progress=True
    )
    query_engine = vector_index.as_query_engine(
        verbose=True,
        similarity_top_k=similarity_top,
        response_mode="compact",
        streaming=True,
    )

    query = "What is the main theme of this sonnet?"
    response = query_engine.query(query)
    assert response is not None
    print(f"Response: {response}")


def start_llm_server():
    backend = "oga"
    device = "npu"
    model = "amd/Phi-3.5-mini-instruct-awq-g128-int4-asym-fp32-onnx-ryzen-strix"

    client = GaiaCliClient(backend=backend, device=device, model=model)
    process = multiprocessing.Process(target=client.start_llm_server)
    process.start()

    # Wait for server to be ready
    timeout = 360  # 360 second timeout
    start_time = time.time()
    while not client.check_llm_server_ready():
        if time.time() - start_time > timeout:
            raise TimeoutError("LLM server failed to start within timeout period")
        print("Waiting for LLM server to start...")
        time.sleep(4)


def start_ollama_server():
    backend = "ollama"
    device = "cpu"
    model = "llama3.2:1b"

    client = GaiaCliClient(backend=backend, device=device, model=model)
    process = multiprocessing.Process(target=client.start_ollama_servers)
    process.start()

    # Wait for server to be ready
    timeout = 60  # 60 second timeout
    start_time = time.time()
    while not client.check_ollama_servers_ready():
        if time.time() - start_time > timeout:
            raise TimeoutError("Ollama servers failed to start within timeout period")
        print("Waiting for Ollama servers to start...")
        time.sleep(4)


if __name__ == "__main__":
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(description="A simple example using argparse")
    parser.add_argument(
        "--backend",
        type=str,
        help="Which software/hardware backend to run LLMs on",
        choices=["ollama", "npu"],
        default="ollama",
    )
    args = parser.parse_args()

    if args.backend == "npu":
        start_llm_server()
    else:  # "ollama"
        start_ollama_server()

    # Run the test
    test_query_engine()
