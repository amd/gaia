import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from enum import Enum
from gaia.eval.claude import ClaudeClient
from gaia.logger import get_logger


class UseCase(Enum):
    """Supported use cases for ground truth generation."""

    RAG = "rag"
    SUMMARIZATION = "summarization"
    QA = "qa"
    EMAIL = "email"


class GroundTruthGenerator:
    """Generates ground truth data for various evaluation use cases using Claude."""

    @staticmethod
    def get_default_prompt(use_case=UseCase.RAG, num_samples=5):
        """Generate default prompt based on use case."""
        if use_case == UseCase.RAG:
            return f"""
    Given this document, generate exactly {num_samples} short queries a user may ask about the document
    and produce a set of ground truth answers to be used in validating a RAG system.
    Include a summary of the document in the queries. Return a json formatted list of
    query-response pairs formatted as follows:
    {{
        'source': 'path/to/document',
        'summary': 'summarized document',
        'qa_pairs': [
            {{'query': 'query1', 'response': 'response1'}},
            {{'query': 'query2', 'response': 'response2'}},
            ...
        ]
    }}

    Generate exactly {num_samples} qa_pairs - no more, no less.
    
    IMPORTANT: Return ONLY the JSON object with no additional text, explanations, or formatting.
    """
        elif use_case == UseCase.SUMMARIZATION:
            return f"""
    Given this transcript, generate comprehensive ground truth summaries and metadata for evaluation.
    Analyze the content and provide different types of summaries with evaluation criteria.
    Return a json formatted response as follows:
    {{
        'source': 'path/to/transcript',
        'transcript_metadata': {{
            'estimated_duration': 'estimated meeting duration',
            'participant_count': 'estimated number of participants',
            'meeting_type': 'type of meeting (e.g., standup, planning, review)'
        }},
        'summaries': {{
            'executive_summary': 'high-level overview for executives',
            'detailed_summary': 'comprehensive summary with key details',
            'action_items': ['list', 'of', 'action', 'items'],
            'key_decisions': ['list', 'of', 'key', 'decisions'],
            'participants': ['list', 'of', 'identified', 'participants'],
            'topics_discussed': ['list', 'of', 'main', 'topics']
        }},
        'evaluation_criteria': {{
            'summary_completeness': 'how complete should a good summary be',
            'summary_accuracy': 'what constitutes accurate information extraction for summaries',
            'relevance': 'what information is most relevant to include',
            'structure': 'how should the summary be structured'
        }}
    }}

    Focus on generating comprehensive summaries and metadata for transcript summarization evaluation.
    
    IMPORTANT: Return ONLY the JSON object with no additional text, explanations, or formatting.
    """
        elif use_case == UseCase.QA:
            return f"""
    Given this transcript, generate exactly {num_samples} relevant questions with accurate answers for evaluation.
    Focus on questions that would commonly be asked about this type of meeting or conversation.
    Return a json formatted response as follows:
    {{
        'source': 'path/to/transcript',
        'transcript_metadata': {{
            'estimated_duration': 'estimated meeting duration',
            'participant_count': 'estimated number of participants',
            'meeting_type': 'type of meeting (e.g., standup, planning, review)'
        }},
        'qa_pairs': [
            {{'query': 'What were the main topics discussed in this meeting?', 'response': 'detailed answer based on transcript content'}},
            {{'query': 'What action items were assigned and to whom?', 'response': 'specific action items and assignees'}},
            {{'query': 'What decisions were made during this meeting?', 'response': 'key decisions and rationale'}},
            {{'query': 'Who participated in this meeting and what were their roles?', 'response': 'participant list and contributions'}},
            {{'query': 'What are the next steps or follow-up items?', 'response': 'future actions and timelines'}}
        ],
        'evaluation_criteria': {{
            'qa_accuracy': 'what constitutes accurate answers to questions about the transcript',
            'relevance': 'what information is most relevant to include in answers',
            'completeness': 'how complete should answers be for different question types'
        }}
    }}

    Generate exactly {num_samples} qa_pairs - no more, no less. Focus on questions that would be commonly asked about this type of meeting transcript.
    
    IMPORTANT: Return ONLY the JSON object with no additional text, explanations, or formatting.
    """
        elif use_case == UseCase.EMAIL:
            return f"""
    Given this business email, generate comprehensive ground truth summaries and analysis for evaluation.
    Analyze the email content and provide structured summaries with evaluation criteria.
    Return a json formatted response as follows:
    {{
        'source': 'path/to/email',
        'email_metadata': {{
            'email_type': 'type of email (e.g., project_update, customer_support, sales_outreach)',
            'sender_role': 'estimated role of sender',
            'recipient_type': 'type of recipients',
            'urgency_level': 'low/medium/high priority assessment'
        }},
        'summaries': {{
            'executive_summary': 'high-level overview of email purpose and content',
            'detailed_summary': 'comprehensive summary with key details and context',
            'key_points': ['list', 'of', 'main', 'points'],
            'action_items': ['list', 'of', 'action', 'items', 'or', 'requests'],
            'decisions_mentioned': ['list', 'of', 'decisions', 'or', 'announcements'],
            'follow_up_required': 'whether follow-up is needed and what type'
        }},
        'qa_pairs': [
            {{'query': 'What is the main purpose of this email?', 'response': 'detailed answer based on email content'}},
            {{'query': 'What action items or requests are mentioned?', 'response': 'specific actions requested'}},
            {{'query': 'What key information or updates are shared?', 'response': 'main information conveyed'}},
            {{'query': 'Who is the intended audience and what is expected of them?', 'response': 'recipient expectations and required responses'}},
            {{'query': 'What is the timeline or urgency level?', 'response': 'timing and priority information'}}
        ],
        'evaluation_criteria': {{
            'summary_completeness': 'how complete should a good email summary be',
            'summary_accuracy': 'what constitutes accurate information extraction for emails',
            'relevance': 'what information is most relevant to include',
            'context_understanding': 'how well should the business context be captured',
            'action_identification': 'how effectively should action items be identified'
        }}
    }}

    Focus on generating comprehensive summaries and analysis for business email evaluation. Always include exactly 5 qa_pairs.
    
    IMPORTANT: Return ONLY the JSON object with no additional text, explanations, or formatting.
    """
        else:
            raise ValueError(f"Unsupported use case: {use_case}")

    def __init__(self, model="claude-sonnet-4-20250514", max_tokens=4096):
        self.log = get_logger(__name__)
        self.claude = ClaudeClient(model=model, max_tokens=max_tokens)

    def generate(
        self,
        file_path,
        use_case=UseCase.RAG,
        prompt=None,
        save_text=True,
        output_dir=None,
        num_samples=5,
        save_file=True,
    ):
        """
        Generate ground truth data for a given document based on use case.

        Args:
            file_path (str): Path to the input document
            use_case (UseCase): The evaluation use case (RAG or transcript summarization)
            prompt (str, optional): Custom prompt for Claude. If None, uses default prompt for use case
            save_text (bool): Whether to save extracted text for HTML files
            output_dir (str, optional): Directory to save output files. If None, uses same directory as input
            num_samples (int): Number of Q&A pairs to generate (for RAG use case only, default: 5)

        Returns:
            dict: Generated ground truth data with metadata
        """
        self.log.info(
            f"Generating ground truth data for: {file_path} (use case: {use_case.value})"
        )

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        # Use appropriate prompt based on use case
        if prompt is None:
            prompt = self.get_default_prompt(use_case, num_samples)

        try:
            # Generate analysis using Claude with usage tracking
            response = self.claude.analyze_file_with_usage(
                str(file_path), prompt, save_text=save_text
            )
            analysis = response["content"]
            usage = response["usage"]
            cost = response["cost"]

            # Debug: Log the raw analysis response
            self.log.debug(
                f"Raw Claude response length: {len(analysis) if analysis else 0}"
            )
            self.log.debug(
                f"Raw Claude response preview: {analysis[:500] if analysis else 'None'}"
            )

            # Check if analysis is valid
            if not analysis or not analysis.strip():
                raise ValueError(
                    "Claude returned an empty response. This may be due to token limits or API issues."
                )

            # Try to parse the JSON response
            try:
                parsed_analysis = json.loads(analysis)
            except json.JSONDecodeError as je:
                # Try to extract JSON from the response if it's wrapped in text
                self.log.debug(
                    "Initial JSON parsing failed, attempting to extract JSON from response"
                )

                # Look for JSON content within the response
                start_idx = analysis.find("{")
                end_idx = analysis.rfind("}") + 1

                if start_idx >= 0 and end_idx > start_idx:
                    json_content = analysis[start_idx:end_idx]
                    try:
                        parsed_analysis = json.loads(json_content)
                        self.log.info("Successfully extracted JSON from response")
                    except json.JSONDecodeError:
                        self.log.error(
                            f"Failed to parse extracted JSON. Response content: {analysis}"
                        )
                        raise ValueError(
                            f"Claude response is not valid JSON: {str(je)}"
                        )
                else:
                    self.log.error(f"No JSON content found in response: {analysis}")
                    raise ValueError(
                        f"No valid JSON found in Claude response: {str(je)}"
                    )

            # Prepare output data with metadata including usage and cost
            output_data = {
                "metadata": {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "model": self.claude.model,
                    "source_file": str(file_path),
                    "use_case": use_case.value,
                    "prompt": prompt,
                    "num_samples_requested": (
                        num_samples if use_case == UseCase.RAG else None
                    ),
                    "usage": usage,
                    "cost": cost,
                },
                "analysis": parsed_analysis,
            }

            # Save to file if save_file is True
            if save_file:
                # Default output directory to ./groundtruth if not specified
                if output_dir is None:
                    output_dir = "./groundtruth"

                output_dir = Path(output_dir)
                output_dir.mkdir(parents=True, exist_ok=True)

                # If file_path is relative and has parent directories, preserve them
                if file_path.is_relative_to(Path.cwd()) and file_path.parent != Path(
                    "."
                ):
                    # Try to preserve relative directory structure
                    try:
                        relative_path = file_path.relative_to(Path.cwd())
                        relative_dir = relative_path.parent
                        output_subdir = output_dir / relative_dir
                        output_subdir.mkdir(parents=True, exist_ok=True)
                        output_path = (
                            output_subdir
                            / f"{file_path.stem}.{use_case.value}.groundtruth.json"
                        )
                    except ValueError:
                        # Fall back to flat structure if relative path calculation fails
                        output_path = (
                            output_dir
                            / f"{file_path.stem}.{use_case.value}.groundtruth.json"
                        )
                else:
                    output_path = (
                        output_dir
                        / f"{file_path.stem}.{use_case.value}.groundtruth.json"
                    )

                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(output_data, f, indent=2)
                self.log.info(f"Ground truth data saved to: {output_path}")

            return output_data

        except Exception as e:
            self.log.error(f"Error generating ground truth data: {e}")
            raise

    def generate_batch(
        self, input_dir, file_pattern="*", use_case=UseCase.RAG, **kwargs
    ):
        """
        Generate ground truth data for multiple documents in a directory.
        All results are automatically consolidated into a single JSON file.

        Args:
            input_dir (str): Directory containing input documents
            file_pattern (str): Glob pattern to match input files
            use_case (UseCase): The evaluation use case
            **kwargs: Additional arguments passed to generate()

        Returns:
            dict: Consolidated ground truth data for all documents
        """
        input_dir = Path(input_dir)
        if not input_dir.is_dir():
            raise NotADirectoryError(f"Input directory not found: {input_dir}")

        # Get output directory from kwargs, with fallback
        output_dir = kwargs.get("output_dir", "./output/groundtruth")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Remove output_dir from kwargs to avoid individual file saves in the generate method
        generate_kwargs = {k: v for k, v in kwargs.items() if k != "output_dir"}

        results = []
        individual_files = []

        # Collect all matching files recursively
        matching_files = list(input_dir.rglob(file_pattern))
        self.log.info(
            f"Found {len(matching_files)} files matching pattern '{file_pattern}' in {input_dir}"
        )

        if not matching_files:
            self.log.warning(
                f"No files found matching pattern '{file_pattern}' in {input_dir}"
            )
            # List directory contents for debugging
            try:
                all_files = list(input_dir.rglob("*"))
                self.log.info(
                    f"All files in directory: {[str(f) for f in all_files[:10]]}"
                )  # Show first 10
            except Exception as e:
                self.log.error(f"Error listing directory contents: {e}")

        for match in matching_files:
            self.log.info(f"  Found: {match}")

        # Create progress tracking directory
        progress_dir = output_dir / f"groundtruth_{use_case.value}_progress"
        progress_dir.mkdir(parents=True, exist_ok=True)

        for i, file_path in enumerate(matching_files):
            self.log.info(f"Processing file {i+1}/{len(matching_files)}: {file_path}")
            file_start_time = time.time()

            try:
                # Generate without saving individual files by passing save_file=False
                result = self.generate(
                    file_path, use_case=use_case, save_file=False, **generate_kwargs
                )
                results.append(result)

                # Calculate relative path from input_dir to preserve directory structure
                relative_path = file_path.relative_to(input_dir)
                relative_dir = relative_path.parent

                # Create output directory structure
                output_subdir = output_dir / relative_dir
                output_subdir.mkdir(parents=True, exist_ok=True)

                # Keep track of what would be the individual file path for consolidation
                individual_file_path = (
                    output_subdir
                    / f"{file_path.stem}.{use_case.value}.groundtruth.json"
                )

                # Save individual file (this provides immediate incremental progress)
                with open(individual_file_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
                individual_files.append(individual_file_path)

                # Write progress tracking information
                file_time = time.time() - file_start_time
                progress_file = progress_dir / f"file_{i+1:04d}_progress.json"
                progress_data = {
                    "file_index": i,
                    "file_path": str(file_path),
                    "individual_output_path": str(individual_file_path),
                    "processing_time_seconds": round(file_time, 3),
                    "usage": result.get("metadata", {}).get("usage", {}),
                    "cost": result.get("metadata", {}).get("cost", {}),
                    "timestamp": datetime.now().isoformat(),
                    "status": "completed",
                }

                with open(progress_file, "w", encoding="utf-8") as f:
                    json.dump(progress_data, f, indent=2)

                # Update overall progress
                overall_progress_file = progress_dir / "overall_progress.json"
                overall_progress_data = {
                    "total_files": len(matching_files),
                    "completed_files": i + 1,
                    "progress_percent": round((i + 1) / len(matching_files) * 100, 1),
                    "use_case": use_case.value,
                    "last_updated": datetime.now().isoformat(),
                }

                with open(overall_progress_file, "w", encoding="utf-8") as f:
                    json.dump(overall_progress_data, f, indent=2)

                self.log.info(
                    f"Ground truth progress: {i+1}/{len(matching_files)} files completed ({overall_progress_data['progress_percent']}%)"
                )

            except Exception as e:
                self.log.error(f"Error processing {file_path}: {e}")

                # Write error progress information
                progress_file = progress_dir / f"file_{i+1:04d}_progress.json"
                progress_data = {
                    "file_index": i,
                    "file_path": str(file_path),
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                    "status": "error",
                }

                with open(progress_file, "w", encoding="utf-8") as f:
                    json.dump(progress_data, f, indent=2)

                continue

        if not results:
            self.log.warning("No files were processed successfully")
            return None

        # Consolidate all results into a single file
        try:
            self.log.info(
                f"Consolidating {len(results)} ground truth files into single JSON"
            )

            # Use the consolidate method to create the final consolidated file
            consolidated_file_pattern = f"*.{use_case.value}.groundtruth.json"
            consolidated_output_path = (
                output_dir / f"consolidated_{use_case.value}_groundtruth.json"
            )

            consolidated_data = self.consolidate_groundtruth(
                input_dir=output_dir,
                output_path=consolidated_output_path,
                file_pattern=consolidated_file_pattern,
            )

            # Keep individual files - don't clean them up
            self.log.info(f"Individual ground truth files saved in: {output_dir}")
            self.log.info(
                f"Consolidated ground truth file saved as: {consolidated_output_path}"
            )

            self.log.info(
                f"Consolidated ground truth data saved to: {consolidated_output_path}"
            )
            self.log.info(f"Total files processed: {len(results)}")

            # Return the consolidated data instead of individual results
            return consolidated_data

        except Exception as e:
            self.log.error(f"Error during consolidation: {e}")
            # If consolidation fails, return individual results
            self.log.warning("Falling back to individual results")
            return results
        finally:
            # Clean up progress directory after successful completion
            try:
                import shutil

                if progress_dir.exists():
                    shutil.rmtree(progress_dir)
                    self.log.info(
                        f"Cleaned up progress tracking files from: {progress_dir}"
                    )
            except Exception as e:
                self.log.warning(
                    f"Failed to clean up progress directory {progress_dir}: {e}"
                )

    def consolidate_groundtruth(
        self,
        input_dir,
        output_path=None,
        file_pattern="*.summarization.groundtruth.json",
    ):
        """
        Consolidate multiple ground truth files into a single JSON file for easier evaluation.

        Args:
            input_dir (str): Directory containing ground truth files
            output_path (str, optional): Path for consolidated output file. If None, creates in input_dir
            file_pattern (str): Glob pattern to match ground truth files

        Returns:
            dict: Consolidated ground truth data
        """
        input_dir = Path(input_dir)
        if not input_dir.is_dir():
            raise NotADirectoryError(f"Input directory not found: {input_dir}")

        # Find all matching ground truth files
        gt_files = list(input_dir.rglob(file_pattern))
        if not gt_files:
            raise FileNotFoundError(
                f"No ground truth files found matching pattern: {file_pattern}"
            )

        self.log.info(f"Consolidating {len(gt_files)} ground truth files")

        consolidated_data = {
            "metadata": {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "consolidated_from": len(gt_files),
                "source_files": [],
                "total_usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "total_cost": {
                    "input_cost": 0.0,
                    "output_cost": 0.0,
                    "total_cost": 0.0,
                },
            },
            "analysis": {
                "summaries": {},
                "transcript_metadata": {},
                "qa_pairs": {},
                "evaluation_criteria": {},
            },
        }

        # Process each ground truth file
        for gt_file in gt_files:
            try:
                with open(gt_file, "r", encoding="utf-8") as f:
                    gt_data = json.load(f)

                # Extract transcript identifier from filename (handle all use cases)
                transcript_id = gt_file.stem
                for use_case in UseCase:
                    transcript_id = transcript_id.replace(
                        f".{use_case.value}.groundtruth", ""
                    )

                # Store source file info
                consolidated_data["metadata"]["source_files"].append(
                    {
                        "transcript_id": transcript_id,
                        "file_path": str(gt_file),
                        "source_file": gt_data["metadata"].get("source_file", ""),
                        "timestamp": gt_data["metadata"].get("timestamp", ""),
                    }
                )

                # Aggregate usage and cost data
                usage = gt_data["metadata"].get("usage", {})
                cost = gt_data["metadata"].get("cost", {})

                consolidated_data["metadata"]["total_usage"][
                    "input_tokens"
                ] += usage.get("input_tokens", 0)
                consolidated_data["metadata"]["total_usage"][
                    "output_tokens"
                ] += usage.get("output_tokens", 0)
                consolidated_data["metadata"]["total_usage"][
                    "total_tokens"
                ] += usage.get("total_tokens", 0)

                consolidated_data["metadata"]["total_cost"]["input_cost"] += cost.get(
                    "input_cost", 0.0
                )
                consolidated_data["metadata"]["total_cost"]["output_cost"] += cost.get(
                    "output_cost", 0.0
                )
                consolidated_data["metadata"]["total_cost"]["total_cost"] += cost.get(
                    "total_cost", 0.0
                )

                # Store analysis data with transcript ID as key
                analysis = gt_data.get("analysis", {})
                consolidated_data["analysis"]["summaries"][transcript_id] = (
                    analysis.get("summaries", {})
                )
                consolidated_data["analysis"]["transcript_metadata"][transcript_id] = (
                    analysis.get("transcript_metadata", {})
                )

                # Store qa_pairs if present (for QA use case)
                if "qa_pairs" in analysis:
                    consolidated_data["analysis"]["qa_pairs"][transcript_id] = (
                        analysis.get("qa_pairs", [])
                    )

                # Store evaluation criteria (should be similar across transcripts, but keep first one)
                if not consolidated_data["analysis"]["evaluation_criteria"]:
                    consolidated_data["analysis"]["evaluation_criteria"] = analysis.get(
                        "evaluation_criteria", {}
                    )

                self.log.debug(f"Processed ground truth file: {gt_file}")

            except Exception as e:
                self.log.error(f"Error processing {gt_file}: {e}")
                continue

        # Set output path if not provided
        if output_path is None:
            output_path = input_dir / "consolidated_groundtruth.json"
        else:
            output_path = Path(output_path)

        # Save consolidated data
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(consolidated_data, f, indent=2, ensure_ascii=False)

        self.log.info(f"Consolidated ground truth saved to: {output_path}")
        self.log.info(f"Total files: {len(gt_files)}")
        self.log.info(
            f"Total cost: ${consolidated_data['metadata']['total_cost']['total_cost']:.4f}"
        )

        return consolidated_data


