import argparse
from pathlib import Path
from typing import Optional, Union

from llama_index.core import (
    Settings,
    VectorStoreIndex,
    SimpleDirectoryReader,
    StorageContext,
    load_index_from_storage,
)
from gaia.agents.agent import Agent
from gaia.llm.llama_index_local import LocalLLM
from gaia.logger import get_logger


class MyAgent(Agent):
    def __init__(
        self, model, host="127.0.0.1", port=8001, cli_mode=False, input_file=None
    ):
        super().__init__(model=model, host=host, port=port, cli_mode=cli_mode)
        self.log = get_logger(__name__)

        # Define model
        self.log.info("Initializing LocalLLM...")
        self.llm = LocalLLM(
            prompt_llm_server=self.prompt_llm_server, stream_to_ui=self.stream_to_ui
        )
        Settings.llm = self.llm
        Settings.embed_model = "local:BAAI/bge-base-en-v1.5"

        self.index = None
        self.query_engine = None

        # Build index if input file is provided
        if input_file:
            try:
                self.log.info(f"Building index from {input_file}...")
                self.build_index(input_file)
                self.setup_query_engine()
                self.log.info("Index built successfully. Ready for queries.")
            except Exception as e:
                self.log.error(f"Error building index: {str(e)}")
                raise
        else:
            self.log.warning(
                "No input file provided. Please build or load an index before querying."
            )

        # Initialize agent server
        self.initialize_server()

    def build_index(
        self, input_path: Union[str, Path], output_path: Optional[str] = None
    ):
        """Build an index from input file/folder and optionally save to disk"""
        input_path = Path(input_path)
        if not input_path.exists():
            self.log.error(f"Input path {input_path} does not exist")
            raise ValueError(f"Input path {input_path} does not exist")

        # Load documents
        self.log.info(f"Loading documents from {input_path}...")
        documents = SimpleDirectoryReader(
            input_files=[str(input_path)] if input_path.is_file() else None,
            input_dir=str(input_path) if input_path.is_dir() else None,
        ).load_data()

        # Build index
        self.log.info("Building index...")
        self.index = VectorStoreIndex.from_documents(documents)

        # Save index if output path specified
        if output_path:
            output_path = Path(output_path)
            output_path.mkdir(parents=True, exist_ok=True)
            self.log.info(f"Saving index to {output_path}...")
            self.index.storage_context.persist(persist_dir=str(output_path))

        return self.index

    def load_index(self, index_path: Union[str, Path]):
        """Load an existing index from disk"""
        index_path = Path(index_path)
        if not index_path.exists():
            self.log.error(f"Index path {index_path} does not exist")
            raise ValueError(f"Index path {index_path} does not exist")

        self.log.info(f"Loading index from {index_path}...")
        storage_context = StorageContext.from_defaults(persist_dir=str(index_path))
        self.index = load_index_from_storage(storage_context)

        return self.index

    def setup_query_engine(self):
        """Setup the query engine for RAG"""
        if not self.index:
            self.log.error("No index loaded")
            raise ValueError("No index loaded. Please build or load an index first.")

        self.log.info("Setting up query engine...")
        self.query_engine = self.index.as_query_engine(
            verbose=False,
            similarity_top_k=3,
            response_mode="compact",
            streaming=True,
            system_prompt=(
                "[INST] <<SYS>>\n"
                "You are a helpful AI assistant that answers questions based on the provided context.\n"
                "Guidelines:\n"
                "- Answer questions using only the provided context\n"
                "- If you cannot answer from the context, say so\n"
                "- Be concise and clear in your responses\n"
                "<</SYS>>\n\n"
            ),
        )

    def prompt_received(self, prompt: str):
        """Handle incoming prompts"""
        if not self.query_engine:
            self.log.info("Setting up query engine for first use")
            self.setup_query_engine()

        self.log.debug(f"Received prompt: {prompt}")
        response = self.query_engine.query(prompt)
        return str(response)

    def prompt_stream(self, prompt: str):
        """Stream responses from the query engine"""
        if not self.query_engine:
            self.log.info("Setting up query engine for first use")
            self.setup_query_engine()

        self.log.debug(f"Streaming prompt: {prompt}")
        response = self.query_engine.query(prompt)
        yield str(response)


def main():
    parser = argparse.ArgumentParser(description="Run the RAG agent")
    parser.add_argument("--host", default="127.0.0.1", help="Host address")
    parser.add_argument("--port", type=int, default=8001, help="Port number")
    parser.add_argument("--model", required=True, help="Model name")
    args = parser.parse_args()

    MyAgent(model=args.model, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
