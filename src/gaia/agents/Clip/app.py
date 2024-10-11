# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

# TODOs:
# - add logger

import re
import json
import os
import time
from collections import deque
from dotenv import load_dotenv

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from llama_index.core import (
    VectorStoreIndex,
    Document,
    DocumentSummaryIndex,
    Settings,
    PromptTemplate,
    get_response_synthesizer,
)
from llama_index.core.agent import ReActAgent
from llama_index.core.tools import FunctionTool, QueryEngineTool, ToolMetadata
from llama_index.core.node_parser import SentenceSplitter
from llama_index.readers.youtube_transcript import YoutubeTranscriptReader

from gaia.logger import get_logger
from gaia.agents.agent import Agent, LocalLLM
from gaia.agents.Clip.prompts import Prompts

class MyAgent(Agent):
    """
    The YouTube assistant acts as a knowledgeable companion for
    YouTube-related tasks, providing search capabilities, index building, and
    query answering functionality. It aims to assist users in finding
    information, answering questions, and engaging in meaningful conversations
    about YouTube content.
    """

    def __init__(self, host="127.0.0.1", port=8001, embed_model="local:BAAI/bge-small-en-v1.5", llm_model = "llama-3.1-8b-instant"):
        super().__init__(host, port)

        load_dotenv()
        youtube_api_key = os.getenv('YOUTUBE_API_KEY')
        assert youtube_api_key
        self.youtube = build("youtube", "v3", developerKey=youtube_api_key)

        self.llm = LocalLLM(prompt_llm_server=self.prompt_llm_server)
        self.llm_server_uri = "ws://localhost:8000/ws"

        # Set up logging
        self.log = get_logger(__name__)

        Settings.llm = self.llm
        Settings.embed_model = embed_model

        Settings.chunk_size = 128
        Settings.chunk_overlap = 16
        self.similarity_top = 3

        # Initialize global variables
        self.summary_index = None
        self.vector_index = None
        self.query_engine = None
        self.search_results = None
        self.query_engine_tools = None
        self.react_agent = None

        self.n_chat_messages = 10
        self.chat_history = deque(maxlen=self.n_chat_messages * 2)  # Store both user and assistant messages

        self.llm_states = [
            # state = 0, no index or search results produced yet.
            ("Index is currently not built and is empty.\n"
            "You need to perform YouTube search using the youtube_search tool before creating the index."),

            # state = 1, search results produced but no index created yet.
            ("Index is currently not built and is empty.\n"
            "YouTube search results have been found:\n"
            f"{self.search_results}\n"
            "Ask the user which result to build the index for.\n"),

            # state = 2, search results produced but no index created yet.
            ("Index is currently built and is not empty.\n"
            "You can now use the query engine to fetch information about the video.\n"
            "To access the index, use the query engine RAG tool by calling: {\"query_engine\" : \"query\"}\n"),
        ]
        # set llm state 0-2
        self.llm_state = 0

        self.llm_system_prompt = Prompts.get_system_prompt(llm_model)

        # this system prompt has been verified to work with llama v2 7b 4bit on NPU.
        self.query_engine_system_prompt = Prompts.get_query_engine_prompt(llm_model)

        # Initialize agent server
        self.initialize_server()

    def youtube_search(self, query, max_results=3):
        """
        Perform a YouTube search with the given query and retrieve a list of videos.
        Args:
            query (str): The search query.
            max_results (int, optional): The maximum number of search results to retrieve. Defaults to 3.
        Returns:
            list: A list of dictionaries representing the videos found in the search results. Each dictionary contains the following keys:
                - id (int): The index of the video in the search results.
                - title (str): The title of the video.
                - description (str): The description of the video.
                - video_id (str): The ID of the video.
                - video_url (str): The URL of the video.
                - publish_time (str): The publish time of the video.
                - channel_title (str): The title of the channel that uploaded the video.
        Raises:
            HttpError: If an HTTP error occurs during the search.
        """
        try:
            msg = f"Running YouTube search with the following query: {query}"
            self.print(msg)
            self.chat_history.append(f"Asssistant: {msg}")
            search_response = self.youtube.search().list( # pylint: disable=E1101
                q=query,
                type="video",
                part="id,snippet",
                maxResults=max_results
            ).execute()

            videos = []
            msg = "Found the following result:"
            self.print(msg)
            self.chat_history.append(f"Asssistant: {msg}")
            for i, search_result in enumerate(search_response.get("items", [])):
                video_id = search_result["id"]["videoId"]
                video = {
                    "id": i,
                    "title": search_result["snippet"]["title"],
                    "description": search_result["snippet"]["description"],
                    "video_id": video_id,
                    "video_url": f"https://www.youtube.com/watch?v={video_id}",
                    "publish_time": search_result["snippet"]["publishTime"],
                    "channel_title": search_result["snippet"]["channelTitle"]
                }
                msg = f'{video["id"]} : {video["title"]}\n\n{video["description"]}\n{video["publish_time"]}    {video["video_id"]}\n\n'
                self.print(msg)
                self.chat_history.append(f"Asssistant: {msg}")
                videos.append(video)

            self.print(videos)
            return videos

        except HttpError as e:
            self.print(f"An HTTP error {e.resp.status} occurred:\n{e.content}")
            return None

    def get_video_url(self, video_id:str):
        """
        Returns the URL of a YouTube video based on the given video ID.
        Parameters:
        - video_id (str): The ID of the YouTube video.
        Returns:
        - str: The URL of the YouTube video.
        """
        return f"https://www.youtube.com/watch?v={video_id}"

    def extract_json_data(self, input_string):
        """
        Extracts the key and value from a JSON-formatted string.
        Args:
            input_string (str): The input string containing the JSON-formatted data.
        Returns:
            tuple: A tuple containing the key and value extracted from the JSON data.
                   If the input string does not contain valid JSON data, (None, None) is returned.
        """

        # Find the JSON-formatted part of the string
        json_match = re.search(r'\{.*?\}', input_string)

        if json_match:
            json_str = json_match.group()
            try:
                # Parse the JSON string
                json_data = json.loads(json_str)

                # Extract the key and value
                key, value = next(iter(json_data.items()))

                return key, value
            except json.JSONDecodeError:
                return None, None
        else:
            self.print("WARNING: No JSON data found in the input string.")
            return None, None

    def get_chat_history(self):
        return list(self.chat_history)

    def prompt_llm(self, query):
        """
        Prompt the LLM with a query and return the response.
        Args:
            query (str): The user's query.
        Returns:
            str: The response from the LLM.
        """
        response = ""
        self.chat_history.append(f"User: {query}")

        system_prompt = (
            f"{self.llm_system_prompt}\n"
            "Current state of index:\n"
            f"{self.llm_states[self.llm_state]}\n"
        )
        prompt = system_prompt + '\n'.join(self.chat_history) + "[/INST]\nAssistant: "

        # print(prompt)
        for chunk in self.prompt_llm_server(prompt=prompt):
            print(chunk, end="", flush=True)
            response += chunk
        print("\n")
        self.chat_history.append(f"Assistant: {response}")

        return response

    def reset(self):
        self.chat_history.clear()
        self.summary_index = None
        self.vector_index = None
        self.query_engine = None
        self.search_results = None
        self.llm_state = 0

    def chat_restarted(self):
        self.print("Client requested chat to restart")
        self.chat_history.clear()
        intro = "Hi, who are you in one sentence?"
        # print("User:", intro)
        try:
            self.prompt_llm(intro)
        except ConnectionRefusedError as e:
            self.print( f"Having trouble connecting to the LLM server, got:\n{str(e)}!")
        finally:
            self.summary_index = None
            self.vector_index = None
            self.query_engine = None
            self.search_results = None

    def extract_youtube_link(self, message):
        youtube_link_pattern = r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/(?:watch\?v=)?(?:embed/)?(?:v/)?(?:shorts/)?(?:\S+)"
        match = re.search(youtube_link_pattern, message)
        if match:
            return match.group()
        else:
            return None

    def extract_video_id(self, url):
        patterns = [
            r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?v=([^&]+)',
            r'(?:https?:\/\/)?(?:www\.)?youtu\.be\/([^?]+)',
            r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/embed\/([^?]+)',
            r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/v\/([^?]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    def get_youtube_transcript_doc(self, yt_links: list) -> Document:
        return YoutubeTranscriptReader().load_data(ytlinks=yt_links)

    def build_summary_index(self, doc: Document):
        # from https://docs.llamaindex.ai/en/stable/examples/index_structs/doc_summary/DocSummary/
        self.print("Building summary index")
        splitter = SentenceSplitter(chunk_size=1024)
        response_synthesizer = get_response_synthesizer(
            response_mode="tree_summarize", use_async=True
        )
        self.summary_index = DocumentSummaryIndex.from_documents(
            doc,
            transformations=[splitter],
            response_synthesizer=response_synthesizer,
            show_progress=True,
        )

    def print_summary(self, doc_id):
        self.print(self.summary_index.get_document_summary(doc_id))

    def build_vector_index(self, doc: Document) -> VectorStoreIndex:
        start_time = time.perf_counter()
        self.vector_index = VectorStoreIndex.from_documents(doc, show_progress=True)
        end_time = time.perf_counter()
        self.print(f"Done building index. Took {(end_time - start_time):.1f} seconds to build.")

    def build_query_engine(self):
        # print("Building RAG query engine.")
        assert self.vector_index, "Vector index is not built yet."
        qa_prompt_tmpl = PromptTemplate(self.query_engine_system_prompt)
        self.query_engine = self.vector_index.as_query_engine(
            verbose=True,
            similarity_top_k=self.similarity_top,
            response_mode="compact",
            streaming=True,
        )
        self.query_engine.update_prompts(
            {"response_synthesizer:text_qa_template": qa_prompt_tmpl}
        )

    def get_youtube_tool(self):
        return FunctionTool.from_defaults(fn=self.get_youtube_transcript_doc)

    def build_query_engine_tools(self, desc=None):
        assert self.query_engine, "Query engine is not built yet."
        self.query_engine_tools = [
            QueryEngineTool(
                query_engine=self.query_engine,
                metadata=ToolMetadata(
                    name="youtube",
                    description=(
                        f"YouTube transcript of {desc}. "
                        "Use a detailed plain text question as input to the tool."
                    ),
                ),
            ),
        ]

    def build_react_agent(self):
        # TODO: some questions to investigate:
        # - can we pass a new query engine tool that is created dynamically?
        # - can we stick with a ReAct agent for the whole interaction?
        # - how does "workflows" fit into this framework?
        self.react_agent = ReActAgent.from_tools(
            self.query_engine_tools,
            llm=self.llm,
            verbose=True,
        )

    def run(self, prompt):
        # print("Message received:", prompt)

        response = self.prompt_llm(prompt)
        # print(response)

        key, value = self.extract_json_data(response)
        # print(f"key: {key}, value: {value}, llm state: {self.llm_state}")
        # print(self.llm_states[self.llm_state])
        if key == "youtube_search":
            self.search_results = self.youtube_search(value, max_results=3)
            self.summary_index = None
            self.vector_index = None
            self.query_engine = None
            self.llm_state = 1

        elif key == "build_index":
            # value in this case is the video_id
            video = [vid for vid in self.search_results if vid["video_id"] == value][0]

            assert video, f"Video with video_id {value} not found in search results."
            msg = f"Fetching transcript from {video}."
            self.print(msg)
            self.chat_history.append(f"Asssistant: {msg}")

            video_id = video["video_id"]
            video_url = [self.get_video_url(video_id)]
            doc = self.get_youtube_transcript_doc(video_url)
            doc[0].doc_id = video_id

            # TODO: Fixme, sometimes summary index fails, possibly due to incorrect llama index version?
            # build and print summary index
            # self.build_summary_index(doc)
            # self.print_summary(video_id)

            self.build_vector_index(doc)
            self.build_query_engine()
            self.build_query_engine_tools(desc=video["description"])
            self.build_react_agent()

            msg = "Index and query engine is now ready to be used on your PC. Feel free to ask any questions about the video!"
            self.print(msg)
            self.chat_history.append(f"Asssistant: {msg}")
            self.llm_state = 2

        elif key == "query_rag":
            self.query_engine.query(value)

        elif key == "reset":
            msg = "Index and query engine are now cleared. Ready to search YouTube again!"
            self.print(msg)
        else:
            pass


def main():
    # Clip LLM CLI for testing purposes.
    import argparse
    parser = argparse.ArgumentParser(description="Interact with the Clip Agent CLI")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host address for the agent server"
    )
    parser.add_argument(
        "--port", type=int, default=8001, help="Port for the agent server"
    )
    args = parser.parse_args()

    agent = MyAgent(host=args.host, port=args.port)
    print("Clip Agent initialized. Type 'exit' to quit.")

    while True:
        try:
            user_input = input("You: ").strip()
            if user_input.lower() == "exit":
                print("Goodbye!")
                break
            elif user_input:
                print("Agent: ", end="", flush=True)
                agent.prompt_received(user_input)
            else:
                print("Please enter a valid input.")
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break


if __name__ == "__main__":
    main()