def main():
    """Command line interface for ground truth generation."""
    parser = argparse.ArgumentParser(
        description="Generate ground truth data for various evaluation use cases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process a single file for RAG evaluation (default)
  python -m gaia.eval.groundtruth -f ./data/html/blender/introduction.html

  # Process a transcript for summary generation
  python -m gaia.eval.groundtruth -f ./data/transcripts/meeting.txt --use-case summarization

  # Process a transcript for Q&A generation
  python -m gaia.eval.groundtruth -f ./data/transcripts/meeting.txt --use-case qa

  # Process all HTML files in a directory for RAG (creates consolidated file)
  python -m gaia.eval.groundtruth -d ./data/html/blender

  # Process transcript files for summarization (creates consolidated file)
  python -m gaia.eval.groundtruth -d ./data/transcripts -p "*.txt" --use-case summarization

  # Process transcript files for Q&A generation (creates consolidated file)
  python -m gaia.eval.groundtruth -d ./data/transcripts -p "*.txt" --use-case qa

  # Process with custom output directory
  python -m gaia.eval.groundtruth -f ./data/html/intro.html -o ./output/gt

  # Use custom Claude model
  python -m gaia.eval.groundtruth -f ./data/doc.html -m claude-3-opus-20240229

  # Generate 10 Q&A pairs per document (RAG only)
  python -m gaia.eval.groundtruth -d ./data/html/blender --num-samples 10

  # Consolidate multiple ground truth files into one
  python -m gaia.eval.groundtruth -d ./output/groundtruth --consolidate
        """,
    )

    # Input source (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "-f", "--file", type=str, help="Path to a single document file to process"
    )
    input_group.add_argument(
        "-d", "--directory", type=str, help="Directory containing documents to process"
    )

    # Optional arguments
    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default="./output/groundtruth",
        help="Output directory for generated ground truth files (default: ./output/groundtruth)",
    )
    parser.add_argument(
        "-p",
        "--pattern",
        type=str,
        default="*.html",
        help="File pattern to match when processing directory (default: *.html)",
    )
    parser.add_argument(
        "-u",
        "--use-case",
        type=str,
        choices=[uc.value for uc in UseCase],
        default=UseCase.RAG.value,
        help=f"Use case for ground truth generation (default: {UseCase.RAG.value})",
    )
    parser.add_argument(
        "-m",
        "--model",
        type=str,
        default="claude-sonnet-4-20250514",
        help="Claude model to use (default: claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Maximum tokens for Claude responses (default: 4096)",
    )
    parser.add_argument(
        "--no-save-text",
        action="store_true",
        help="Don't save extracted text for HTML files",
    )
    parser.add_argument(
        "--custom-prompt",
        type=str,
        help="Path to a file containing a custom prompt for Claude",
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=5,
        help="Number of Q&A pairs to generate per document (RAG use case only, default: 5)",
    )
    parser.add_argument(
        "--consolidate",
        action="store_true",
        help="Consolidate multiple ground truth files into a single file",
    )

    args = parser.parse_args()

    # Convert use case string to enum
    use_case = UseCase(args.use_case)

    # Initialize generator
    try:
        generator = GroundTruthGenerator(model=args.model, max_tokens=args.max_tokens)
    except Exception as e:
        print(f"Error initializing generator: {e}")
        return 1

    # Load custom prompt if provided
    custom_prompt = None
    if args.custom_prompt:
        try:
            with open(args.custom_prompt, "r", encoding="utf-8") as f:
                custom_prompt = f.read().strip()
            print(f"Using custom prompt from: {args.custom_prompt}")
        except Exception as e:
            print(f"Error loading custom prompt: {e}")
            return 1

    save_text = not args.no_save_text

    try:
        if args.consolidate:
            # Consolidate mode - directory is required
            if not args.directory:
                print("Error: --consolidate requires --directory (-d) to be specified")
                return 1

            # Use the specified use case for the consolidation pattern
            consolidate_pattern = f"*.{use_case.value}.groundtruth.json"
            print(f"Consolidating ground truth files from: {args.directory}")
            print(f"Pattern: {consolidate_pattern}")
            result = generator.consolidate_groundtruth(
                input_dir=args.directory,
                output_path=Path(args.output_dir)
                / f"consolidated_{use_case.value}_groundtruth.json",
                file_pattern=consolidate_pattern,
            )
            print(
                f"✅ Successfully consolidated {result['metadata']['consolidated_from']} files"
            )
            print(
                f"  Output: {Path(args.output_dir) / f'consolidated_{use_case.value}_groundtruth.json'}"
            )
            print(
                f"  Total cost: ${result['metadata']['total_cost']['total_cost']:.4f}"
            )
            print(
                f"  Total tokens: {result['metadata']['total_usage']['total_tokens']:,}"
            )

        elif args.file:
            # Process single file
            print(f"Processing single file: {args.file} (use case: {use_case.value})")
            result = generator.generate(
                file_path=args.file,
                use_case=use_case,
                prompt=custom_prompt,
                save_text=save_text,
                output_dir=args.output_dir,
                num_samples=args.num_samples,
            )
            print(f"✅ Successfully generated ground truth data")
            print(f"  Output: {args.output_dir}")
            usage = result["metadata"]["usage"]
            cost = result["metadata"]["cost"]
            print(
                f"  Token usage: {usage['input_tokens']:,} input + {usage['output_tokens']:,} output = {usage['total_tokens']:,} total"
            )
            print(
                f"  Cost: ${cost['input_cost']:.4f} input + ${cost['output_cost']:.4f} output = ${cost['total_cost']:.4f} total"
            )

            # Different output based on use case
            if use_case == UseCase.RAG:
                qa_pairs_count = len(result["analysis"]["qa_pairs"])
                print(
                    f"  Q&A pairs: {qa_pairs_count} (${cost['total_cost']/qa_pairs_count:.4f} per pair)"
                )
            elif use_case == UseCase.SUMMARIZATION:
                print(
                    f"  Summary types generated: {len(result['analysis']['summaries'])} different formats"
                )
                print(
                    f"  Evaluation criteria: {len(result['analysis']['evaluation_criteria'])} categories"
                )
            elif use_case == UseCase.QA:
                print(f"  Q&A pairs: {len(result['analysis']['qa_pairs'])}")
                print(
                    f"  Evaluation criteria: {len(result['analysis']['evaluation_criteria'])} categories"
                )

        elif args.directory:
            # Process directory
            print(
                f"Processing directory: {args.directory} (use case: {use_case.value})"
            )
            print(f"File pattern: {args.pattern}")
            results = generator.generate_batch(
                input_dir=args.directory,
                file_pattern=args.pattern,
                use_case=use_case,
                prompt=custom_prompt,
                save_text=save_text,
                output_dir=args.output_dir,
                num_samples=args.num_samples,
            )

            if results:
                total_usage = {
                    "input_tokens": sum(
                        r["metadata"]["usage"]["input_tokens"] for r in results
                    ),
                    "output_tokens": sum(
                        r["metadata"]["usage"]["output_tokens"] for r in results
                    ),
                    "total_tokens": sum(
                        r["metadata"]["usage"]["total_tokens"] for r in results
                    ),
                }
                total_cost = {
                    "input_cost": sum(
                        r["metadata"]["cost"]["input_cost"] for r in results
                    ),
                    "output_cost": sum(
                        r["metadata"]["cost"]["output_cost"] for r in results
                    ),
                    "total_cost": sum(
                        r["metadata"]["cost"]["total_cost"] for r in results
                    ),
                }
                print(f"✅ Successfully processed {len(results)} files")
                print(f"  Output: {args.output_dir}")
                print(
                    f"  Total token usage: {total_usage['input_tokens']:,} input + {total_usage['output_tokens']:,} output = {total_usage['total_tokens']:,} total"
                )
                print(
                    f"  Total cost: ${total_cost['input_cost']:.4f} input + ${total_cost['output_cost']:.4f} output = ${total_cost['total_cost']:.4f} total"
                )
                print(
                    f"  Average cost per file: ${total_cost['total_cost']/len(results):.4f}"
                )

                # Different summary stats based on use case
                if use_case == UseCase.RAG:
                    total_pairs = sum(len(r["analysis"]["qa_pairs"]) for r in results)
                    print(f"  Total Q&A pairs: {total_pairs}")
                    print(
                        f"  Average cost per Q&A pair: ${total_cost['total_cost']/total_pairs:.4f}"
                    )
                elif use_case == UseCase.SUMMARIZATION:
                    print(
                        f"  Generated {len(results)} comprehensive transcript summaries"
                    )
                elif use_case == UseCase.QA:
                    print(f"  Generated {len(results)} Q&A pairs")
            else:
                print("No files were processed successfully")
                return 1

    except Exception as e:
        print(f"Error during processing: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
