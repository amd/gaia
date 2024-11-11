# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import os
import time
import base64
import asyncio
import argparse
from collections import deque
from gaia.agents.agent import Agent
from gaia.agents.Datalin.find_match import find_match
from gaia.agents.Datalin.split import split_onnx_model


class MyAgent(Agent):
    def __init__(self, host="127.0.0.1", port=8001):
        super().__init__(host, port)

        self.n_chat_messages = 4
        self.chat_history = deque(
            maxlen=self.n_chat_messages * 2
        )  # Store both user and assistant messages

        self.llm_system_prompt = (
            "[INST] <<SYS>>\n"
            "You are a helpful, funny digital assistant that doesn't talk about itself.\n"
            "You are here to help engineers at AMD to use tools provided by the DAT team.\n"
            "All of those tools aim at doing model analysis. Please provide safe, super concise and accurate information to the user.\n"
            "You are running locally, so all models are safe with you. To be able to help the user, you need that users send you a model.\n"
            "Once they send you a model they can ask you questions about the model. Always answer in less than 50 words.\n"
            "If you get asked whether a model runs on any specific devices, say that you can't run models just yet,\n"
            "but people should look into Lemonade 🍋 by Jeremy Fowers (https://github.com/aigdat/genai).\n"
            "When referring to the tool, always use the 🍋 emoji and share the link.\n"
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
        prompt = (
            self.llm_system_prompt
            + "\n".join(self.chat_history)
            + "[/INST]\nAssistant: "
        )

        # self.log.info(prompt)
        for chunk in self.prompt_llm_server(prompt=prompt):

            # Stream chunk to UI
            self.stream_to_ui(chunk, new_card=new_card)
            new_card = False

            response += chunk
        self.chat_history.append(f"Assistant: {response}")
        return response

    def prompt_received(self, prompt):
        self.log.info(f"User: {prompt}")
        response = self.prompt_llm(prompt)
        self.log.info(f"Response: {response}")

    def process_attachments(self, user_query: str, attachments: list):
        if attachments:
            # Iterate through each attachment
            for attachment in attachments:
                # Check the content type of the attachment
                if attachment.endswith(".onnx"):
                    # Handle onnx
                    self.print(
                        "Nice! This looks like an onnx file. Let me see what I can do. Give me a sec..."
                    )
                    downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                    # pylint: disable=attribute-defined-outside-init
                    self.model_path = os.path.join(downloads_dir, attachment.name)
                    asyncio.create_task(self.process_attachment(self.model_path))
                else:
                    self.print(
                        "Ugh. I can only handle .onnx files. Can you send me in that format?"
                    )

        else:
            if "split" and "subgraphs" in user_query:
                self.print("Sure. Give me a few seconds to try it...")
                request = user_query.split()
                try:
                    subgraphs = int(request[request.index("subgraphs") - 1])
                    asyncio.create_task(self.split_subgraphs(subgraphs))
                except:  # pylint: disable=bare-except
                    self.print("Sure. How many subgraphs would you like to generate?")

            else:
                answer = self.prompt_llm(user_query)

                if "parameters" in answer:
                    asyncio.create_task(self.suggest_confluence())

    async def process_attachment(self, path: str):
        similarity_list, model_details = find_match(path)

        time.sleep(5)
        self.print(
            f"Ok, I was able to process the model. This looks a lot like **{similarity_list[0]}, {similarity_list[1]}, and {similarity_list[2]}**. "
            "What else would you like to know about this model?"
        )

        self.print(
            f"MODEL DETAILS: Parameters {model_details['parameters']}, Opset: {model_details['onnx_model_info']['opset']}"
        )

        # Assuming heatmap.png is in the same directory as this script
        with open("heatmap.png", "rb") as file:
            image_data = file.read()
            # pylint: disable=W0612
            base64_image = base64.b64encode(image_data).decode("ascii")

        # TODO: display image

    async def split_subgraphs(self, subgraphs: int):
        model_names = split_onnx_model(self.model_path, subgraphs)

        time.sleep(5)
        self.print("Ok, here is the data! (attached data)")

        # Assuming model.onnx is in the same directory as this script
        for model in model_names:
            with open(model, "rb") as file:
                onnx_data = file.read()
                # pylint: disable=W0612
                base64_onnx = base64.b64encode(onnx_data).decode("ascii")

            # TODO: support file attachments.

    async def suggest_confluence(self):
        time.sleep(5)
        self.print(
            "By the way, while we were chatting I just checked **Confluence** and it looks like there is no intake report about this model there. "
            "Would you like me to create one? I can add something to [confluence.amd.com/display/AIG/](https://confluence.amd.com/display/AIG/)."
        )

    def chat_restarted(self):
        print("Client requested chat to restart")
        # self.print("Hi there! I'm Chaty. What would you like to chat about today?")
        self.chat_history.clear()
        intro = (
            "Introduce yourself in the following way:\n"
            "Hi, I'm Datalin, a local AI agent (and future RAG) that is here to help you with DAT's tools.\n"
            "I heard you have a model you wanted me to have a look at?\n"
        )
        print("User:", intro)
        try:
            response = self.prompt_llm(intro)
            print(f"Response: {response}")
        except ConnectionRefusedError as e:
            self.print(f"Having trouble connecting to the LLM server, got:\n{str(e)}!")


def main():
    # LLM CLI for testing purposes.
    parser = argparse.ArgumentParser(description="Interact with the Joker Agent CLI")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host address for the agent server"
    )
    parser.add_argument(
        "--port", type=int, default=8001, help="Port for the agent server"
    )
    args = parser.parse_args()

    agent = MyAgent(host=args.host, port=args.port)
    print("Agent initialized. Type 'exit' to quit.")

    while True:
        try:
            user_input = input("You: ").strip()
            if user_input.lower() == "exit":
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
