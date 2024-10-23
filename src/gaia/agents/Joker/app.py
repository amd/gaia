# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import os
import argparse
from collections import deque
from pathlib import Path
from llama_index.core import Settings, PromptTemplate
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from gaia.agents.agent import Agent
from gaia.llm.llama_index_local import LocalLLM


class MyAgent(Agent):
    def __init__(self, host="127.0.0.1", port=8001):
        super().__init__(host, port)

        # Define model
        Settings.llm = LocalLLM(
            prompt_llm_server=self.prompt_llm_server, stream_to_ui=self.stream_to_ui
        )
        Settings.embed_model = "local:BAAI/bge-base-en-v1.5"

        # Load the joker data
        joke_data = os.path.join(Path(__file__).parent, "data", "jokes.txt")
        Settings.chunk_size = 128
        Settings.chunk_overlap = 0
        documents = SimpleDirectoryReader(input_files=[joke_data]).load_data()
        index = VectorStoreIndex.from_documents(documents)

        self.n_chat_messages = 4
        self.chat_history = deque(maxlen=self.n_chat_messages * 2)  # Store both user and assistant messages

        # phi3-mini system prompt
        # Prepare query engine
        # qa_prompt_tmpl_str = (
        #     "<|user|>\n"
        #     "Context information is below.\n"
        #     "---------------------\n"
        #     "{context_str}\n"
        #     "---------------------\n"
        #     "Given the context information above I want you to think step by step to answer the query in a crisp manner, incase case you don't know the answer say 'I don't know!'.\n"
        #     "Keep you answers short and concise and end message with </s>.\n"
        #     "{query_str}</s>\n"
        #     "<|assistant|>"
        # )
        self.qa_prompt_tmpl_str = (
            "[INST] <<SYS>>\n"
            "You are Joker, a sarcastic and funny asistant with an attitude that likes to chat with the user.\n"
            "List of jokes below below to use in your response.\n"
            "---------------------\n"
            "{context_str}\n"
            "---------------------\n"
            "\n"
            "Your tasks:\n"
            "1. Given the context information above I want you to think step by step to answer the query in a crisp manner.\n"
            "2. Keep you answers funny, sarcastic, short and concise and end message with </s>.\n"
            "3. Chat about funny and sarcastic things\n"
            "4. Answer comments and questions from user using the list of jokes above\n"
            "\n"
            "Guidelines:\n"
            "- Answer a question given in a natural human-like manner.\n"
            "- Think step-by-step when answering questions.\n"
            "- When introducing yourself, keep it to just a single sentence, for example:\n"
            "\"Assistant: Hi, I can tell funny jokes. Just tell me a hint and I'll tell you a joke.\"\n"
            "- Keep your answers funny and concise\n"
            "\n"
            "<</SYS>>\n\n"
            "User: {query_str} [/INST]\n"
            "Assistant: "
        )
        qa_prompt_tmpl = PromptTemplate(self.qa_prompt_tmpl_str)
        self.query_engine = index.as_query_engine(
            verbose=True,
            similarity_top_k=1,
            response_mode="compact",
            streaming=True,
        )
        self.query_engine.update_prompts(
            {"response_synthesizer:text_qa_template": qa_prompt_tmpl}
        )

        # Initialize agent server
        self.initialize_server()

    def get_chat_history(self):
        return list(self.chat_history)

    def chat(self, query):
        # Add user query to chat history, construct the prompt and add response
        # to chat history.
        self.chat_history.append(f"User: {query}")
        prompt = '\n'.join(self.chat_history)
        response = self.query_engine.query(prompt)
        self.chat_history.append(f"Assistant: {response}")

        return response

    def prompt_received(self, prompt):
        self.log.info(f"User: {prompt}")
        response = self.chat(prompt)
        self.log.info(f"Response: {response}")

    def chat_restarted(self):
        self.log.info("Client requested chat to restart")
        self.chat_history.clear()
        intro = "Hi, who are you in one sentence?"
        prompt = self.qa_prompt_tmpl_str + '\n'.join(f"User: {intro}") + "[/INST]\nAssistant: "
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
            self.print(f"Having trouble connecting to the LLM server, got:\n{str(e)}!")
            self.log.error(str(e))


def main():
    # Joker LLM CLI for testing purposes.
    parser = argparse.ArgumentParser(description="Interact with the Joker Agent CLI")
    parser.add_argument("--host", default="127.0.0.1", help="Host address for the agent server")
    parser.add_argument("--port", type=int, default=8001, help="Port for the agent server")
    args = parser.parse_args()

    agent = MyAgent(host=args.host, port=args.port)
    print("Joker Agent initialized. Type 'exit' to quit.")

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
    agent = MyAgent()
