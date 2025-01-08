# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from gaia.agents.agent import Agent


class MyAgent(Agent):
    def __init__(self, host="127.0.0.1", port=8001):
        super().__init__(host, port)
        self.llm_system_prompt = (
            "[INST] <<SYS>>\n"
            "You are an Example LLM, a large language model.\n"
            "\n"
            "<</SYS>>\n\n"
        )
        self.initialize_server()

    def prompt_received(self, prompt):
        self.log.info("Message received:", prompt)

        # Prompt LLM server and stream results directly to UI
        new_card = True
        response = ""
        for token in self.prompt_llm_server(prompt=prompt):

            # Stream token to UI
            self.stream_to_ui(token, new_card=new_card)
            new_card = False
            response += token

        self.log.info(f"Message streamed: {response}")

    def chat_restarted(self):
        self.log.info("Client requested chat to restart")
        intro = "Hi, who are you in one sentence?"
        prompt = (
            self.llm_system_prompt
            + "\n".join(f"User: {intro}")
            + "[/INST]\nAssistant: "
        )
        self.log.info(f"User: {intro}")
        try:
            new_card = True
            for chunk in self.prompt_llm_server(prompt=prompt):

                # Stream chunk to UI
                self.stream_to_ui(chunk, new_card=new_card)
                new_card = False
                print(chunk, end="", flush=True)
            print("\n")
        except ConnectionRefusedError as e:
            self.print_ui(
                f"Having trouble connecting to the LLM server, got:\n{str(e)}!"
            )
            self.log.error(str(e))


if __name__ == "__main__":
    agent = MyAgent()
