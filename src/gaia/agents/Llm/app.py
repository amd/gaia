# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
from dotenv import load_dotenv

from gaia.agents.agent import Agent


class MyAgent(Agent):
    def __init__(self, model, host="127.0.0.1", port=8001, cli_mode=False):
        super().__init__(model=model, host=host, port=port, cli_mode=cli_mode)
        load_dotenv()

        # Initialize agent server
        self.initialize_server()

    def prompt_llm(self, query):
        response = ""
        for chunk in self.prompt_llm_server(query):
            response += chunk
        return response

    def prompt_received(self, prompt):
        response = self.prompt_llm(prompt)
        return response

    def chat_restarted(self):
        self.log.info("Client requested chat to restart")

    def welcome_message(self):
        self.print_ui(
            "Basic LLM Completion Test\n"
            "This is a minimal implementation to evaluate raw LLM completions.\n"
            "Enter any text and the model will generate a direct continuation."
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
