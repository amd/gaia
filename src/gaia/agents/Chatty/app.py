# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import argparse
from collections import deque
from gaia.agents.agent import Agent


class MyAgent(Agent):
    def __init__(self, host, port):
        super().__init__(host, port)

        self.n_chat_messages = 4
        self.chat_history = deque(maxlen=self.n_chat_messages * 2)  # Store both user and assistant messages

        self.llm_system_prompt = (
            "[INST] <<SYS>>\n"
            "You are Chatty, a large langauge model running locally on the User's laptop offering the best privacy.\n"
            "You are friendly, inquisitive and keep your answers short and concise.\n"
            "Your goal is to engage the User while providing helpful responses.\n"
            "\n"
            "Guidelines:\n"
            "- Analyze queries step-by-step for accurate, brief answers.\n"
            "- End each message with </s>.\n"
            "- Use a natural, conversational tone.\n"
            "- Avoid using expressions like *grins*, use emojis sparingly.\n"
            "- Show curiosity by asking relevant follow-up questions.\n"
            "- Break down complex problems when answering.\n"
            "- Introduce yourself in one friendly sentence.\n"
            "- Balance information with user engagement.\n"
            "- Adapt to the user's language style and complexity.\n"
            "- Admit uncertainty and ask for clarification when needed.\n"
            "- Respect user privacy.\n"
            "\n"
            "Prioritize helpful, engaging interactions within ethical bounds.\n"
            "<</SYS>>\n\n"
        )

        # Initialize agent server
        self.initialize_server()

    def get_chat_history(self):
        return list(self.chat_history)

    def prompt_llm(self, query):
        response = ""
        new_card = True
        self.chat_history.append(f"User: {query}")
        prompt = self.llm_system_prompt + '\n'.join(self.chat_history) + "[/INST]\nAssistant: "

        # print(prompt)
        for chunk in self.prompt_llm_server(prompt=prompt):

            # Stream chunk to UI
            self.stream_to_ui(chunk, new_card=new_card)
            new_card = False

            response += chunk
        self.chat_history.append(f"Assistant: {response}")
        return response

    def prompt_received(self, prompt):
        print("User:", prompt)
        response = self.prompt_llm(prompt)
        print(f"Response: {response}")

    def chat_restarted(self):
        print("Client requested chat to restart")
        # self.print("Hi there! I'm Chatty. What would you like to chat about today?")
        self.chat_history.clear()
        intro = "Hi, Chatty, who are you in one sentence?"
        print("User:", intro)
        response = self.prompt_llm(intro)
        print(f"Response: {response}")


def main():
    # LLM CLI for testing purposes.
    parser = argparse.ArgumentParser(description="Interact with the Agent CLI")
    parser.add_argument("--host", default="127.0.0.1", help="Host address for the agent server")
    parser.add_argument("--port", type=int, default=8001, help="Port for the agent server")
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
