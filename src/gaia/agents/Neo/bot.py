import os
import re
import time
import subprocess
from dotenv import load_dotenv

# from src.gaia.agents.Neo.system_prompt import react_system_prompt_small

from llama_index.core import (
    VectorStoreIndex,
    SimpleDirectoryReader,
    SummaryIndex,
    Settings,
)
from llama_index.core.agent import ReActAgent
from llama_index.core.tools import QueryEngineTool, FunctionTool, ToolMetadata
from llama_index.readers.github import GithubRepositoryReader, GithubClient

from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import ChannelAccount, Activity

from llm.npu_llm import LocalLLM


def extract_github_owner_repo(message):
    github_link_pattern = (
        r"https?://(?:www\.)?github\.com/([a-zA-Z0-9_-]+)/([a-zA-Z0-9_-]+)"
    )
    match = re.search(github_link_pattern, message)
    if match:
        owner = match.group(1)
        repo = match.group(2)
        return owner, repo
    else:
        return None, None


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
    repo_engine = repo_index.as_query_engine(
        verbose=True,
        similarity_top_k=1,
        response_mode="compact",
        streaming=True,
    )

    # global repo_tool
    # repo_tool = QueryEngineTool(
    #     query_engine=repo_engine,
    #     metadata=ToolMetadata(
    #         name=f"{owner}/{repo}",
    #         description=(f"Provides information about {owner}/{repo} code repository. " "Use a detailed plain text question as input to the tool."),
    #     ),
    # )

    return f"Successfully created {owner}/{repo} repo index and tools!"


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


multiply_tool = FunctionTool.from_defaults(fn=multiply)
exe_tool = FunctionTool.from_defaults(fn=exe_command)

llm = LocalLLM()
Settings.llm = llm
Settings.embed_model = "local:BAAI/bge-base-en-v1.5"

# initialize ReAct agent
# TODO: Disable the ReAct agent for now due to slowness/bad UX.
# agent = ReActAgent.from_tools([multiply_tool, exe_tool], llm=llm, verbose=True, streaming=True, is_dummy_stream=True)
# agent.update_prompts({"agent_worker:system_prompt": react_system_prompt_small})

# use query engine instead for now.
# Settings.chunk_size = 64
# Settings.chunk_overlap = 0
# documents = SimpleDirectoryReader(
#     input_files=["./README_small.md"]
# ).load_data()
# index = VectorStoreIndex.from_documents(documents)

# query_engine = index.as_query_engine(
#     verbose=True,
#     similarity_top_k=1,
#     response_mode="compact",
#     streaming=True,
# )


import nest_asyncio

nest_asyncio.apply()


class MyBot(ActivityHandler):

    async def on_message_activity(self, turn_context: TurnContext):
        global repo_engine

        message = turn_context.activity.text
        owner, repo = extract_github_owner_repo(message)

        tps = 0
        if owner and repo:
            response = f"Thanks for sharing the link. Indexing {owner}/{repo} repo now."
            act = Activity(
                type="message",
                text=response,
                channel_data={"tokens_per_second": tps},
            )
            await turn_context.send_activity(act)
            create_repo_engine(owner, repo)
        else:
            # Send message to agent and get response
            query = turn_context.activity.text
            print(f"\nQuery: {query}")
            start_time = time.time()
            streaming_response = repo_engine.query(query)
            print("Answer: ", end="")
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

    async def on_members_added_activity(
        self, members_added: ChannelAccount, turn_context: TurnContext
    ):
        initial_greeting = "Hi I'm Neo, I can index github projects for you so you can easily query them. Just paste a link and I'll get on it! For example, 'please index this repo: https://github.com/onnx/turnkeyml'"
        for member_added in members_added:
            if member_added.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(initial_greeting)
