# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import argparse
from collections import deque
from dotenv import load_dotenv

from gaia.agents.agent import Agent
from gaia.interface.util import UIMessage
from gaia.agents.Chaty.prompts import Prompts


class MyAgent(Agent):
    def __init__(self, model, host="127.0.0.1", port=8001, cli_mode=False):
        super().__init__(model=model, host=host, port=port, cli_mode=cli_mode)

        load_dotenv()
        self.n_chat_messages = 4
        self.chat_history = deque(maxlen=self.n_chat_messages * 2)  # Store both user and assistant messages

        # Initialize agent server
        self.initialize_server()

    def get_chat_history(self):
        return list(self.chat_history)

    def prompt_llm(self, query):
        response = ""
        self.chat_history.append(f"user: {query}")
        prompt = Prompts.get_system_prompt(self.model, list(self.chat_history))  # Use the static method directly

        for chunk in self.prompt_llm_server(prompt=prompt):
            response += chunk
        self.chat_history.append(f"assistant: {response}")
        return response

    def prompt_received(self, prompt):
        response = self.prompt_llm(prompt)
        return response

    def chat_restarted(self):
        self.log.info("Client requested chat to restart")
        self.chat_history.clear()
        intro = "Hi, who are you in one sentence?"
        self.log.info(f"User: {intro}")
        try:
            response = self.prompt_llm(intro)
            self.log.info(f"Response: {response}")
        except ConnectionRefusedError as e:
            UIMessage.error(f"Having trouble connecting to the LLM server.\n\n{str(e)}")

def main():
    parser = argparse.ArgumentParser(description="Run the MyAgent chatbot")
    parser.add_argument("--host", default="127.0.0.1", help="Host address for the agent server")
    parser.add_argument("--port", type=int, default=8001, help="Port number for the agent server")
    parser.add_argument("--model", required=True, help="Model name")
    args = parser.parse_args()

    MyAgent(
        model=args.model,
        host=args.host,
        port=args.port,
    )

if __name__ == "__main__":
    main()
