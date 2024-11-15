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
            '"Assistant: Hi, I can tell funny jokes. Just tell me a hint and I\'ll tell you a joke."\n'
            "- Keep your answers funny and concise\n"
            "\n"
            "<</SYS>>\n\n"
            "User: {query_str} [/INST]\n"
            "Assistant: "
        )
        qa_prompt_tmpl = PromptTemplate(self.qa_prompt_tmpl_str)
        self.query_engine = self.vector_index.as_query_engine(
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

    def prompt_llm(self, query):
        self.chat_history.append(f"User: {query}")
        prompt = "\n".join(self.chat_history)

        # Get the streaming response and convert it to string
        response = str(self.query_engine.query(prompt))

        self.chat_history.append(f"Assistant: {response}")
        return response

    def prompt_received(self, prompt):
        response = self.prompt_llm(prompt)
        return response

    def chat_restarted(self):
        self.log.info("Client requested chat to restart")
        self.chat_history.clear()


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
