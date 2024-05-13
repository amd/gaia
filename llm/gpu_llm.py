from typing import Optional, List, Mapping, Any

import time
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

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

class GpuLLM(CustomLLM):
    context_window: int = 3900
    num_output: int = 256
    model_name: str = "microsoft/Phi-3-mini-4k-instruct"
    torch.random.manual_seed(0)

    model = AutoModelForCausalLM.from_pretrained(
        model_name, 
        # device_map="cuda", 
        device_map="cpu", 
        torch_dtype="auto", 
        trust_remote_code=True, 
    )
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    pipe = pipeline(
        "text-generation",
        model=model,
        tokenizer=tokenizer,
    )

    generation_args = {
        "max_new_tokens": 500,
        "return_full_text": False,
        "temperature": 0.0,
        "do_sample": False,
    }

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

        output = self.pipe(prompt, **self.generation_args)
        text = output[0]['generated_text']

        return CompletionResponse(text=text)

    @llm_completion_callback()
    def stream_complete(
        self, prompt: str, **kwargs: Any
    ) -> CompletionResponseGen:
        output = self.pipe(prompt, **self.generation_args)
        text = output[0]['generated_text']
        response = ""
        for token in text:
            response += token
            yield CompletionResponse(text=response, delta=token)


def test_query_engine(queries, query_engine):
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
    # define our LLM
    llm = GpuLLM()
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
    # test_query_engine(queries, query_engine)

    # ---------------------
    # Build the ReAct agent
    # ---------------------
    query_engine_tools = [
        QueryEngineTool(
            query_engine=query_engine,
            metadata=ToolMetadata(
                name="repo",
                description=(
                    "Provides information about code repository. "
                    "Use a detailed plain text question as input to the tool."
                ),
            ),
        ),
    ]

    agent = ReActAgent.from_tools(query_engine_tools, llm=llm, verbose=True)
    # TODO: Update w/ shorter prompt
    # agent.update_prompts({"agent_worker:system_prompt": react_system_prompt})

    test_agent(queries, agent)