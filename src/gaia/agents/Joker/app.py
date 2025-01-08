# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import argparse
from collections import deque
from pathlib import Path
from llama_index.core import Settings
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from gaia.agents.agent import Agent
from gaia.llm.llama_index_local import LocalLLM


class MyAgent(Agent):
    def __init__(self, model, host="127.0.0.1", port=8001, cli_mode=False):
        super().__init__(model=model, host=host, port=port, cli_mode=cli_mode)

        # Define model
        self.llm = LocalLLM(
            prompt_llm_server=self.prompt_llm_server, stream_to_ui=self.stream_to_ui
        )
        Settings.llm = self.llm
        Settings.embed_model = "local:BAAI/bge-base-en-v1.5"

        # Load the joker data
        joke_data = os.path.join(Path(__file__).parent, "data", "jokes.txt")
        Settings.chunk_size = 128
        Settings.chunk_overlap = 0
        documents = SimpleDirectoryReader(input_files=[joke_data]).load_data()
        self.vector_index = VectorStoreIndex.from_documents(documents)

        self.n_chat_messages = 4
        self.chat_history = deque(
            maxlen=self.n_chat_messages * 2
        )  # Store both user and assistant messages

        # Create chat engine with proper configuration
        self.chat_engine = self.vector_index.as_chat_engine(
            verbose=False,
            similarity_top_k=1,
            chat_mode="context",
            streaming=True,
            system_prompt=(
                "[INST] <<SYS>>\n"
                "You are Joker, a sarcastic and funny assistant with an attitude that likes to chat with the user. "
                "Use the provided jokes in your responses when relevant. "
                "Keep your answers funny, sarcastic, short and concise. "
                "Chat about funny and sarcastic things and answer questions using the available jokes.\n"
                "Guidelines:\n"
                "- Answer a question given in a natural human-like manner.\n"
                "- Think step-by-step when answering questions.\n"
                "- Keep your answers funny and concise\n"
                "<</SYS>>\n\n"
            ),
        )

        # Initialize agent server
        self.initialize_server()

    def get_chat_history(self):
        return list(self.chat_history)

    def prompt_llm(self, query):
        self.chat_history.append(f"User: {query}")
        response = str(self.chat_engine.chat(query))
        self.chat_history.append(f"Assistant: {response}")
        return response

    def prompt_received(self, prompt):
        response = self.prompt_llm(prompt)
        return response

    def chat_restarted(self):
        self.log.info("Client requested chat to restart")
        self.chat_history.clear()

    def welcome_message(self):
        self.print_ui(
            "Welcome to Joker! I'm your sarcastic and funny AI assistant with an attitude. "
            "I'm powered by a knowledge base of jokes and use RAG (Retrieval-Augmented Generation) to:\n"
            "- Tell relevant jokes from my extensive collection\n"
            "- Chat about funny and sarcastic things\n"
            "- Answer your questions with a humorous twist\n"
            "- Find the perfect joke for any topic or situation\n\n"
            "Just give me a hint or topic and I'll search my joke database to make you laugh!"
        )


def main():
    parser = argparse.ArgumentParser(description="Run the Joker Agent")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host address for the agent server"
    )
    parser.add_argument(
        "--port", type=int, default=8001, help="Port number for the agent server"
    )
    parser.add_argument("--model", required=True, help="Model name")
    args = parser.parse_args()

    MyAgent(
        model=args.model,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
