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


class LocalLLM(CustomLLM):
    context_window: int = 3900
    num_output: int = 256
    model_name: Any
    server_url: str
    api_key: str

    def __init__(
        self, server_url: str = "http://localhost:1234/v1",
        api_key: str = "lm-studio",
        model_name: str = "LM Studio Community/Phi-3-mini-4k-instruct-GGUF",
        **kwargs
    ):
        super().__init__(
            server_url=server_url,
            api_key=api_key,
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
        url = f"{self.server_url}/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        data = {
            "model": self.model_name,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": kwargs.get("temperature", 0.7)
        }

        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes

        completion_data = response.json()
        text = completion_data["choices"][0]["message"]["content"]

        return CompletionResponse(text=text)

    @llm_completion_callback()
    def stream_complete(self, prompt: str, **kwargs: Any) -> CompletionResponseGen:
        import websocket  # Import the websocket library

        url = f"{self.server_url}/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        data = {
            "model": self.model_name,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": kwargs.get("temperature", 0.7),
            "stream": True
        }

        ws = websocket.create_connection(url, header=headers)
        ws.send(json.dumps(data))

        response = ""
        while True:
            chunk = ws.recv()
            if not chunk:
                break
            chunk_data = json.loads(chunk)
            delta = chunk_data["choices"][0]["delta"].get("content", "")
            response += delta
            yield CompletionResponse(text=response, delta=delta)

        ws.close()  # Close the WebSocket connection


if __name__ == "__main__":
    llm = LLMServer()
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