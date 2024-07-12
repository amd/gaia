# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

import re
# import time
import json
import os
import argparse
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
from llama_index.core.tools import FunctionTool
from llama_index.core.node_parser import SentenceSplitter
from llama_index.readers.youtube_transcript import YoutubeTranscriptReader

from gaia.agents.agent import Agent, LocalLLM

class MyAgent(Agent):
    def __init__(self, host, port):
        super().__init__(host, port)

        load_dotenv()
        youtube_api_key = os.getenv('YOUTUBE_API_KEY')
        assert youtube_api_key
        self.youtube = build("youtube", "v3", developerKey=youtube_api_key)

        # Define model
        self.llm = LocalLLM(prompt_llm_server=self.prompt_llm_server, stream_to_ui=self.stream_to_ui)
        Settings.llm = self.llm
        Settings.embed_model = "local:BAAI/bge-small-en-v1.5"

        Settings.chunk_size = 128
        Settings.chunk_overlap = 16
        self.similarity_top = 3

        # Initialize global variables
        self.yt_vector_index = None
        self.yt_query_engine = None
        self.yt_search_results = None

        # TODO
        # self.chat_history = []

        self.llm_states = [
            # state = 0, no index or search results produced yet.
            ("Index is currently not built and is empty.\n"
            "You need to perform YouTube search using the youtube_search tool before creating the index."),

            # state = 1, search results produced but no index created yet.
            ("Index is currently not built and is empty.\n"
            "YouTube search results have been found:\n"
            f"{self.yt_search_results}"
            "Ask the user which result to build index for.\n"),

            # state = 2, index is built, use the query engine.
            ("Index is currently built and is not empty.\n"
            "You can now use the query engine to fetch information about the video.\n"
            "To access the index, use the query engine RAG tool by calling: {\"query_engine\" : \"query\"}\n"),
        ]
        # set llm state 0-2
        self.llm_state = 0

        self.llm_system_prompt = (
            "[INST] <<SYS>>\n"
            "You are a YouTube-focused assistant called Clipy that helps user with YouTube by calling function tools.\n"
            "You are helpful by providing the necessary json-formatted queries in the form of {\"tool\" : \"query\"}:\n"
            "Do not include the results from the tools.\n"
            "In order to build the index, you have to first search YouTube.\n"
            "You only have ability to call the tools below, do not assume you have access to the output from the tools.\n"
            "1. {\"youtube_search\" : \"user_query\"}\n"
            "2. {\"build_index\" : \"video_id\"}\n"
            "3. {\"query_rag\" : \"user_query\"}\n"
            "4. {\"reset\" : \"\"}\n"
            "user_query is a query derived from the User comments.\n"
            "video_id is a video ID string from the YouTube search results.\n"
            "\n"
            "Your tasks:\n"
            "2. Output a json that will be used in an external search tool for YouTube videos\n"
            "3. Call tool that can build an index from a video.\n"
            "1. Chat about YouTube content once index is built\n"
            "4. Answer questions from user using the index\n"
            "\n"
            "Guidelines:\n"
            "- Answer a question given in a natural human-like manner.\n"
            "- Think step-by-step when answering questions.\n"
            "- When introducing yourself, keep it to just a single sentence, for example:\n"
            "\"Assistant: Hi, I can help you find information you're looking for on YouTube. Just ask me about any topic!\"\n"
            "- If no index exists, search YouTube and offer to build one\n"
            "- If an index does exist, use the query engine to answer questions.\n"
            "- If unsure, offer to search for more videos\n"
            "- Keep your answers short, concise and helpful\n"
            "- Search_query should be the subject of what the user is looking for, not a youtube link.\n"
            "- Do NOT provide search results, those are being provided by the external tools.\n"
            "- You can only provide the json formatted output to call the tools, you do not have access to the tools directly.\n"
            "Current state of index:\n"
            f"{self.llm_states[self.llm_state]}\n"
            "\n"
            "When using a tool, end your response with only the tool function call. Do not answer search results."
            "Always use the most relevant tool for each task.\n"
            "When needing to use a tool, your response should be formatted, here is an example script:\n"
            "User: What kind of philanthropy did Mr. Beast do?"
            "Assistant: To answer your question, I first need to search YouTube for the answer. Calling the following tool: {\"youtube_search\" : \"Mr Beast philanthropy\"} </s>\n"
            "<</SYS>>\n\n"
        )

        # this system prompt has been verified to work with llama v2 7b 4bit on NPU.
        self.query_engine_system_prompt = (
            "[INST] <<SYS>>\n"
            "{context_str}\n\n"
            "Think step-by-step to answer the query in a crisp, short and concise manner based on the information provided.\n"
            "If the answer does not exist in the given information, simply answer 'I don't know!'\n"
            "Do not mention or refer to the context or information provided in your response.\n"
            "Answer directly without any preamble or explanatory phrases about the source of your information.\n"
            "<</SYS>>\n\n"
            "{query_str} [/INST]\n"
        )

        # Initialize agent server
        self.initialize_server()

    def youtube_search(self, query, max_results=3):
        try:
            self.print(f"Running YouTube search with the following query: {query}")
            search_response = self.youtube.search().list( # pylint: disable=E1101
                q=query,
                type="video",
                part="id,snippet",
                maxResults=max_results
            ).execute()

            videos = []

            self.print("Found the following results:")
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
                self.print(f'{video["id"]} : {video["title"]}\n\n{video["description"]}\n{video["publish_time"]}    {video["video_id"]}\n\n')
                videos.append(video)

            print(videos)
            return videos

        except HttpError as e:
            print(f"An HTTP error {e.resp.status} occurred:\n{e.content}")
            return None

    def get_video_url(self, video_id:str):
        return f"https://www.youtube.com/watch?v={video_id}"

    def extract_json_data(self, input_string):
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
            return None, None

    def prompt_llm(self, query):
        response = ""
        new_card = True
        prompt = f"{self.llm_system_prompt}\nUser: {query} [/INST]\nAssistant: "
        # print(prompt)
        for chunk in self.prompt_llm_server(prompt=prompt):

            # Stream chunk to UI
            self.stream_to_ui(chunk, new_card=new_card)
            new_card = False

            response += chunk
        return response

    def prompt_received(self, prompt):
        print("Message received:", prompt)

        response = self.prompt_llm(prompt)
        print(response)

        key, value = self.extract_json_data(response)
        print(f"key: {key}, value: {value}, llm state: {self.llm_state}")
        print(self.llm_states[self.llm_state])

        if key == "youtube_search":
            self.yt_search_results = self.youtube_search(value, max_results=3)
            self.yt_vector_index = None
            self.yt_query_engine = None
            self.llm_state = 1

        if key == "build_index":
            video = self.yt_search_results[value]
            print(f"Fetching transcript from {video}.")
            video_id = video["video_id"]
            yt_url = [self.get_video_url(video_id)]
            yt_doc = self.get_youtube_transcript_doc(yt_url)
            yt_doc[0].doc_id = video_id

            self.yt_vector_index = self.build_vector_index(yt_doc)
            self.yt_query_engine = self.get_query_engine(self.yt_vector_index)

            print("Done! Index and query engine is now ready to be used on your PC. Feel free to ask any questions about the video!")
            self.llm_state = 2

        if key == "query_rag":
            query = value
            print(f"\nQuery: {query}")
            streaming_response = self.yt_query_engine.query(query)
            print("Answer: ", end="", flush=True)
            response = ""
            for text in streaming_response.response_gen:
                if text:
                    response += text
                    print(text, end="", flush=True)

        if key == "reset":
            self.yt_vector_index = None
            self.yt_query_engine = None
            self.yt_search_results = None
            self.llm_state = 0

    def chat_restarted(self):
        print("Client requested chat to restart")
        # self.prompt_llm("Hello, who are you?")
        self.print("Hi, I'm Clipy, an assistant that helps you search and find information on YouTube")
        self.yt_vector_index = None
        self.yt_query_engine = None
        self.yt_search_results = None

    def extract_youtube_link(self, message):
        youtube_link_pattern = r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/(?:watch\?v=)?(?:embed/)?(?:v/)?(?:shorts/)?(?:\S+)"
        match = re.search(youtube_link_pattern, message)
        if match:
            return match.group()
        else:
            return None

    def get_youtube_transcript_doc(self, yt_links: list) -> Document:
        self.print(f"Fetching YouTube transcript from {yt_links}")
        return YoutubeTranscriptReader().load_data(ytlinks=yt_links)

    def build_vector_index(self, doc: Document) -> VectorStoreIndex:
        self.print("Building vector index...")
        index = VectorStoreIndex.from_documents(doc, show_progress=True)
        self.print("Done!")
        return index

    def build_summary_index(self, doc: Document) -> DocumentSummaryIndex:
        # from https://docs.llamaindex.ai/en/stable/examples/index_structs/doc_summary/DocSummary/
        self.print("Building summary index")
        splitter = SentenceSplitter(chunk_size=1024)
        response_synthesizer = get_response_synthesizer(
            response_mode="tree_summarize", use_async=True
        )
        doc_summary_index = DocumentSummaryIndex.from_documents(
            doc,
            transformations=[splitter],
            response_synthesizer=response_synthesizer,
            show_progress=True,
        )
        return doc_summary_index

    def get_query_engine(self, index):
        self.print("Building RAG query engine.")
        qa_prompt_tmpl = PromptTemplate(self.query_engine_system_prompt)
        query_engine = index.as_query_engine(
            verbose=True,
            similarity_top_k=self.similarity_top,
            response_mode="compact",
            streaming=True,
        )
        query_engine.update_prompts(
            {"response_synthesizer:text_qa_template": qa_prompt_tmpl}
        )
        return query_engine

    def get_youtube_tool(self):
        return FunctionTool.from_defaults(fn=self.get_youtube_transcript_doc)

    def remove_color_formatting(self, text):
        # ANSI escape codes for color formatting
        ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
        return ansi_escape.sub("", text)


def main():
    # Clipy LLM CLI for testing purposes.
    parser = argparse.ArgumentParser(description="Interact with the Clipy Agent CLI")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host address for the agent server"
    )
    parser.add_argument(
        "--port", type=int, default=8001, help="Port for the agent server"
    )
    args = parser.parse_args()

    agent = MyAgent(host=args.host, port=args.port)
    print("Clipy Agent initialized. Type 'exit' to quit.")

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
