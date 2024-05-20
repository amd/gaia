import os
import re
import time
import sys
import asyncio
import subprocess
from io import StringIO
import openai
from dotenv import load_dotenv

from lemonade import leap
from agents.Neo.system_prompt import react_system_prompt, react_system_prompt_small

from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, SummaryIndex, Settings
from llama_index.llms.openai import OpenAI
from llama_index.core.agent import ReActAgent
from llama_index.core.tools import QueryEngineTool, FunctionTool, ToolMetadata
from llama_index.readers.github import GithubRepositoryReader, GithubClient

from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import ChannelAccount, Activity

from llm.npu_llm import LocalLLM

llm = LocalLLM()
Settings.llm = llm
Settings.embed_model = "local:BAAI/bge-base-en-v1.5"

# use query engine instead for now.
Settings.chunk_size = 64
Settings.chunk_overlap = 0
documents = SimpleDirectoryReader(
    input_files=["./data/jokes.txt"]
).load_data()
index = VectorStoreIndex.from_documents(documents)

query_engine = index.as_query_engine(
    verbose=True,
    similarity_top_k=1,
    response_mode="compact",
    streaming=True,
)

class MyBot(ActivityHandler):

    async def on_message_activity(self, turn_context: TurnContext):
        # Send message to agent and get response
        query = turn_context.activity.text
        print(f"\nQuery: {query}")
        start_time = time.time()
        streaming_response = query_engine.query(query)
        print("Answer: ", end="")
        response = ""
        for text in streaming_response.response_gen:
            if text:
                response += text

                # Send streaming message
                asyncio.create_task(self.send_stream(turn_context, text))

                # Print the streaming response to the console
                print(text, end="", flush=True)

        elapsed_time = time.time() - start_time
        tps = len(response.split()) / elapsed_time

        # strip end characters
        response = response.rstrip("</s>")

        # Send the entire response as a single message
        act = Activity(
            type="message",
            text=response,
            channel_data={"tokens_per_second": tps},
        )
        await turn_context.send_activity(act)

    async def on_members_added_activity(self, members_added: ChannelAccount, turn_context: TurnContext):
        initial_greeting = "Hi I'm Neo, I can generate jokes for you. Just ask me to tell you a joke about anything. For example, 'tell me a joke about lemons'"
        for member_added in members_added:
            if member_added.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(initial_greeting)

    async def send_stream(self, turn_context: TurnContext, streamed_message):
        act = Activity(
            type="streaming",
            text=streamed_message,
        )
        await turn_context.send_activity(act)
