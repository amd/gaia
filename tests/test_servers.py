# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from gaia.llm.ollama_server import OllamaClient, OllamaClientServer
from gaia.llm.ollama_server import (
    launch_ollama_model_server,
    launch_ollama_client_server,
)

if __name__ == "__main__":

    # Test ollama model serve
    model_serve = launch_ollama_model_server()

    # Test ollama client
    client = OllamaClient(model="llama3.2:1b")
    stream = client.chat("Why is the sky blue?", "You are a helpful assistant.")
    for chunk in stream:
        print(chunk["message"]["content"], end="", flush=True)

    # Test download
    client.delete_model("smollm:135m")
    client.set_model("smollm:135m")

    # Test ollama server
    parser = OllamaClientServer.parser()
    args = parser.parse_args()
    launch_ollama_client_server(args.model, "http://localhost", 8000)
