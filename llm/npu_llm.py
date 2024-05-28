import sys
import time
import requests
import websocket
import threading

from typing import Optional, List, Mapping, Any
from lemonade import leap

from llama_index.core import SimpleDirectoryReader, VectorStoreIndex, SummaryIndex, Settings
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

class LocalLLMLegacy(CustomLLM):
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

    def __init__(
        self,
        server_url:str = "localhost:8000",
        model_name:str = "meta-llama/Llama-2-7b-chat-hf",
        **kwargs
    ):
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
        response = requests.post(f'http://{self.server_url}/generate', json=payload, headers=headers)
        response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
        data = response.json()
        text = data["response"]

        return CompletionResponse(text=text)


    @llm_completion_callback()
    def stream_complete(self, prompt: str, **kwargs: Any) -> CompletionResponseGen:
        response_queue = []
        response_complete = threading.Event()

        def on_message(ws, message):
            if message.endswith('</s>'):
                response_queue.append(message.rstrip('</s>'))
                response_complete.set()
            else:
                response_queue.append(message)

        def on_error(ws, error):
            print(f"Error: {error}")

        def on_close(ws, close_status_code, close_msg):
            if not response_complete.is_set():
                print("WebSocket connection closed unexpectedly")

        def on_open(ws):
            ws.send(prompt)

        websocket.enableTrace(False)
        ws = websocket.WebSocketApp(
            f"ws://{self.server_url}/ws",
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )

        ws_thread = threading.Thread(target=ws.run_forever)
        ws_thread.start()

        while not response_complete.is_set():
            if response_queue:
                message = response_queue.pop(0)
                yield CompletionResponse(text=message, delta=message)

        # may need to pop once more, FIXME
        if response_queue:
            message = response_queue.pop(0)
            yield CompletionResponse(text=message, delta=message)

        # ws.close()
        # ws_thread.join()


def test_query_engine(queries, query_engine):
    # print(f"Query Engine prompts:\n{query_engine.get_prompts()}")
    for query in queries:
        start = time.time()
        response = query_engine.query(query)
        latency = time.time() - start

        print(f"Query: {query}")
        print(f"Response: {response}")
        print(f"{latency} secs\n")
        print('-------------------------------------------------------------------')


def test_query_engine_stream(queries, query_engine):
    # print(f"Query Engine prompts:\n{query_engine.get_prompts()}")
    for query in queries:
        print(f"Query: {query}")
        print(f"Response:")
        start = time.time()
        streaming_response = query_engine.query(query)
        for text in streaming_response.response_gen:
            if text:
                sys.stdout.write(text)
                sys.stdout.flush()
        latency = time.time() - start

        print(f"\n{latency} secs\n")
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
        # input_files=["./README.md"]
        input_files=["./data/jokes.txt"]
    ).load_data()
    # index = SummaryIndex.from_documents(documents)
    index = VectorStoreIndex.from_documents(documents)

    streaming_mode = True

    Settings.chunk_size = 64
    Settings.chunk_overlap = 0
    query_engine = index.as_query_engine(
        verbose=True,
        # verbose=False,
        similarity_top_k=3,
        response_mode="compact",
        streaming=streaming_mode
    )

    queries = [
        "how do i install dependencies?",
        "generate the commands to install dependencies"
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
        "tell me a joke about a bee.",
        "tell me a joke about a storm cloud.",
        "tell me a joke about spiderman."
    ]

    # ---------------------
    # Test the query engine
    # ---------------------
    if streaming_mode:
        test_query_engine_stream(queries, query_engine)
    else:
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