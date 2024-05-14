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


# define sample Tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers and returns the result integer"""
    return a * b


def exe_command(command, folder=None):
    """Windows command shell execution tool"""
    original_dir = None
    try:
        original_dir = os.getcwd()  # Store the original working directory

        if folder:
            # Change the current working directory to the specified folder
            os.chdir(folder)

        # Create a subprocess and pipe the stdout and stderr streams
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

        # Read and print the output and error streams in real-time
        for line in process.stdout:
            print(line, end="")
        for line in process.stderr:
            print(line, end="")

        # Wait for the subprocess to finish and get the return code
        return_code = process.wait()

        if return_code != 0:
            print(f"\nCommand exited with return code: {return_code}")
            return False
        else:
            return True

    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e}")
        return False
    finally:
        os.chdir(original_dir)  # Change back to the original working directory


repo_engine = None
repo_tool = None


def create_repo_engine(owner: str, repo: str) -> QueryEngineTool:
    github_client = GithubClient(github_token=os.environ["GITHUB_TOKEN"], verbose=True)

    repo_reader = GithubRepositoryReader(
        github_client=github_client,
        owner=owner,
        repo=repo,
        use_parser=False,
        verbose=True,
        filter_directories=(
            ["docs"],
            GithubRepositoryReader.FilterType.INCLUDE,
        ),
        filter_file_extensions=(
            [
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".svg",
                ".ico",
                "json",
                ".ipynb",
            ],
            GithubRepositoryReader.FilterType.EXCLUDE,
        ),
    )

    repo_docs = repo_reader.load_data(branch="main")

    # build index
    repo_index = VectorStoreIndex.from_documents(repo_docs, show_progress=True)

    # persist index
    repo_index.storage_context.persist(persist_dir="./storage/repo")

    global repo_engine
    repo_engine = repo_index.as_query_engine(similarity_top_k=3)

    global repo_tool
    repo_tool = QueryEngineTool(
        query_engine=repo_engine,
        metadata=ToolMetadata(
            name=f"{owner}/{repo}",
            description=(f"Provides information about {owner}/{repo} code repository. " "Use a detailed plain text question as input to the tool."),
        ),
    )

    return f"Successfully created {owner}/{repo} repo index, query engine and tool!"


def remove_color_formatting(text):
    # ANSI escape codes for color formatting
    ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    return ansi_escape.sub("", text)


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


# def custom_agent_query(agent, query):

#     # Redirect stdout to a variable
#     original_stdout = sys.stdout
#     captured_output = StringIO()
#     sys.stdout = captured_output

#     # Query agent
#     start_time = time.time()
#     agent.chat(query)
#     elapsed_time = time.time() - start_time

#     # Restore the original stdout
#     sys.stdout = original_stdout

#     # Parse response
#     messages = []
#     response = remove_color_formatting(captured_output.getvalue())
#     tps = len(response.split()) / elapsed_time
#     valid_message_types = ["thought", "action input", "observation", "answer", "assistant"]
#     for line in response.split("\n"):
#         message_type = line.split(":")[0].lower()
#         if message_type in valid_message_types:
#             # Only messages of type "message" appear in the main chat window
#             # if message_type == "answer":
#             if message_type == "assistant":
#                 message_type = "message"
#             message_content = " ".join(line.split(":")[1:])
#             messages.append([message_type, message_content, tps])

#     # Print captured message
#     print(captured_output.getvalue())

#     return messages

multiply_tool = FunctionTool.from_defaults(fn=multiply)
exe_tool = FunctionTool.from_defaults(fn=exe_command)

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
agent = ReActAgent.from_tools([multiply_tool, exe_tool], llm=llm, verbose=True, streaming=True, is_dummy_stream=True)
agent.update_prompts({"agent_worker:system_prompt": react_system_prompt_small})

# use query engine instead for now.
Settings.chunk_size = 64
Settings.chunk_overlap = 0
documents = SimpleDirectoryReader(
    # input_files=["./README_small.md"]
    input_files=["./data/jokes.txt"]
).load_data()
# index = SummaryIndex.from_documents(documents)
index = VectorStoreIndex.from_documents(documents)

query_engine = index.as_query_engine(
    verbose=True,
    # verbose=False,
    similarity_top_k=3,
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
        print("Answer: ", end="", flush=True)
        response = ""
        for text in streaming_response.response_gen:
            if text:
                response += text

                # Send streaming
                act = Activity(
                    type="streaming",
                    text=text,
                )
                await turn_context.send_activity(act)

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
