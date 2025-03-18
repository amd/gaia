# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import re
import time
import subprocess

from llama_index.core import (
    VectorStoreIndex,
    # SimpleDirectoryReader,
    Settings,
)

from llama_index.core.tools import QueryEngineTool
from llama_index.core.node_parser import SentenceSplitter
from llama_index.readers.github import GithubRepositoryReader, GithubClient

from gaia.agents.agent import Agent
from gaia.llm.llama_index_local import LocalLLM

# TODO: uncomment when ReAct agent is enabled again.
# from llama_index.core.agent import ReActAgent
# from llama_index.core.tools import FunctionTool, ToolMetadata
# from gaia.agents.Neo.system_prompt import react_system_prompt_small


class MyAgent(Agent):
    def __init__(self, host="127.0.0.1", port=8001):
        super().__init__(host, port)

        self.repo_engine = None
        self.repo_tool = None

        # Define model
        Settings.llm = LocalLLM(
            prompt_llm_server=self.prompt_llm_server, stream_to_ui=self.stream_to_ui
        )
        Settings.embed_model = "local:BAAI/bge-base-en-v1.5"

        # TODO: try these settings.
        # Settings.chunk_size = 64
        # Settings.chunk_overlap = 0

        # Prepare query engine
        # qa_prompt_tmpl_str = (
        #     "<|user|>\n"
        #     "Context information is below.\n"
        #     "---------------------\n"
        #     "{context_str}\n"
        #     "---------------------\n"
        #     "Given the context information above I want you to think step by step to answer the query in a crisp manner, incase case you don't know the answer say 'I don't know!'.\n"
        #     "Keep you answers short and concise and end message with </s>.\n"
        #     "{query_str}</s>\n"
        #     "<|assistant|>"
        # )
        # qa_prompt_tmpl = PromptTemplate(qa_prompt_tmpl_str)
        # self.query_engine = index.as_query_engine(
        #     verbose=True,
        #     similarity_top_k=1,
        #     response_mode="compact",
        #     streaming=True,
        # )
        # self.query_engine.update_prompts(
        #     {"response_synthesizer:text_qa_template": qa_prompt_tmpl}
        # )

        # Initialize agent server
        self.initialize_server()

    def prompt_received(self, prompt):
        self.log.info("Message received:", prompt)
        owner, repo = self.extract_github_owner_repo(prompt)

        if owner and repo:
            self.print_ui(
                f"Thanks for sharing the link. Indexing {owner}/{repo} repo now."
            )
            self.create_repo_engine(owner, repo)
        else:
            # Send message to agent and get response
            self.log.info(f"\nQuery: {prompt}")
            response = self.repo_engine.query(prompt)
            self.log.info(f"Answer: {response}")

            # strip end characters
            response = response.rstrip("</s>")

    def chat_restarted(self):
        self.log.info("Client requested chat to restart")
        # self.chat_history.clear()
        intro = "Hi, who are you in one sentence?"
        # prompt = self.qa_prompt_tmpl_str + '\n'.join(f"User: {intro}") + "[/INST]\nAssistant: "
        prompt = "\n".join(f"User: {intro}") + "[/INST]\nAssistant: "
        self.log.info(f"User: {intro}")
        try:
            new_card = True
            for chunk in self.prompt_llm_server(prompt=prompt):

                # Stream chunk to UI
                self.stream_to_ui(chunk, new_card=new_card)
                new_card = False
                print(chunk, end="", flush=True)
            print("\n")

        except ConnectionRefusedError as e:
            self.print_ui(
                f"Having trouble connecting to the LLM server, got:\n{str(e)}!"
            )
            self.log.error(str(e))
        finally:
            self.print_ui(
                "I can index github projects for you so you can easily query them. Just paste a link and I'll get on it!\n"
                "For example, 'please index this repo: https://github.com/onnx/turnkeyml'"
            )

    def extract_github_owner_repo(self, message):
        github_link_pattern = (
            r"https?://(?:www\.)?github\.com/([a-zA-Z0-9_-]+)/([a-zA-Z0-9_-]+)"
        )
        match = re.search(github_link_pattern, message)
        if match:
            owner = match.group(1)
            repo = match.group(2)
            self.print_ui(f"Matched {owner} with {repo}!")
            return owner, repo
        else:
            self.print_ui(
                f"Did not find {owner} with {repo}, did you get the names wrong?"
            )
            return None, None

    def exe_command(self, command, folder=None):
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

    def create_repo_engine(self, owner: str, repo: str) -> QueryEngineTool:
        github_client = GithubClient(
            github_token=os.environ["GITHUB_TOKEN"], verbose=True
        )

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

        # Split documents into chunks
        node_parser = SentenceSplitter(chunk_size=256, chunk_overlap=20)
        nodes = node_parser.get_nodes_from_documents(repo_docs, show_progress=False)

        # build index
        # repo_index = VectorStoreIndex.from_documents(repo_docs, show_progress=True)
        repo_index = VectorStoreIndex(nodes, show_progress=True)

        # persist index
        repo_index.storage_context.persist(persist_dir="./storage/repo")

        self.repo_engine = repo_index.as_query_engine(
            verbose=True,
            similarity_top_k=1,
            response_mode="compact",
            streaming=True,
        )

        # self.repo_tool = QueryEngineTool(
        #     query_engine=self.repo_engine,
        #     metadata=ToolMetadata(
        #         name=f"{owner}/{repo}",
        #         description=(f"Provides information about {owner}/{repo} code repository. " "Use a detailed plain text question as input to the tool."),
        #     ),
        # )

        return f"Successfully created {owner}/{repo} repo index and tools!"

    def remove_color_formatting(self, text):
        # ANSI escape codes for color formatting
        ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
        return ansi_escape.sub("", text)

    def custom_agent_query(self, agent, query):
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

    # initialize ReAct agent
    # TODO: Disable the ReAct agent for now due to slowness/bad UX.
    # exe_tool = FunctionTool.from_defaults(fn=exe_command)
    # agent = ReActAgent.from_tools([exe_tool], llm=llm, verbose=True, streaming=True, is_dummy_stream=True)
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
