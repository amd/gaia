import time
from typing import Any
from openai import OpenAI

from llama_index.core import SimpleDirectoryReader, SummaryIndex, Settings
from llama_index.core.llms import (
    CustomLLM,
    CompletionResponse,
    CompletionResponseGen,
    LLMMetadata,
)
# from llama_index.core.agent import ReActAgent
# from llama_index.core.tools import QueryEngineTool, ToolMetadata
from llama_index.core.llms.callbacks import llm_completion_callback


class LocalLLM(CustomLLM):
    context_window: int = 3900
    num_output: int = 500
    max_tokens: int = -1
    model_name: str
    temperature: float
    client: Any

    def __init__(
        self,
        client,
        # model_name: str = "LM Studio Community/Phi-3-mini-4k-instruct-GGUF",
        model_name: str = "LM Studio Community/Meta-Llama-3-8B-Instruct-GGUF",
        temperature: float = 0.7,
        **kwargs
    ):
        super().__init__(
            client=client,
            model_name=model_name,
            temperature=temperature,
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
    def complete(self, prompt: str, **kwargs: Any) -> CompletionResponse: # pylint: disable=W0221
        print("-----------------------------------------------")
        print(prompt)
        print("-----------------------------------------------")
        completion = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=self.temperature,
            stream=False
        )
        text = completion.choices[0].message.content
        print(text)

        return CompletionResponse(text=text)


    @llm_completion_callback()
    def stream_complete(self, prompt: str, **kwargs: Any) -> CompletionResponseGen: # pylint: disable=W0221
        completion = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=self.temperature,
            stream=True
        )

        response = ""
        for chunk in completion:
            text = chunk.choices[0].delta.content
            if text:
                response += text
                yield CompletionResponse(text=response, delta=chunk)


def test_query_engine(queries, query_engine):
    # from Neo.system_prompt import react_system_header_str
    # print(f"Query Engine prompts:\n{query_engine.get_prompts()}")
    for query in queries:
        print(f"Query: {query}")

        print("Response:")
        start = time.time()
        response = query_engine.query(query)
        latency = time.time() - start
        print(f"{response}")

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
    client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
    llm = LocalLLM(client)
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
        verbose=True,
        # verbose=False,
        similarity_top_k=1,
        response_mode="compact",
        streaming=True
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
