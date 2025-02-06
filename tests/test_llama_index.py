# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
import time
import multiprocessing
import asyncio
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
    return response  # Return the response so we can verify it completed


def start_llm_server_process():
    backend = "oga"
    device = "hybrid"
    dtype = "int4"
    model = "amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid"
    client = GaiaCliClient(backend=backend, device=device, dtype=dtype, model=model)
    client.start_llm_server()
    # Save the actual server PID
    client.save_server_info()


def start_llm_server():
    print("Starting LLM server...")
    process = multiprocessing.Process(target=start_llm_server_process)
    process.start()

    # Store the process globally so we can terminate it later
    global llm_server_process
    llm_server_process = process

    print("Creating check client...")
    # Create a new client just for checking status
    backend = "oga"
    device = "hybrid"
    dtype = "int4"
    model = "amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid"
    check_client = GaiaCliClient(
        backend=backend, device=device, dtype=dtype, model=model
    )

    # Wait for server to be ready
    timeout = 360  # 360 second timeout
    start_time = time.time()
    print("Waiting for LLM server to be ready...")
    while not check_client.check_llm_server_ready():
        if time.time() - start_time > timeout:
            raise TimeoutError("LLM server failed to start within timeout period")
        print("Still waiting for LLM server...")
        time.sleep(4)
    print("LLM server is ready!")

    return process


def start_ollama_server_process():
    backend = "ollama"
    device = "cpu"
    model = "llama3.2:1b"
    client = GaiaCliClient(backend=backend, device=device, model=model)
    client.start_ollama_servers()


def start_ollama_server():
    process = multiprocessing.Process(target=start_ollama_server_process)
    process.start()

    # Store the process globally so we can terminate it later
    global ollama_server_process
    ollama_server_process = process

    # Create a new client just for checking status
    backend = "ollama"
    device = "cpu"
    model = "llama3.2:1b"
    check_client = GaiaCliClient(backend=backend, device=device, model=model)

    # Save server information
    check_client.server_pids["ollama_client"] = process.pid
    check_client.save_server_info()

    # Wait for server to be ready
    timeout = 60  # 60 second timeout
    start_time = time.time()
    while not check_client.check_ollama_servers_ready():
        if time.time() - start_time > timeout:
            raise TimeoutError("Ollama servers failed to start within timeout period")
        print("Waiting for Ollama servers to start...")
        time.sleep(4)

    return process


def cleanup_servers():
    """Helper function to cleanup all running servers"""
    try:
        client = GaiaCliClient()
        client.stop()
        print("Servers stopped successfully")
    except Exception as e:
        print(f"Error during cleanup: {e}")


if __name__ == "__main__":
    server_process = None
    client = None
    try:
        parser = argparse.ArgumentParser(description="A simple example using argparse")
        parser.add_argument(
            "--backend",
            type=str,
            help="Which software/hardware backend to run LLMs on",
            choices=["ollama", "hybrid"],
            default="ollama",
        )
        args = parser.parse_args()

        if args.backend == "hybrid":
            server_process = start_llm_server()
        else:  # "ollama"
            server_process = start_ollama_server()

        # Load the server info from the file that was saved by the server process
        client = asyncio.run(GaiaCliClient.load_existing_client())
        if not client:
            raise RuntimeError("Failed to load server information")

        # Wait for server to be ready
        timeout = 360  # 360 second timeout
        start_time = time.time()
        print("Waiting for LLM server to be ready...")
        while not (
            client.check_llm_server_ready()
            if args.backend == "hybrid"
            else client.check_ollama_servers_ready()
        ):
            if time.time() - start_time > timeout:
                raise TimeoutError("LLM server failed to start within timeout period")
            print("Still waiting for LLM server...")
            time.sleep(4)
        print("LLM server is ready!")

        # Run the test
        response = test_query_engine()
        print("Test completed successfully!")

    finally:
        try:
            # Clean up using the client that has the correct server PIDs
            if client:
                print("Stopping servers through GaiaCliClient...")
                client.stop()
        except Exception as e:
            print(f"Error during cleanup: {e}")
            # Fallback to direct process termination if client stop fails
            if server_process and server_process.is_alive():
                print("Fallback: Terminating server process directly...")
                server_process.terminate()
                server_process.join(timeout=5)
                print("Server process terminated")
