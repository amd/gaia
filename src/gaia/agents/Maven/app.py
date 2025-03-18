# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import json
import hashlib
import shutil
from collections import deque

from datetime import datetime, timedelta
from youtube_search import YoutubeSearch
import wikipedia

from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.agent import ReActAgent
from llama_index.core.objects import ObjectIndex

from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    VectorStoreIndex,
    PromptTemplate,
    SummaryIndex,
    StorageContext,
    load_index_from_storage,
)

from llama_index.llms.openai import OpenAI

from llama_index.readers.papers import ArxivReader
from llama_index.readers.youtube_transcript import YoutubeTranscriptReader
from llama_index.readers.web import SimpleWebPageReader

from llama_index.core.tools import QueryEngineTool, ToolMetadata
from llama_index.tools.duckduckgo import DuckDuckGoSearchToolSpec

from gaia.logger import get_logger
from gaia.agents.agent import Agent
from gaia.llm.llama_index_local import LocalLLM


class MyAgent(Agent):
    def __init__(self, host="127.0.0.1", port=8001, temp_dir="./temp"):
        super().__init__(host, port)

        self.log = get_logger(__name__)
        self.n_chat_messages = 4
        self.chat_history = deque(
            maxlen=self.n_chat_messages * 2
        )  # Store both user and assistant messages

        # Define model and settings
        Settings.llm = LocalLLM(
            prompt_llm_server=self.prompt_llm_server, stream_to_ui=self.stream_to_ui
        )
        Settings.embed_model = "local:BAAI/bge-base-en-v1.5"
        Settings.chunk_size = 128
        Settings.chunk_overlap = 0

        self.temp_dir = temp_dir
        self.wiki_output_dir = f"{temp_dir}/wiki_docs"

        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

        self.index_obj = {}
        self.next_state = "build_index"
        self.research_topic = "AVX512"
        self.number_of_videos = 3

        self.llm_system_prompt = (
            "[INST] <<SYS>>\n"
            "You are Maven, an AI PC assistant for research designed to help the user with any research topic.\n"
            "You are able to search the web and compile information, all done locally on the user's PC."
            "You are friendly, inquisitive and keep your answers short and concise.\n"
            "Your goal is to engage the User while providing helpful responses.\n"
            "\n"
            "Guidelines:\n"
            "- Analyze queries step-by-step for accurate, brief answers.\n"
            "- End each message with </s>.\n"
            "- Use a natural, conversational tone.\n"
            "- Avoid using expressions like *grins*, use emojis sparingly.\n"
            "- Show curiosity by asking relevant follow-up questions.\n"
            "- Break down complex problems when answering.\n"
            "- Introduce yourself in one friendly sentence.\n"
            "- Balance information with user engagement.\n"
            "- Adapt to the user's language style and complexity.\n"
            "- Admit uncertainty and ask for clarification when needed.\n"
            "- Respect user privacy.\n"
            "\n"
            "Prioritize helpful, engaging interactions within ethical bounds.\n"
            "<</SYS>>\n\n"
        )

        # FIXME: temporary
        self.build_pdf_index("./data/pdf")

        self.top_agent = None

        # Initialize agent server
        self.initialize_server()

    def prompt_llm(self, query):
        response = ""
        new_card = True
        self.chat_history.append(f"User: {query}")
        prompt = (
            self.llm_system_prompt
            + "\n".join(self.chat_history)
            + "[/INST]\nAssistant: "
        )

        for chunk in self.prompt_llm_server(prompt=prompt):

            # Stream chunk to UI
            self.stream_to_ui(chunk, new_card=new_card)
            new_card = False

            response += chunk
        self.chat_history.append(f"Assistant: {response}")
        return response

    def build_agents(self):
        # Build agents dictionary
        agents = {}
        query_engines = {}
        all_tools = []

        # Iterate over the combined documents
        for key, value in self.index_obj.items():
            self.log.debug(f"key: {key}, value: {value}")
            # Define query engines for vector and summary indexes
            vector_query_engine = value["vector_query_engine"]
            summary_query_engine = value["summary_query_engine"]

            # Define tools for querying the vector and summary indexes
            query_engine_tools = [
                QueryEngineTool(
                    query_engine=vector_query_engine,
                    metadata=ToolMetadata(
                        name="vector_tool",
                        description=(
                            f"Useful for questions related to specific aspects of {key}.\n\n"
                            "You are a research assistant providing accurate information from local PDFs, Wikipedia, YouTube, webpages, and arXiv papers. Follow these guidelines:\n"
                            "1. For broad overviews, historical context, and general information, use Wikipedia.\n"
                            "2. For in-depth research and technical details, use arXiv papers.\n"
                            "3. For the latest news and updates, use webpages.\n"
                            "4. For tutorials, demonstrations, reviews and feedback from the industry, use YouTube.\n"
                            "5. For specific, pre-verified documents, use local PDFs.\n"
                            "General Instructions:\n"
                            "Choose the source that best fits the query. Combine information from multiple sources when necessary. Ensure the information is accurate and relevant.\n\n"
                            "You must ALWAYS use at least one of the provided tools when answering a question; do NOT rely on prior knowledge alone."
                        ),
                    ),
                ),
                QueryEngineTool(
                    query_engine=summary_query_engine,
                    metadata=ToolMetadata(
                        name="summary_tool",
                        description=(
                            f"Useful for questions related to specific aspects of {key}.\n\n"
                            "You are a research assistant providing accurate information from local PDFs, Wikipedia, YouTube, webpages, and arXiv papers. Follow these guidelines:\n"
                            "1. For broad overviews, historical context, and general information, use Wikipedia.\n"
                            "2. For in-depth research and technical details, use arXiv papers.\n"
                            "3. For the latest news and updates, use webpages.\n"
                            "4. For tutorials, demonstrations, reviews and feedback from the industry, use YouTube.\n"
                            "5. For specific, pre-verified documents, use local PDFs.\n"
                            "General Instructions:\n"
                            "Choose the source that best fits the query. Combine information from multiple sources when necessary. Ensure the information is accurate and relevant.\n\n"
                            "You must ALWAYS use at least one of the provided tools when answering a question; do NOT rely on prior knowledge alone."
                        ),
                    ),
                ),
            ]

            # Build an OpenAI agent with the defined tools and a custom system prompt
            function_llm = OpenAI(
                model="gpt-4"
            )  # Initialize the LLM with GPT-4 model, FIXME: use phi3-mini
            agent = ReActAgent.from_tools(
                # agent = OpenAIAgent.from_tools(
                query_engine_tools,
                llm=function_llm,
                verbose=True,
                system_prompt=f"""\
        You are a specialized agent designed to answer queries about {self.research_topic}.
        You must ALWAYS use at least one of the tools provided when answering a question; do NOT rely on prior knowledge.\
        """,
            )

            # Store the agent and the query engine in their respective dictionaries
            agents[key] = agent
            query_engines[key] = vector_query_engine

            tool_summary = (
                f"Useful for questions related to specific aspects of {key}.\n\n"
                "You are a research assistant providing accurate information from local PDFs, Wikipedia, YouTube, webpages, and arXiv papers. Follow these guidelines:\n"
                "1. For broad overviews, historical context, and general information, use Wikipedia.\n"
                "2. For in-depth research and technical details, use arXiv papers.\n"
                "3. For the latest news and updates, use webpages.\n"
                "4. For tutorials, demonstrations, reviews and feedback from the industry, use YouTube.\n"
                "5. For specific, pre-verified documents, use local PDFs.\n"
                "General Instructions:\n"
                "Choose the source that best fits the query. Combine information from multiple sources when necessary. Ensure the information is accurate and relevant.\n\n"
                "You must ALWAYS use at least one of the provided tools when answering a question; do NOT rely on prior knowledge alone."
            )

            doc_tool = QueryEngineTool(
                query_engine=agents[key],
                metadata=ToolMetadata(
                    name=f"tool_{key}",
                    description=tool_summary,
                ),
            )
            all_tools.append(doc_tool)

        # define an "object" index and retriever over these tools
        obj_index = ObjectIndex.from_objects(
            all_tools,
            index_cls=VectorStoreIndex,
        )

        self.top_agent = ReActAgent.from_tools(
            # self.top_agent = OpenAIAgent.from_tools(
            tool_retriever=obj_index.as_retriever(
                similarity_top_k=3,
                verbose=True,
                streaming=True,
            ),
            system_prompt=(
                f"Useful for questions related to specific aspects of {self.research_topic}.\n\n"
                "You are a research assistant providing accurate information from local PDFs, Wikipedia, YouTube, webpages, and arXiv papers. Follow these guidelines:\n"
                "1. For broad overviews, historical context, and general information, use Wikipedia.\n"
                "2. For in-depth research and technical details, use arXiv papers.\n"
                "3. For the latest news and updates, use webpages.\n"
                "4. For tutorials, demonstrations, reviews and feedback from the industry, use YouTube.\n"
                "5. For specific, pre-verified documents, use local PDFs.\n"
                "General Instructions:\n"
                "Choose the source that best fits the query. Combine information from multiple sources when necessary. Ensure the information is accurate and relevant.\n\n"
                "You must ALWAYS use at least one of the provided tools when answering a question; do NOT rely on prior knowledge alone.\n"
                "Always reply in less than 50 words. Fo directly to yhe point and stop."
            ),
            verbose=True,
            streaming=True,
        )

    def _cleanup_temp_dir(self):
        # Check if the temporary directory exists
        if os.path.exists(self.temp_dir):
            try:
                # Remove the temporary directory and all its contents
                shutil.rmtree(self.temp_dir)
                self.log.info(f"Cleaned up temporary directory: {self.temp_dir}")
            except Exception as e:
                self.log.error(
                    f"Failed to delete temporary directory: {self.temp_dir}. Reason: {e}"
                )
        else:
            self.log.warning(f"Temporary directory does not exist: {self.temp_dir}")

    def _get_pdf_content(self, local_folder: str) -> str:
        pdf_files = [file for file in os.listdir(local_folder) if file.endswith(".pdf")]
        pdf_content = ""
        for pdf_file in pdf_files:
            pdf_path = os.path.join(local_folder, pdf_file)
            with open(pdf_path, "rb") as file:
                pdf_content += file.read().decode("utf-8", errors="ignore")

        return pdf_content

    def _get_persist_dirs(self, key: str, input_str: str):
        # Generate a hash based on the YouTube links
        hash_key = hashlib.md5(input_str.encode("utf-8")).hexdigest()
        vector_dir = f"{self.temp_dir}/{key}_vector_index/{hash_key}"
        summary_dir = f"{self.temp_dir}/{key}_summary_index/{hash_key}"
        return vector_dir, summary_dir

    def _load_index(self, vector_dir: str, summary_dir: str):
        storage_context = StorageContext.from_defaults(persist_dir=vector_dir)
        vector_index = load_index_from_storage(storage_context, verbose=True)
        storage_context = StorageContext.from_defaults(persist_dir=summary_dir)
        summary_index = load_index_from_storage(storage_context, verbose=True)
        return vector_index, summary_index

    def _build_index(self, documents, vector_dir: str, summary_dir: str):
        vector_index = VectorStoreIndex.from_documents(documents)
        nodes = SentenceSplitter().get_nodes_from_documents(documents, verbose=True)
        summary_index = SummaryIndex(nodes, verbose=True)

        # Persist the new index to the directory
        vector_index.storage_context.persist(persist_dir=vector_dir)
        summary_index.storage_context.persist(persist_dir=summary_dir)
        return vector_index, summary_index

    def _set_index_obj(self, key: str, vector_index, summary_index):
        vector_query_engine = vector_index.as_query_engine(
            verbose=True,
            similarity_top_k=1,
            response_mode="compact",
            streaming=True,
        )
        qa_prompt_tmpl_str = (
            "<|user|>\n"
            "Context information is below.\n"
            "---------------------\n"
            "{context_str}\n"
            "---------------------\n"
            "Given the context information above I want you to think step by step to answer the query in a crisp manner, incase case you don't know the answer say 'I don't know!'.\n"
            "Keep you answers short and concise.\n"
            "{query_str}</s>\n"
            "<|assistant|>"
        )

        qa_prompt_tmpl = PromptTemplate(qa_prompt_tmpl_str)
        vector_query_engine.update_prompts(
            {"response_synthesizer:text_qa_template": qa_prompt_tmpl}
        )
        # self.log.info(f"vector query engine prompts:\n{vector_query_engine.get_prompts()}")

        summary_query_engine = summary_index.as_query_engine(
            verbose=True,
            similarity_top_k=1,
            response_mode="compact",
            streaming=True,
        )
        # self.log.info(f"summary query engine prompts:\n{summary_query_engine.get_prompts()}")

        self.index_obj[key] = {"vector_index": vector_index}
        self.index_obj[key].update({"summary_index": summary_index})
        self.index_obj[key].update({"vector_query_engine": vector_query_engine})
        self.index_obj[key].update({"summary_query_engine": summary_query_engine})

    def build_pdf_index(self, local_folder: str):
        key = "pdf"
        # Generate a hash based on the content of all PDF files in the folder
        pdf_content_str = self._get_pdf_content(local_folder)
        vector_dir, summary_dir = self._get_persist_dirs(key, pdf_content_str)

        # Check if the index already exists for the given hash
        if os.path.exists(vector_dir) and os.path.exists(summary_dir):
            # Load the existing index from the persist directory
            self.log.info(
                f"Found and loading an existing index in {vector_dir} and {summary_dir}."
            )
            vector_index, summary_index = self._load_index(vector_dir, summary_dir)
            ret = f"SUCCESS: loaded existing {key} index."
        else:
            # Create a new index if it doesn't exist
            documents = SimpleDirectoryReader(local_folder).load_data()
            vector_index, summary_index = self._build_index(
                documents, vector_dir, summary_dir
            )
            ret = f"SUCCESS: {key} index from {local_folder} folder created."

        self._set_index_obj(key, vector_index, summary_index)

        return ret

    def _clean_wikipedia_text(self, page_text):
        lines = page_text.split("\n")
        cleaned_lines = []
        in_reference_section = False

        for line in lines:
            # Skip lines that are part of the references section
            if "References" in line:
                in_reference_section = True
            if in_reference_section:
                continue

            # Skip lines that are part of the contents or media
            if (
                line.strip()
                .lower()
                .startswith(
                    (
                        "contents",
                        "references",
                        "external links",
                        "see also",
                        "notes",
                        "further reading",
                    )
                )
            ):
                continue

            # Skip lines that are empty
            if not line.strip():
                continue

            cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    def _clean_wikipedia_text(self, page_text):
        lines = page_text.split("\n")
        cleaned_lines = []
        in_reference_section = False

        for line in lines:
            # Skip lines that are part of the references section
            if "References" in line:
                in_reference_section = True
            if in_reference_section:
                continue

            # Skip lines that are part of the contents or media
            if (
                line.strip()
                .lower()
                .startswith(
                    (
                        "contents",
                        "references",
                        "external links",
                        "see also",
                        "notes",
                        "further reading",
                    )
                )
            ):
                continue

            # Skip lines that are empty
            if not line.strip():
                continue

            cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    def _fetch_clean_wikipedia_page(self, topic):
        try:
            page = wikipedia.page(topic)
            cleaned_text = self._clean_wikipedia_text(page.content)
            return cleaned_text, page.title
        except wikipedia.exceptions.PageError:
            self.log.error(f"Wikipedia page for topic '{topic}' does not exist.")
            return None, None
        except wikipedia.exceptions.DisambiguationError as e:
            self.log.error(
                f"Disambiguation error for topic '{topic}'. Options are: {e.options}"
            )
            return None, None
        except Exception as e:
            self.log.error(f"An error occurred: {e}")
            return None, None

    def _get_wikipedia_documents(self, research_topic: str):
        cleaned_page_text, _ = self._fetch_clean_wikipedia_page(research_topic)

        if cleaned_page_text:
            # Define the directory and file name
            os.makedirs(
                self.wiki_output_dir, exist_ok=True
            )  # Ensure the directory exists
            output_file_path = os.path.join(
                self.wiki_output_dir, f"{research_topic}.txt"
            )

            # Write the cleaned text to a file
            with open(output_file_path, "w", encoding="utf-8") as file:
                file.write(cleaned_page_text)

            # Load the file using SimpleDirectoryReader
            reader = SimpleDirectoryReader(input_dir=self.wiki_output_dir)
            documents_wiki = reader.load_data()

            return documents_wiki

        else:
            self.log.error(
                f"Could not retrieve or clean Wikipedia page for topic '{research_topic}'."
            )
            return None

    def build_wikipedia_index(self, research_topic: str):
        key = "wiki"
        vector_dir, summary_dir = self._get_persist_dirs(key, research_topic)

        # Check if the index already exists for the given hash
        if os.path.exists(vector_dir) and os.path.exists(summary_dir):
            # Load the existing index from the persist directory
            self.log.info(
                f"Found and loading an existing index in {vector_dir} and {summary_dir}."
            )
            vector_index, summary_index = self._load_index(vector_dir, summary_dir)
            ret = f"SUCCESS: loaded existing {key} index."
        else:
            # Create a new index if it doesn't exist
            documents = self._get_wikipedia_documents(research_topic)
            vector_index, summary_index = self._build_index(
                documents, vector_dir, summary_dir
            )
            ret = f"SUCCESS: {key} index from {research_topic} topic created."

        self._set_index_obj(key, vector_index, summary_index)

        return ret

    def build_arxiv_index(self, research_topic: str):
        key = "arxiv"
        vector_dir, summary_dir = self._get_persist_dirs(key, research_topic)

        # Check if the index already exists for the given hash
        if os.path.exists(vector_dir) and os.path.exists(summary_dir):
            # Load the existing index from the persist directory
            self.log.info(
                f"Found and loading an existing index in {vector_dir} and {summary_dir}."
            )
            vector_index, summary_index = self._load_index(vector_dir, summary_dir)
            ret = f"SUCCESS: loaded existing {key} index."
        else:
            # Create a new index if it doesn't exist
            num_papers_read = 3
            retry_attempts = 3
            for attempt in range(retry_attempts):
                try:
                    documents = ArxivReader().load_data(
                        search_query=research_topic, max_results=num_papers_read
                    )
                    break
                except ValueError as e:
                    if "No files found in .papers" in str(e):
                        self.log.error(
                            f"Attempt {attempt + 1} failed: {e}. Retrying..."
                        )
                    else:
                        # Handle other ValueError exceptions
                        ret = f"An unexpected error occurred: {e}"
                        self.log.error(ret)
                        return ret
            else:
                # If all attempts fail, handle the error gracefully
                ret = f"Failed to load papers for topic '{research_topic}' after {retry_attempts} attempts."
                self.log.error(ret)
                return ret

            vector_index, summary_index = self._build_index(
                documents, vector_dir, summary_dir
            )

            ret = f"SUCCESS: {key} index from {research_topic} topic created."

        self._set_index_obj(key, vector_index, summary_index)

        return ret

    def _filter_and_sort_videos(
        self, json_data, max_duration_minutes=20, max_publish_time_years=1
    ):
        # Load the JSON data
        data = json.loads(json_data)

        # Function to convert duration from "MM:SS" or "HH:MM:SS" to seconds
        def duration_to_seconds(duration):
            parts = list(map(int, duration.split(":")))
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            return 0

        # Filter videos based on duration and publish time
        filtered_videos = []
        max_duration_seconds = max_duration_minutes * 60
        one_year_ago = datetime.now() - timedelta(days=max_publish_time_years * 365)

        for video in data["videos"]:
            duration_seconds = duration_to_seconds(video["duration"])

            publish_time_parts = video["publish_time"].split()
            try:
                publish_time_num = int(publish_time_parts[0])
            except ValueError:
                # Skip the video if the publish time is not a number
                continue

            publish_time_unit = publish_time_parts[1]
            publish_delta = timedelta(days=0)

            if "month" in publish_time_unit:
                publish_delta = timedelta(days=publish_time_num * 30)
            elif "year" in publish_time_unit:
                publish_delta = timedelta(days=publish_time_num * 365)

            publish_date = datetime.now() - publish_delta

            if (
                duration_seconds <= max_duration_seconds
                and publish_date >= one_year_ago
            ):
                filtered_videos.append(video)

        # Sort videos by views
        sorted_videos = sorted(
            filtered_videos,
            key=lambda x: int(x["views"].replace(" views", "").replace(",", "")),
            reverse=True,
        )

        # Get the full URLs of the top n videos
        top_n_urls = [
            "https://www.youtube.com/watch?v=" + video["id"]
            for video in sorted_videos[: self.number_of_videos]
        ]

        return top_n_urls

    def build_youtube_index(self, research_topic: str):
        key = "youtube"
        vector_dir, summary_dir = self._get_persist_dirs(key, research_topic)

        # Check if the index already exists for the given hash
        if os.path.exists(vector_dir) and os.path.exists(summary_dir):
            # Load the existing index from the persist directory
            self.log.info(
                f"Found and loading an existing index in {vector_dir} and {summary_dir}."
            )
            vector_index, summary_index = self._load_index(vector_dir, summary_dir)
            ret = f"SUCCESS: loaded existing {key} index."
        else:
            # Search YouTube
            json_data = YoutubeSearch(research_topic, max_results=10).to_json()
            yt_links = self._filter_and_sort_videos(json_data)

            # Create a new index if it doesn't exist
            documents = YoutubeTranscriptReader().load_data(ytlinks=yt_links)
            vector_index, summary_index = self._build_index(
                documents, vector_dir, summary_dir
            )
            ret = f"SUCCESS: {key} index created."

        self._set_index_obj(key, vector_index, summary_index)

        return ret

    def build_webpage_index(self, research_topic: str):
        key = "webpage"
        vector_dir, summary_dir = self._get_persist_dirs(key, research_topic)

        # Check if the index already exists for the given hash
        if os.path.exists(vector_dir) and os.path.exists(summary_dir):
            # Load the existing index from the persist directory
            self.log.info(
                f"Found and loading an existing index in {vector_dir} and {summary_dir}."
            )
            vector_index, summary_index = self._load_index(vector_dir, summary_dir)
            ret = f"SUCCESS: loaded existing {key} index."
        else:
            # Create a new index if it doesn't exist
            full_search = DuckDuckGoSearchToolSpec().duckduckgo_full_search(
                research_topic, max_results=3
            )
            urls = [article["href"] for article in full_search]
            documents = SimpleWebPageReader(html_to_text=True).load_data(urls)
            vector_index, summary_index = self._build_index(
                documents, vector_dir, summary_dir
            )
            ret = f"SUCCESS: {key} index from {research_topic} topic created."

        self._set_index_obj(key, vector_index, summary_index)

        return ret

    def _prompt_received(self, prompt):
        self.log.info("Message received:", prompt)

        if self.next_state == "build_index":
            # TODO: interact and extract research_topic, yt_links and local folder
            research_topic = prompt
            local_folder = "./data/pdf"
            self.log.info(
                f"Building indices using {research_topic} topic and {local_folder} folder."
            )

            self.build_pdf_index(local_folder)
            self.build_wikipedia_index(research_topic)
            self.build_arxiv_index(research_topic)
            self.build_youtube_index(research_topic)
            self.build_webpage_index(research_topic)
            self.next_state = "build_agent"
            self.stream_to_ui("Finished building indices!", new_card=True)

        elif self.next_state == "build_agent":
            self.log.info("Building agent!")
            self.build_agents()
            self.next_state = "interact_agent"
            self.stream_to_ui("Finished building agents!", new_card=True)

        elif self.next_state == "interact_agent":
            response = self.top_agent.query(prompt)
            self.log.info(f"Agent Response: {response}")

    def prompt_received(self, prompt):
        # query_engine = self.index_obj["pdf"]["vector_query_engine"]
        # response = query_engine.query(prompt)
        # self.test_pdf_engine(prompt)
        # self.test_wiki_engine(prompt)
        # self.test_arxiv_engine(prompt)
        # self.test_youtube_engine(prompt)
        # self.test_webpage_engine(prompt)
        self.test_agent(prompt)

    def test_pdf_engine(self, prompt):
        self.log.info("Message received:", prompt)
        self.build_pdf_index("./data/pdf")
        query_engine = self.index_obj["pdf"]["vector_query_engine"]
        response = query_engine.query(prompt)
        self.log.info(f"Agent Response: {response}")

    def test_wiki_engine(self, prompt):
        self.log.info("Message received:", prompt)
        self.build_wikipedia_index(prompt)
        query_engine = self.index_obj["wiki"]["vector_query_engine"]
        response = query_engine.query(prompt)
        self.log.info(f"Agent Response: {response}")

    def test_arxiv_engine(self, prompt):
        self.log.info("Message received:", prompt)
        self.build_arxiv_index(prompt)
        query_engine = self.index_obj["arxiv"]["vector_query_engine"]
        response = query_engine.query(prompt)
        self.log.info(f"Agent Response: {response}")

    def test_youtube_engine(self, prompt):
        self.log.info("Message received:", prompt)
        self.build_youtube_index(prompt)
        query_engine = self.index_obj["youtube"]["vector_query_engine"]
        response = query_engine.query(prompt)
        self.log.info(f"Agent Response: {response}")

    def test_webpage_engine(self, prompt):
        self.log.info("Message received:", prompt)
        self.build_webpage_index(prompt)
        query_engine = self.index_obj["webpage"]["vector_query_engine"]
        response = query_engine.query(prompt)
        self.log.info(f"Agent Response: {response}")

    def test_agent(self, prompt: str):
        self.log.info("Message received:", prompt)
        # build 2 indices
        self.build_pdf_index("./data/pdf")
        # self.build_wikipedia_index("avx512")
        self.build_agents()
        # test agent
        response = self.top_agent.query(prompt)
        self.log.info(f"Agent Response: {response}")

    def chat_restarted(self):
        self.log.info("Client requested chat to restart")
        self.chat_history.clear()
        intro = "Hi, who are you in one sentence?"
        self.log.info(f"User: {intro}")
        try:
            response = self.prompt_llm(intro)
            self.log.info(f"Response: {response}")
        except ConnectionRefusedError as e:
            self.print_ui(
                f"Having trouble connecting to the LLM server, got:\n{str(e)}!"
            )
            self.log.error(str(e))
        finally:
            self.next_state = "build_index"


if __name__ == "__main__":
    # px.launch_app()
    agent = MyAgent(host="127.0.0.1", port=8001)

    local_folder = "./data/pdf"
    research_topic = "AVX512"

    agent.test_arxiv_engine("What is SIMD width of avx 512?")

    # test query engines
    # agent.test_wiki_engine("What is SIMD width of avxvnni?")
    # agent.test_arxiv_engine("What is the difference between avx512 and avx2?")
    # agent.test_youtube_engine("Whats Ian's take on avx 512?")
    # agent.test_webpage_engine("What are the downsides of using avx512?")

    # # test agent
    # # historical context should use wikipedia
    # agent.test_agent(f"What was used before the invention of {research_topic}")
    # # technical details should refer to arxiv
    # agent.test_agent(f"Give me the technical details behind {research_topic}")
    # # for reviews and and feed back use youtube
    # agent.test_agent(
    #     f"What is the review or feedback from the industry on {research_topic}"
    # )
    # # latest news should use webpages/ wikipedia
    # agent.test_agent(f"What is the latest news and updates on {research_topic}")
    # # latest news should use webpages/ wikipedia
    # agent.test_agent(f"What are the registers used in {research_topic}")
