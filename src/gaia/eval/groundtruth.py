import json
from datetime import datetime
from pathlib import Path
from gaia.eval.claude import ClaudeClient
from gaia.logger import get_logger


class GroundTruthGenerator:
    """Generates ground truth data for RAG system evaluation using Claude."""

    DEFAULT_PROMPT = """
    Given this document, generate a set of short queries a user may ask about the document 
    and produce a set of ground truth answers to be used in validating a RAG system. 
    Include a summary of the document in the queries. Return a json formatted list of 
    query-response pairs formatted as follows:
    {
        'source': 'path/to/document',
        'summary': 'summarized document',
        'qa_pairs': [
            {'query': 'query1', 'response': 'response1'},
            {'query': 'query2', 'response': 'response2'},
            ...
        ]
    }
    """

    def __init__(self, model="claude-3-7-sonnet-20250219", max_tokens=1024):
        self.log = get_logger(__name__)
        self.claude = ClaudeClient(model=model, max_tokens=max_tokens)

    def generate(self, file_path, prompt=None, save_text=True, output_dir=None):
        """
        Generate ground truth data for a given document.

        Args:
            file_path (str): Path to the input document
            prompt (str, optional): Custom prompt for Claude. If None, uses DEFAULT_PROMPT
            save_text (bool): Whether to save extracted text for HTML files
            output_dir (str, optional): Directory to save output files. If None, uses same directory as input

        Returns:
            dict: Generated ground truth data with metadata
        """
        self.log.info(f"Generating ground truth data for: {file_path}")

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        prompt = prompt or self.DEFAULT_PROMPT

        try:
            # Generate analysis using Claude
            analysis = self.claude.analyze_file(
                str(file_path), prompt, save_text=save_text
            )
            token_count = self.claude.count_file_tokens(str(file_path), prompt)

            # Prepare output data with metadata
            output_data = {
                "metadata": {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "model": self.claude.model,
                    "source_file": str(file_path),
                    "prompt": prompt,
                    "token_count": token_count,
                },
                "analysis": json.loads(analysis),
            }

            # Save to file if output_dir specified
            if output_dir:
                output_dir = Path(output_dir)
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / f"{file_path.stem}.groundtruth.json"
            else:
                output_path = file_path.with_suffix(".groundtruth.json")

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, indent=2)
            self.log.info(f"Ground truth data saved to: {output_path}")

            return output_data

        except Exception as e:
            self.log.error(f"Error generating ground truth data: {e}")
            raise

    def generate_batch(self, input_dir, file_pattern="*.html", **kwargs):
        """
        Generate ground truth data for multiple documents in a directory.

        Args:
            input_dir (str): Directory containing input documents
            file_pattern (str): Glob pattern to match input files
            **kwargs: Additional arguments passed to generate()

        Returns:
            list: List of generated ground truth data for each document
        """
        input_dir = Path(input_dir)
        if not input_dir.is_dir():
            raise NotADirectoryError(f"Input directory not found: {input_dir}")

        results = []
        for file_path in input_dir.glob(file_pattern):
            self.log.info(f"Processing file: {file_path}")
            try:
                result = self.generate(file_path, **kwargs)
                results.append(result)
            except Exception as e:
                self.log.error(f"Error processing {file_path}: {e}")
                continue

        return results


if __name__ == "__main__":
    # Test the GroundTruthGenerator
    generator = GroundTruthGenerator()

    # Test single file processing
    # file_path = "./data/html/blender/introduction.html"
    # try:
    #     print(f"Processing single file: {file_path}")
    #     result = generator.generate(
    #         file_path, save_text=True, output_dir="./output/groundtruth"
    #     )
    #     print(f"Successfully generated ground truth data to ./output/groundtruth")
    #     print(f"Token count: {result['metadata']['token_count']}")
    #     print(f"Number of QA pairs: {len(result['analysis']['qa_pairs'])}")

    # except Exception as e:
    #     print(f"Error processing file: {e}")

    # Test batch processing
    input_dir = "./data/html/blender"
    try:
        print(f"\nProcessing directory: {input_dir}")
        results = generator.generate_batch(
            input_dir, file_pattern="*.html", output_dir="./output/groundtruth"
        )
        print(f"Successfully processed {len(results)} files")

    except Exception as e:
        print(f"Error processing directory: {e}")
