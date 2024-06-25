import os
from pathlib import Path
from llama_index.core import Settings
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from gaia.agents.agent import Agent, LocalLLM


class MyAgent(Agent):
    def __init__(self, host, port):
        super().__init__(host, port)

        # Define model
        Settings.llm = LocalLLM(
            prompt_llm_server=self.prompt_llm_server, stream_to_ui=self.stream_to_ui
        )
        Settings.embed_model = "local:BAAI/bge-base-en-v1.5"

        # Load the joker data
        joke_data = os.path.join(Path(__file__).parent, "data", "jokes.txt")
        Settings.chunk_size = 64
        Settings.chunk_overlap = 0
        documents = SimpleDirectoryReader(input_files=[joke_data]).load_data()
        index = VectorStoreIndex.from_documents(documents)

        # Prepare query engine
        self.query_engine = index.as_query_engine(
            verbose=True,
            similarity_top_k=1,
            response_mode="compact",
            streaming=True,
        )

        # Initialize agent server
        self.initialize_server()

    def prompt_received(self, prompt):
        print("Message received:", prompt)

        # Call agent
        response = self.query_engine.query(prompt)
        print(f"Agent Response: {response}")

    def chat_restarted(self):
        print("Client requested chat to restart")
        self.stream_to_ui("Hi, I'm joker", new_card=True)
        self.stream_to_ui("I'm here to tell you jokes.", new_card=True)
        self.stream_to_ui(" Feel free to ask anything.", new_card=False)


if __name__ == "__main__":
    agent = MyAgent(host="127.0.0.1", port=8001)
