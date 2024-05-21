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

from llama_index.core import VectorStoreIndex, Document, SimpleDirectoryReader, DocumentSummaryIndex, Settings
from llama_index.llms.openai import OpenAI
from llama_index.core.agent import ReActAgent
from llama_index.core.tools import QueryEngineTool, FunctionTool, ToolMetadata
from llama_index.readers.github import GithubRepositoryReader, GithubClient
from llama_index.readers.youtube_transcript import YoutubeTranscriptReader

from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import ChannelAccount, Activity

from llm.npu_llm import LocalLLM

def extract_youtube_link(message):
    youtube_link_pattern = r'https?://(?:www\.)?(?:youtube\.com|youtu\.be)/(?:watch\?v=)?(?:embed/)?(?:v/)?(?:shorts/)?(?:\S+)'
    match = re.search(youtube_link_pattern, message)
    if match:
        return match.group()
    else:
        return None

def get_youtube_transcript_doc(yt_links:list)->Document:
    return YoutubeTranscriptReader().load_data(ytlinks=yt_links)

def build_index(doc:Document, persist_dir=None)->VectorStoreIndex:
    if persist_dir:
        storage_context = StorageContext.from_defaults(persist_dir=persist_dir)
        index = VectorStoreIndex.load_from_storage(storage_context)
    else:
        index = VectorStoreIndex.from_documents(doc, show_progress=True)
    return index

def build_summary_index(doc:Document, persist_dir=None)->DocumentSummaryIndex:
    if persist_dir:
        storage_context = StorageContext.from_defaults(persist_dir=persist_dir)
        index = DocumentSummaryIndex.load_from_storage(storage_context)
    else:
        # from https://docs.llamaindex.ai/en/stable/examples/index_structs/doc_summary/DocSummary/
        index = DocumentSummaryIndex.from_documents(doc, show_progress=True)
        splitter = SentenceSplitter(chunk_size=1024)

        # default mode of building the index
        response_synthesizer = get_response_synthesizer(
            response_mode="tree_summarize", use_async=True
        )
        doc_summary_index = DocumentSummaryIndex.from_documents(
            doc,
            transformations=[splitter],
            response_synthesizer=response_synthesizer,
            show_progress=True,
        )
    return index

def get_query_engine(index, similarity_top=3):
    query_engine = index.as_query_engine(
        verbose=True,
        # verbose=False,
        similarity_top_k=similarity_top,
        response_mode="compact",
        streaming=True
    )
    return query_engine

def get_youtube_tool():
    return FunctionTool.from_defaults(fn=get_youtube_transcript_doc)

def remove_color_formatting(text):
    # ANSI escape codes for color formatting
    ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    return ansi_escape.sub("", text)


def custom_engine_query(query_engine, query):
    print(f"\nQuery: {query}")
    start_time = time.time()
    streaming_response = query_engine.query(query)
    print("Answer: ", end="", flush=True)
    response = ""
    for text in streaming_response.response_gen:
        if text:
            response += text
            # Print the streaming response to the console
            print(text, end="", flush=True)
    elapsed_time = time.time() - start_time
    tps = len(response.split()) / elapsed_time

    # strip end characters
    response = response.rstrip("</s>")
    return response, tps


def custom_agent_query(agent, query):
    print(f"Query: {query}")
    start_time = time.time()
    streaming_response = agent.chat(query)
    response = ""
    for text in streaming_response.response_gen:
        if text:
            response += text
            # Print the streaming response to the console
            print(text, end="", flush=True)
    elapsed_time = time.time() - start_time
    tps = len(response.split()) / elapsed_time

    # strip end characters
    response = response.rstrip("</s>")
    return response, tps


# initialize llm
# load_dotenv()
# openai.api_key = os.getenv("OPENAI_API_KEY")
# llm = OpenAI(model="gpt-3.5-turbo-0613")
# llm = OpenAI(model="gpt-4")

llm = LocalLLM()
Settings.llm = llm
Settings.embed_model = "local:BAAI/bge-base-en-v1.5"

# initialize ReAct agent
# TODO: Disable the ReAct agent for now due to slowness/bad UX.
# agent = ReActAgent.from_tools([multiply_tool, exe_tool], llm=llm, verbose=True, streaming=True, is_dummy_stream=True)
# agent.update_prompts({"agent_worker:system_prompt": react_system_prompt_small})

# use query engine instead for now.
Settings.chunk_size = 64
Settings.chunk_overlap = 0

yt_doc = None
yt_index = None
yt_summary = None
yt_engine = None
# yt_tool = get_youtube_tool(transcript_doc)


query_engine_tools = [
    QueryEngineTool(
        query_engine=yt_engine,
        metadata=ToolMetadata(
            name="youtube",
            description=(
                "YouTube transcript of Andrej Karpathy's Introduction to LLMs. "
                "Use a detailed plain text question as input to the tool."
            ),
        ),
    ),
]


class MyBot(ActivityHandler):

    async def on_message_activity(self, turn_context: TurnContext):
        global yt_doc
        global yt_index
        global yt_summary
        global yt_engine

        message = turn_context.activity.text
        message_yt_link = extract_youtube_link(message)
        tps = 0
        if message_yt_link:
            response = f"Thanks for the link. Extracting the transcript, building index and query engine from {message_yt_link}"
            act = Activity(
                type="message",
                text=response,
                channel_data={"tokens_per_second": tps},
            )
            await turn_context.send_activity(act)

            yt_links = [message_yt_link]
            yt_doc = get_youtube_transcript_doc(yt_links)
            doc_id = "youtube_doc"
            yt_doc[0].doc_id = doc_id
            yt_index = build_index(yt_doc)
            yt_engine = get_query_engine(yt_index)

            response = f"All done! Generating a short summary..."
            act = Activity(
                type="message",
                text=response,
                channel_data={"tokens_per_second": tps},
            )
            await turn_context.send_activity(act)

            yt_summary = build_summary_index(yt_doc)
            response = summary_index.get_document_summary(doc_id)
            act = Activity(
                type="message",
                text=response,
                channel_data={"tokens_per_second": tps},
            )
            await turn_context.send_activity(act)

        elif yt_doc and yt_index and yt_engine:
            response, tps = custom_engine_query(yt_engine, message)
        else:
            response = "Hey! Can you share a youtube link with me? I can't query a youtube video until you share a link with me :)"

        # Send the entire response as a single message
        act = Activity(
            type="message",
            text=response,
            channel_data={"tokens_per_second": tps},
        )
        await turn_context.send_activity(act)

    async def on_members_added_activity(self, members_added: ChannelAccount, turn_context: TurnContext):
        pass
