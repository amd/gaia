import os
import anthropic
import base64
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from gaia.logger import get_logger
import json
from pathlib import Path

load_dotenv()


class ClaudeClient:
    log = get_logger(__name__)

    def __init__(self, model="claude-3-7-sonnet-20250219", max_tokens=1024):
        self.log = self.__class__.log  # Use the class-level logger for instances
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            self.log.error("ANTHROPIC_API_KEY not found in environment")
            raise ValueError("ANTHROPIC_API_KEY not found in environment")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.log.info(f"Initialized ClaudeClient with model: {model}")

    def get_completion(self, prompt):
        self.log.info("Getting completion from Claude")
        self.log.debug(f"Prompt token count: {self.count_tokens(prompt)}")
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content
        except Exception as e:
            self.log.error(f"Error getting completion: {e}")
            raise

    def list_models(self):
        self.log.info("Retrieving available models")
        try:
            models = self.client.models.list(limit=20)
            self.log.info(f"Successfully retrieved {len(models)} models")
            return models
        except Exception as e:
            self.log.error(f"Error listing models: {e}")
            raise

    def count_tokens(self, prompt):
        return self.client.messages.count_tokens(
            model=self.model, messages=[{"role": "user", "content": prompt}]
        )

    def _convert_html_to_text(
        self, file_path, save_text=False, output_dir="./output/claude"
    ):
        """
        Convert HTML file content to plain text.

        Args:
            file_path (str): Path to the HTML file
            save_text (bool): If True, saves extracted text to a file

        Returns:
            str: Extracted text content
        """
        self.log.info("Converting HTML to text")
        with open(file_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
            text_content = soup.get_text(separator="\n", strip=True)
            self.log.debug(f"Extracted {len(text_content)} characters of text")

            if save_text:
                # Create output directory if it doesn't exist
                os.makedirs(output_dir, exist_ok=True)

                filename = Path(file_path).stem
                output_path = f"{output_dir}/{filename}.soup.txt"
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(text_content)
                self.log.info(f"Saved extracted text to: {output_path}")

            return text_content

    def analyze_file(
        self,
        file_path,
        prompt,
        media_type=None,
        save_text=False,
        output_dir="./output/claude",
    ):
        """
        Analyze a file using Claude's file understanding capabilities.

        Args:
            file_path (str): Path to the file to analyze
            prompt (str): The analysis prompt/question
            media_type (str, optional): The MIME type of the file. If None, will try to infer from extension
            save_text (bool, optional): If True, saves extracted text content to a file (for HTML files only)
            output_dir (str, optional): The directory to save the output file
        """
        self.log.info(f"Analyzing file: {file_path}")
        ext = os.path.splitext(file_path)[1].lower()

        try:
            # For HTML files, extract text using BeautifulSoup
            if ext in [".html", ".htm"]:
                text_content = self._convert_html_to_text(
                    file_path, save_text, output_dir
                )
                message = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[
                        {
                            "role": "user",
                            "content": f"Document content:\n\n{text_content}\n\n{prompt}",
                        }
                    ],
                )
                self.log.info("Successfully analyzed HTML content")
                return message.content[0].text

            # For other file types, use the original base64 encoding method
            mime_types = {
                ".txt": "text/plain",
                ".pdf": "application/pdf",
                ".md": "text/markdown",
                ".csv": "text/csv",
            }

            if media_type is None:
                media_type = mime_types.get(ext, "application/octet-stream")
                self.log.debug(f"Using media type: {media_type}")

            with open(file_path, "rb") as f:
                file_content = base64.b64encode(f.read()).decode("utf-8")
                self.log.debug(f"File encoded, size: {len(file_content)} bytes")

            self.log.info("Sending file for analysis")
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": file_content,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            self.log.info("Successfully analyzed file")
            return message.content[0].text

        except Exception as e:
            self.log.error(f"Error analyzing file: {e}")
            raise

    def count_file_tokens(
        self, file_path, prompt="", media_type=None, output_dir="./output/claude"
    ):
        """
        Count tokens for a file and optional prompt combination.

        Args:
            file_path (str): Path to the file to analyze
            prompt (str, optional): Additional prompt text to include in token count
            media_type (str, optional): The MIME type of the file. If None, will try to infer from extension

        Returns:
            int: Total token count
        """
        self.log.info(f"Counting tokens for file: {file_path}")
        ext = os.path.splitext(file_path)[1].lower()

        try:
            # For HTML files, count tokens of extracted text
            if ext in [".html", ".htm"]:
                text_content = self._convert_html_to_text(
                    file_path, save_text=False, output_dir=output_dir
                )
                content = f"Document content:\n\n{text_content}\n\n{prompt}"
                token_count = self.count_tokens(content)
                self.log.info(f"HTML file token count: {token_count.input_tokens}")
                return token_count.input_tokens

            # For other supported file types, encode and count
            mime_types = {
                ".txt": "text/plain",
                ".pdf": "application/pdf",
                ".md": "text/markdown",
                ".csv": "text/csv",
            }

            if media_type is None:
                media_type = mime_types.get(ext, "application/octet-stream")
                self.log.debug(f"Using media type: {media_type}")

            with open(file_path, "rb") as f:
                file_content = base64.b64encode(f.read()).decode("utf-8")

            message_content = [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": file_content,
                    },
                }
            ]

            if prompt:
                message_content.append({"type": "text", "text": prompt})

            token_count = self.client.messages.count_tokens(
                model=self.model,
                messages=[{"role": "user", "content": message_content}],
            )

            self.log.info(f"File token count: {token_count.input_tokens}")
            return token_count.input_tokens

        except Exception as e:
            self.log.error(f"Error counting tokens: {e}")
            raise


# Example usage
if __name__ == "__main__":
    client = ClaudeClient()

    # Test file analysis with Blender introduction document
    file_path = "./data/html/blender/introduction.html"
    prompt = (
        "Given this document, generate a set of short queries a user "
        "may ask about the document and produce a set of ground truth "
        "answers to be used in validating a RAG system. Include a "
        "summary of the document in the queries. Return a json "
        "formatted list of query-response pairs formatted as follows:"
        "{'source': 'path/to/document', 'summary': 'summarized document', "
        "'qa_pairs': [{'query': 'query1', 'response': 'response1'}, "
        "{'query': 'query2', 'response': 'response2'}, ...]}"
    )

    analysis = client.analyze_file(
        file_path, prompt, save_text=True, output_dir="./output/claude"
    )
    print(client.count_file_tokens(file_path, prompt))

    # Prepare enhanced output with metadata
    from datetime import datetime

    output_data = {
        "metadata": {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model": client.model,
            "source_file": file_path,
            "prompt": prompt,
            "token_count": client.count_file_tokens(file_path, prompt),
        },
        "analysis": json.loads(analysis),  # Parse JSON string into dictionary
    }

    # Save analysis to JSON file
    output_dir = "./output/claude"
    os.makedirs(output_dir, exist_ok=True)
    output_path = f"{output_dir}/{Path(file_path).stem}.out.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
    print(f"Analysis saved to: {output_path}")
