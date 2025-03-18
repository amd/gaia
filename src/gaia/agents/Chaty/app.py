# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
from collections import deque
from dotenv import load_dotenv

from gaia.agents.agent import Agent
from gaia.agents.Chaty.prompts import Prompts


class MyAgent(Agent):
    def __init__(self, model, host="127.0.0.1", port=8001, cli_mode=False):
        super().__init__(model=model, host=host, port=port, cli_mode=cli_mode)

        load_dotenv()
        self.n_chat_messages = 4
        self.chat_history = deque(
            maxlen=self.n_chat_messages * 2
        )  # Store both user and assistant messages

        # Initialize agent server
        self.initialize_server()

    def get_chat_history(self):
        return list(self.chat_history)

    def prompt_llm(self, query):
        response = ""
        self.chat_history.append(f"user: {query}")
        prompt = Prompts.get_system_prompt(
            self.model, list(self.chat_history)
        )  # Use the static method directly

        for chunk in self.prompt_llm_server(prompt=prompt):
            response += chunk
        self.chat_history.append(f"assistant: {response}")
        return response

    def prompt_received(self, prompt):
        response = self.prompt_llm(prompt)
        return response

    def prompt_stream(self, prompt):
        # Stream response directly instead of concatenating
        self.chat_history.append(f"user: {prompt}")
        prompt = Prompts.get_system_prompt(self.model, list(self.chat_history))

        response = ""
        for chunk in self.prompt_llm_server(prompt=prompt):
            response += chunk
            yield chunk  # Yield each chunk for streaming

        self.chat_history.append(f"assistant: {response}")

    def chat_restarted(self):
        """Clear the conversation history"""
        self.log.info("Client requested chat to restart")
        self.chat_history.clear()

    def welcome_message(self):
        """Return the welcome message for this agent"""
        return (
            "Welcome to Chaty! This AI assistant provides clear and technically-sound responses. "
            f"The system maintains a history of the last {self.n_chat_messages} conversation turns for contextual "
            "awareness. Feel free to reference previous messages in your questions.\n\n"
            "What would you like to chat about?"
        )


def main():
    parser = argparse.ArgumentParser(description="Run the MyAgent chatbot")
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
