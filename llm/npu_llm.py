import time
import requests

from typing import Optional, List, Mapping, Any
from lemonade import leap

from llama_index.core import SimpleDirectoryReader, SummaryIndex, Settings
from llama_index.core.callbacks import CallbackManager
from llama_index.core.llms import (
    CustomLLM,
    CompletionResponse,
    CompletionResponseGen,
    LLMMetadata,
)
from llama_index.core.agent import ReActAgent
from llama_index.core.tools import QueryEngineTool, FunctionTool, ToolMetadata
from llama_index.readers.github import GithubRepositoryReader, GithubClient
from llama_index.core.llms.callbacks import llm_completion_callback

class NpuLLM(CustomLLM):
    context_window: int = 3900
    num_output: int = 256
    model_name: Any
    model: Any
    tokenizer: Any

    def __init__(self, model_name: str, model: Any, tokenizer: Any, **kwargs):
        super().__init__(
            model_name=model_name,
            model=model,
            tokenizer=tokenizer,
            **kwargs
        )

    @property
    def metadata(self) -> LLMMetadata:
        """Get LLM metadata."""
        return LLMMetadata(
            context_window=self.context_window,
            num_output=self.num_output,
            model_name=self.model_name,
        )

    @llm_completion_callback()
    def complete(self, prompt: str, **kwargs: Any) -> CompletionResponse:
        # print(f"{prompt}")
        input_ids = self.tokenizer(prompt, return_tensors="pt").input_ids
        response = self.model.generate(input_ids, max_new_tokens=30)
        text = self.tokenizer.decode(response[0])

        return CompletionResponse(text=text)

    @llm_completion_callback()
    def stream_complete(
        self, prompt: str, **kwargs: Any
    ) -> CompletionResponseGen:
        input_ids = self.tokenizer(prompt, return_tensors="pt").input_ids
        response = self.model.generate(input_ids, max_new_tokens=30)
        text = tokenizer.decode(response[0])

        response = ""
        for token in text:
            response += token
            yield CompletionResponse(text=response, delta=token)


class LocalLLM(CustomLLM):
    context_window: int = 3900
    num_output: int = 256
    model_name: Any
    server_url: str

    def __init__(self, server_url:str = "http://localhost:8000/generate", model_name:str = "meta-llama/Llama-2-7b-chat-hf", **kwargs):
        super().__init__(
            server_url=server_url,
            model_name=model_name,
            **kwargs
        )

    @property
    def metadata(self) -> LLMMetadata:
        """Get LLM metadata."""
        return LLMMetadata(
            context_window=self.context_window,
            num_output=self.num_output,
            model_name=self.model_name,
        )

    @llm_completion_callback()
    def complete(self, prompt: str, **kwargs: Any) -> CompletionResponse:
        payload = {"text": prompt}
        headers = {"Content-Type": "application/json"}
        response = requests.post(self.server_url, json=payload, headers=headers)
        response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
        data = response.json()
        text = data["response"]

        return CompletionResponse(text=text)


    @llm_completion_callback()
    def stream_complete(self, prompt: str, **kwargs: Any) -> CompletionResponseGen:
        import websocket  # Import the websocket library

        ws = websocket.WebSocket()
        ws.connect(self.server_url.replace("http", "ws"))  # Connect to the WebSocket server
        ws.send(prompt)  # Send the prompt to the server

        response = ""
        while True:
            chunk = ws.recv()  # Receive a chunk of generated text from the server
            if not chunk:
                break
            response += chunk
            yield CompletionResponse(text=response, delta=chunk)

        ws.close()  # Close the WebSocket connection


def test_query_engine(queries, query_engine):
    # from Neo.system_prompt import react_system_header_str
    print(f"Query Engine prompts:\n{query_engine.get_prompts()}")
    for query in queries:
        start = time.time()
        response = query_engine.query(query)
        latency = time.time() - start

        print(f"Query: {query}")
        print(f"Response: {response}")
        print(f"{latency} secs\n")
        print('-------------------------------------------------------------------')


def test_agent(queries, agent):
    hint = "(do not answer implictly, instead use the readme tool)"
    for query in queries:
        start = time.time()
        response = agent.chat(f"{query}\n{hint}")
        latency = time.time() - start

        print(f"Query: {query}")
        print(f"Response: {response}")
        print(f"{latency} secs\n")
        print('-------------------------------------------------------------------')


if __name__ == "__main__":
    llm = LocalLLM()
    Settings.llm = llm

    # define embed model
    Settings.embed_model = "local:BAAI/bge-base-en-v1.5"

    # Load the your data
    documents = SimpleDirectoryReader(
        input_files=["./README.md"]
    ).load_data()
    index = SummaryIndex.from_documents(documents)

    # query_engine = index.as_query_engine()
    query_engine = index.as_query_engine(
        # verbose=True,
        verbose=False,
        similarity_top_k=1,
        response_mode="compact"
    )

    queries = [
        "give me the link to Turnkey tests",
        "give me the link to Turnkey instructions",
        "what is TurnkeyML's mission?",
        "how do I get started?",
        "where is the installation guide?",
        "how about tutorials?",
        "what use-cases are there?",
        "execute the demo.",
        "what was it benchmarked on?",
        "generated a report, collect the statistics and summarize in a spreadsheet",
    ]
    queries = [
        "how do i install dependencies?",
        "generate the commands to install dependencies"
    ]

    # ---------------------
    # Test the query engine
    # ---------------------
    test_query_engine(queries, query_engine)

    # ---------------------
    # Build the ReAct agent
    # ---------------------
    # query_engine_tools = [
    #     QueryEngineTool(
    #         query_engine=query_engine,
    #         metadata=ToolMetadata(
    #             name="repo",
    #             description=(
    #                 "Provides information about code repository. "
    #                 "Use a detailed plain text question as input to the tool."
    #             ),
    #         ),
    #     ),
    # ]

    # from Neo.system_prompt import react_system_prompt

    # agent = ReActAgent.from_tools(query_engine_tools, llm=llm, verbose=True)
    # agent.update_prompts({"agent_worker:system_prompt": react_system_prompt})

    # # test_agent(queries, agent)