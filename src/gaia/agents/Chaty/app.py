# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import argparse
from collections import deque
from dotenv import load_dotenv

from gaia.agents.agent import Agent
from gaia.interface.util import UIMessage
from gaia.agents.Chaty.prompts import Prompts


class MyAgent(Agent):
    def __init__(self, host="127.0.0.1", port=8001, model="meta-llama/Meta-Llama-3-8B", cli_mode=False):
        super().__init__(host, port, model, cli_mode)

        load_dotenv()
        self.n_chat_messages = 4
        self.chat_history = deque(maxlen=self.n_chat_messages * 2)  # Store both user and assistant messages
        self.llm_system_prompt = Prompts.get_system_prompt("llama3-pirate")

        # Initialize agent server
        self.initialize_server()

    def get_chat_history(self):
        return list(self.chat_history)

    def prompt_llm(self, query):
        response = ""
        self.chat_history.append(f"user: {query}")
        prompt = self.llm_system_prompt + '\n'.join(self.chat_history) + "<|eot_id|><|start_header_id|>assistant<|end_header_id|>\nassistant:"

        for chunk in self.prompt_llm_server(prompt=prompt):
            response += chunk
        self.chat_history.append(f"Assistant: {response}")
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
    args = parser.parse_args()

    agent = MyAgent(host=args.host, port=args.port)
    print("Agent initialized. Type 'exit' to quit.")

    while True:
        try:
            user_input = input("You: ").strip()
            if user_input.lower() == 'exit':
                print("Goodbye!")
                break
            elif user_input:
                print("Agent: ", end="", flush=True)
                agent.prompt_received(user_input)
            else:
                print("Please enter a valid input.")
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break

if __name__ == "__main__":
    main()
