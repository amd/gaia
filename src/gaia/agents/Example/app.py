# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

from gaia.agents.agent import Agent

class MyAgent(Agent):
    def __init__(self, host, port):
        super().__init__(host, port)
        self.initialize_server()

    def prompt_received(self, prompt):
        print("Message received:", prompt)

        # Prompt LLM server and stream results directly to UI
        new_card = True
        response = ""
        for token in self.prompt_llm_server(prompt=prompt):

            # Stream token to UI
            self.stream_to_ui(token, new_card=new_card)
            new_card = False
            response += token

        print(f"Message streamed: {response}")

    def chat_restarted(self):
        print("Client requested chat to restart")


if __name__ == "__main__":
    agent = MyAgent(host="127.0.0.1", port=8001)
