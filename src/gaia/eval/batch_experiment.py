# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from gaia.chat.prompts import Prompts
from gaia.eval.claude import ClaudeClient
from gaia.eval.config import DEFAULT_CLAUDE_MODEL
from gaia.llm.lemonade_client import LemonadeClient
from gaia.logger import get_logger

# Import PDF reader
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

# Experiment configuration constants
CREATIVE_TEMPERATURE_MAX = 0.7
CREATIVE_TEMPERATURE_INCREMENT = 0.3


def should_use_chat_template(task_type: str) -> bool:
    """
    Determine if chat template formatting should be used for a given task.

    Args:
        task_type: Type of task ('chat', 'qa', 'summarization', etc.)

    Returns:
        True if chat template should be used, False otherwise
    """
    # Only use chat templates for actual conversation/QA tasks
    # NOT for completion tasks like summarization
    return task_type in ["chat", "qa"]


def format_prompt_with_template(
    model: str,
    system_prompt: str,
    user_content: str,
    document_content: str = "",
    use_chat_template: bool = False,
) -> str:
    """
    Format prompt using model-specific template from Prompts class, or simple format.

    Args:
        model: Model name/path
        system_prompt: The system instruction
        user_content: The user's query/content
        document_content: Optional document/transcript context to include
        use_chat_template: Whether to use chat template formatting (for QA/chat tasks)

    Returns:
        Formatted prompt
    """
    if use_chat_template:
        # Use ChatML formatting for chat/QA tasks
        if document_content:
            enhanced_system_prompt = (
                f"{system_prompt}\n\nContext Document:\n{document_content}"
            )
        else:
            enhanced_system_prompt = system_prompt

        # Convert to chat history format expected by Prompts.format_chat_history
        chat_history = [f"user: {user_content}"]
        return Prompts.format_chat_history(
            model=model, chat_history=chat_history, system_prompt=enhanced_system_prompt
        )
    else:
        # Simple format for completion tasks (like summarization)
        if document_content:
            return f"{system_prompt}\n\nDocument Content:\n{document_content}\n\n{user_content}"
        else:
            return f"{system_prompt}\n\n{user_content}"


def extract_thinking_from_response(response_text: str) -> Dict[str, Any]:
    """
    Extract thinking content from <think>...</think> tags if present.

    Args:
        response_text: The raw model response

    Returns:
        Dict with 'response' (final answer) and 'thinking' (reasoning process, or None)
    """
    if not response_text:
        return {"response": "", "thinking": None}

    # Check for thinking tags
    think_pattern = r"<think>(.*?)</think>"
    match = re.search(think_pattern, response_text, flags=re.DOTALL)

    if match:
        thinking = match.group(1).strip()
        # Extract everything after the </think> tag as the final response
        final_response = re.sub(
            think_pattern, "", response_text, flags=re.DOTALL
        ).strip()
        return {"response": final_response, "thinking": thinking}

    # No thinking tags found, return full response
    return {"response": response_text.strip(), "thinking": None}


@dataclass
class ExperimentConfig:
    """Configuration for a single experiment."""

    name: str
    llm_type: str  # "claude" or "lemonade"
    model: str
    system_prompt: str
    experiment_type: str = "qa"  # "qa" or "summarization"
    max_tokens: int = 512
    temperature: float = 0.7
    parameters: Dict[str, Any] = None

    def __post_init__(self):
        if self.parameters is None:
            self.parameters = {}
        if self.experiment_type not in ["qa", "summarization"]:
            raise ValueError(
                f"experiment_type must be 'qa' or 'summarization', got: {self.experiment_type}"
            )


class BatchExperimentRunner:
    """Run batch experiments with different LLM configurations on transcript data.

    Summarization experiments make independent LLM calls for each component
    (executive summary, detailed summary, action items, etc.) to produce
    natural, focused outputs without complex JSON formatting.
    """

    def __init__(self, config_file: str):
        self.log = get_logger(__name__)
        self.config_file = config_file
        self.experiments = []
        self.load_config()

    def _extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extract text from PDF file using local PDF library."""
        if PdfReader is None:
            raise ImportError(
                "PDF reading library not found. Please install pypdf:\n"
                "  uv pip install pypdf"
            )

        try:
            reader = PdfReader(pdf_path)
            total_pages = len(reader.pages)
            self.log.info(
                f"📄 Extracting text from {total_pages} pages of {pdf_path}..."
            )

            text = ""
            for i, page in enumerate(reader.pages, 1):
                # Show progress for large PDFs
                if i % 10 == 0 or i == total_pages:
                    self.log.debug(f"  Processing page {i}/{total_pages}...")
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"

            extracted_text = text.strip()
            self.log.info(f"📝 Extracted {len(extracted_text):,} characters from PDF")
            return extracted_text

        except Exception as e:
            self.log.error(f"Error reading PDF {pdf_path}: {e}")
            raise

    def load_config(self):
        """Load experiment configuration from JSON file."""
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                config_data = json.load(f)

            # Validate config structure
            if "experiments" not in config_data:
                raise ValueError("Configuration file must contain 'experiments' array")

            # Parse experiments
            for exp_data in config_data["experiments"]:
                experiment = ExperimentConfig(
                    name=exp_data["name"],
                    llm_type=exp_data["llm_type"],
                    model=exp_data["model"],
                    system_prompt=exp_data["system_prompt"],
                    experiment_type=exp_data.get("experiment_type", "qa"),
                    max_tokens=exp_data.get("max_tokens", 512),
                    temperature=exp_data.get("temperature", 0.7),
                    parameters=exp_data.get("parameters", {}),
                )
                self.experiments.append(experiment)

            self.log.info(f"Loaded {len(self.experiments)} experiments from config")

        except Exception as e:
            self.log.error(f"Error loading config file: {e}")
            raise

    def create_llm_client(self, experiment: ExperimentConfig):
        """Create appropriate LLM client based on experiment config."""
        if experiment.llm_type.lower() == "claude":
            return ClaudeClient(
                model=experiment.model, max_tokens=experiment.max_tokens
            )
        elif experiment.llm_type.lower() == "lemonade":
            # Filter out non-LLM client parameters before passing to client constructor
            # Parameters like 'stop', 'combined_prompt' are for completions API, not client init
            llm_params = {
                k: v
                for k, v in experiment.parameters.items()
                if k not in ["combined_prompt", "stop"]
            }
            return LemonadeClient(model=experiment.model, verbose=False, **llm_params)
        else:
            raise ValueError(f"Unsupported LLM type: {experiment.llm_type}")

    def process_question_claude(
        self,
        client: ClaudeClient,
        question: str,
        system_prompt: str,
        document_content: str = "",
    ) -> Dict:
        """Process a question using Claude client."""
        try:
            if document_content:
                # Include document content in the prompt
                prompt = f"{system_prompt}\n\nDocument Content:\n{document_content}\n\nQuestion: {question}\n\nAnswer:"
            else:
                prompt = f"{system_prompt}\n\nQuestion: {question}\n\nAnswer:"
            response_data = client.get_completion_with_usage(prompt)

            # Extract response text
            response = response_data["content"]
            if isinstance(response, list):
                response_text = (
                    response[0].text
                    if hasattr(response[0], "text")
                    else str(response[0])
                )
            else:
                response_text = (
                    response.text if hasattr(response, "text") else str(response)
                )

            return {
                "response": response_text.strip(),
                "usage": response_data["usage"],
                "cost": response_data["cost"],
                "error": None,
            }
        except Exception as e:
            self.log.error(f"Error processing question with Claude: {e}")
            return {
                "response": f"ERROR: {str(e)}",
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "cost": {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
                "error": str(e),
            }

    def process_question_lemonade(
        self,
        client: LemonadeClient,
        question: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        document_content: str = "",
    ) -> Dict:
        """Process a question using Lemonade client."""
        try:
            # Format prompt for QA (uses ChatML for supported models)
            # Document content is passed separately and added to system prompt
            formatted_prompt = format_prompt_with_template(
                model=client.model,
                system_prompt=system_prompt,
                user_content=question,
                document_content=document_content,
                use_chat_template=True,  # Use chat template for QA tasks
            )

            # Use completions method with the client's loaded model
            response_data = client.completions(
                model=client.model,  # Use model from experiment config
                prompt=formatted_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False,
            )

            # Extract text from the response
            response_text = ""
            if "choices" in response_data and response_data["choices"]:
                response_text = response_data["choices"][0].get("text", "")

            # Extract thinking tokens if present
            extracted = extract_thinking_from_response(response_text)

            # Get token statistics from Lemonade
            try:
                stats = client.get_stats()
                input_tokens = stats.get("input_tokens", 0) if stats else 0
                output_tokens = stats.get("output_tokens", 0) if stats else 0
                total_tokens = input_tokens + output_tokens
            except Exception as e:
                self.log.warning(f"Failed to get stats from Lemonade: {e}")
                input_tokens = output_tokens = total_tokens = 0

            result = {
                "response": extracted["response"],
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                },
                "cost": {
                    "input_cost": 0.0,
                    "output_cost": 0.0,
                    "total_cost": 0.0,
                },  # Local inference has no cost
                "error": None,
            }

            # Add thinking tokens if present
            if extracted["thinking"]:
                result["thinking"] = extracted["thinking"]

            return result

        except Exception as e:
            self.log.error(f"Error processing question with Lemonade: {e}")
            return {
                "response": f"ERROR: {str(e)}",
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "cost": {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
                "error": str(e),
            }

    def _get_summary_prompts(self) -> Dict[str, str]:
        """Generate individual user prompts for each summary component.

        Note: System prompt is passed separately when formatting with chat template.
        These are just the user-facing questions/tasks.
        """
        return {
            "executive_summary": "Provide a brief executive summary (2-3 sentences) of the key outcomes and decisions from this transcript.",
            "detailed_summary": "Provide a detailed summary of the transcript, covering all major topics, discussions, and outcomes in paragraph form.",
            "action_items": "List the specific action items that were assigned during this meeting. Include who is responsible for each item when mentioned. Provide as a simple list.",
            "key_decisions": "List the key decisions that were made during this meeting. Focus on concrete decisions and outcomes. Provide as a simple list.",
            "participants": "List the participants mentioned in this transcript. Include their roles or titles when available. Provide as a simple list.",
            "topics_discussed": "List the main topics and subjects that were discussed in this meeting. Provide as a simple list.",
        }

    def process_summarization_claude(
        self,
        client: ClaudeClient,
        transcript: str,
        system_prompt: str,
        combined_prompt: bool = False,
    ) -> Dict:
        """Process summarization by making independent or combined calls for each component."""
        try:
            summary_prompts = self._get_summary_prompts()
            results = {}
            total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}
            errors = []

            if combined_prompt:
                # Make a single call with all components
                self.log.info(
                    f"Summarizing transcript with 1 combined model call for: {', '.join(summary_prompts.keys())}"
                )

                # Build combined prompt
                combined_request = f"{system_prompt}\n\nPlease provide the following summaries for the transcript:\n\n"
                for component, user_prompt in summary_prompts.items():
                    combined_request += f"**{component.upper()}**:\n{user_prompt}\n\n"
                combined_request += f"\nTranscript:\n{transcript}\n\nPlease structure your response with clear headers for each section."

                response_data = client.get_completion_with_usage(combined_request)

                # Extract response text
                response = response_data["content"]
                if isinstance(response, list):
                    response_text = (
                        response[0].text
                        if hasattr(response[0], "text")
                        else str(response[0])
                    )
                else:
                    response_text = (
                        response.text if hasattr(response, "text") else str(response)
                    )

                # Parse response into components
                for component in summary_prompts.keys():
                    # Try to extract each component from the combined response
                    component_upper = component.upper()
                    start_markers = [
                        f"**{component_upper}**:",
                        f"{component_upper}:",
                        f"# {component_upper}",
                        f"## {component_upper}",
                    ]

                    section_text = ""
                    for marker in start_markers:
                        if marker in response_text:
                            start_idx = response_text.find(marker) + len(marker)
                            # Find the next section or end
                            end_idx = len(response_text)
                            for other_component in summary_prompts.keys():
                                if other_component == component:
                                    continue
                                other_upper = other_component.upper()
                                for other_marker in [
                                    f"**{other_upper}**:",
                                    f"{other_upper}:",
                                    f"# {other_upper}",
                                    f"## {other_upper}",
                                ]:
                                    idx = response_text.find(other_marker, start_idx)
                                    if idx != -1 and idx < end_idx:
                                        end_idx = idx
                            section_text = response_text[start_idx:end_idx].strip()
                            break

                    results[component] = (
                        section_text if section_text else response_text.strip()
                    )

                # Use combined usage and cost
                if response_data["usage"]:
                    total_usage = response_data["usage"]
                if response_data["cost"]:
                    total_cost = response_data["cost"]

            else:
                # Original behavior: independent calls
                self.log.info(
                    f"Summarizing transcript with {len(summary_prompts)} independent model calls: {', '.join(summary_prompts.keys())}"
                )

                for component, user_prompt in summary_prompts.items():
                    try:
                        # Create full prompt with system prompt, user prompt, and transcript
                        full_prompt = f"{system_prompt}\n\n{user_prompt}\n\nTranscript:\n{transcript}\n\nResponse:"

                        response_data = client.get_completion_with_usage(full_prompt)

                        # Extract response text
                        response = response_data["content"]
                        if isinstance(response, list):
                            response_text = (
                                response[0].text
                                if hasattr(response[0], "text")
                                else str(response[0])
                            )
                        else:
                            response_text = (
                                response.text
                                if hasattr(response, "text")
                                else str(response)
                            )

                        results[component] = response_text.strip()

                        # Accumulate usage and cost
                        if response_data["usage"]:
                            for key in total_usage:
                                total_usage[key] += response_data["usage"].get(key, 0)
                        if response_data["cost"]:
                            for key in total_cost:
                                total_cost[key] += response_data["cost"].get(key, 0.0)

                        # Small delay between component calls to avoid rate limiting
                        time.sleep(0.5)

                    except Exception as e:
                        self.log.error(f"Error processing {component} with Claude: {e}")
                        results[component] = f"ERROR: {str(e)}"
                        errors.append(f"{component}: {str(e)}")

            return {
                "response": results,
                "usage": total_usage,
                "cost": total_cost,
                "error": "; ".join(errors) if errors else None,
            }
        except Exception as e:
            self.log.error(f"Error in independent summarization with Claude: {e}")
            return {
                "response": f"ERROR: {str(e)}",
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "cost": {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
                "error": str(e),
            }

    def process_summarization_lemonade(
        self,
        client: LemonadeClient,
        transcript: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        combined_prompt: bool = False,
        extra_params: Dict[str, Any] = None,
    ) -> Dict:
        """Process summarization by making independent or combined calls for each component."""
        try:
            summary_prompts = self._get_summary_prompts()
            results = {}
            total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}
            errors = []

            # Prepare extra parameters (like stop sequences)
            if extra_params is None:
                extra_params = {}

            if combined_prompt:
                # Make a single call with all components
                self.log.info(
                    f"Summarizing transcript with 1 combined model call for: {', '.join(summary_prompts.keys())}"
                )

                # Build user request for all components
                user_request = (
                    "Please provide the following summaries for the transcript:\n\n"
                )
                for component, user_prompt in summary_prompts.items():
                    user_request += f"**{component.upper()}**:\n{user_prompt}\n\n"
                user_request += "\nPlease structure your response with clear headers for each section."

                # Format prompt for summarization using chat template
                formatted_prompt = format_prompt_with_template(
                    model=client.model,
                    system_prompt=system_prompt,
                    user_content=user_request,
                    document_content=transcript,  # Pass transcript as context
                    use_chat_template=True,  # Use chat template for summarization
                )

                response_data = client.completions(
                    model=client.model,
                    prompt=formatted_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False,
                    **extra_params,  # Pass stop sequences and other params
                )

                # Extract text from the response
                response_text = ""
                if "choices" in response_data and response_data["choices"]:
                    response_text = response_data["choices"][0].get("text", "")

                # Extract thinking tokens if present
                extracted = extract_thinking_from_response(response_text)
                response_text = extracted["response"]
                thinking_content = extracted["thinking"]

                # Get token statistics from Lemonade
                try:
                    stats = client.get_stats()
                    input_tokens = stats.get("input_tokens", 0) if stats else 0
                    output_tokens = stats.get("output_tokens", 0) if stats else 0
                    total_tokens = input_tokens + output_tokens
                    total_usage = {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "total_tokens": total_tokens,
                    }
                except Exception as e:
                    self.log.warning(f"Failed to get stats from Lemonade: {e}")
                    total_usage = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    }

                # Parse response into components
                for component in summary_prompts.keys():
                    # Try to extract each component from the combined response
                    component_upper = component.upper()
                    start_markers = [
                        f"**{component_upper}**:",
                        f"{component_upper}:",
                        f"# {component_upper}",
                        f"## {component_upper}",
                    ]

                    section_text = ""
                    for marker in start_markers:
                        if marker in response_text:
                            start_idx = response_text.find(marker) + len(marker)
                            # Find the next section or end
                            end_idx = len(response_text)
                            for other_component in summary_prompts.keys():
                                if other_component == component:
                                    continue
                                other_upper = other_component.upper()
                                for other_marker in [
                                    f"**{other_upper}**:",
                                    f"{other_upper}:",
                                    f"# {other_upper}",
                                    f"## {other_upper}",
                                ]:
                                    idx = response_text.find(other_marker, start_idx)
                                    if idx != -1 and idx < end_idx:
                                        end_idx = idx
                            section_text = response_text[start_idx:end_idx].strip()
                            break

                    results[component] = (
                        section_text if section_text else response_text.strip()
                    )

                # Total usage already calculated above, cost is always 0 for local
                total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}

                # Store thinking content if present (for combined mode)
                if thinking_content:
                    results["_thinking"] = thinking_content

            else:
                # Original behavior: independent calls
                self.log.info(
                    f"Summarizing transcript with {len(summary_prompts)} independent model calls: {', '.join(summary_prompts.keys())}"
                )

                for component, user_prompt in summary_prompts.items():
                    try:
                        # Format using chat template with separate system prompt and user prompt
                        formatted_prompt = format_prompt_with_template(
                            model=client.model,
                            system_prompt=system_prompt,
                            user_content=user_prompt,
                            document_content=transcript,
                            use_chat_template=True,  # Use chat template for summarization
                        )

                        response_data = client.completions(
                            model=client.model,
                            prompt=formatted_prompt,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            stream=False,
                            **extra_params,  # Pass stop sequences and other params
                        )

                        # Extract text from the response
                        response_text = ""
                        if "choices" in response_data and response_data["choices"]:
                            response_text = response_data["choices"][0].get("text", "")

                        # Extract thinking tokens if present
                        extracted = extract_thinking_from_response(response_text)

                        results[component] = extracted["response"]

                        # Get token statistics from Lemonade
                        try:
                            stats = client.get_stats()
                            if stats:
                                total_usage["input_tokens"] += stats.get(
                                    "input_tokens", 0
                                )
                                total_usage["output_tokens"] += stats.get(
                                    "output_tokens", 0
                                )
                                total_usage["total_tokens"] += stats.get(
                                    "input_tokens", 0
                                ) + stats.get("output_tokens", 0)
                        except Exception as e:
                            self.log.warning(f"Failed to get stats from Lemonade: {e}")

                        # Small delay between component calls to avoid rate limiting
                        time.sleep(0.5)

                    except Exception as e:
                        self.log.error(
                            f"Error processing {component} with Lemonade: {e}"
                        )
                        results[component] = f"ERROR: {str(e)}"
                        errors.append(f"{component}: {str(e)}")

            result_dict = {
                "response": results,
                "usage": total_usage,
                "cost": total_cost,
                "error": "; ".join(errors) if errors else None,
            }

            # Add thinking if present (stored with key "_thinking" in results dict)
            if "_thinking" in results:
                result_dict["thinking"] = results.pop("_thinking")

            return result_dict

        except Exception as e:
            self.log.error(f"Error in independent summarization with Lemonade: {e}")
            return {
                "response": f"ERROR: {str(e)}",
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "cost": {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
                "error": str(e),
            }

    def check_experiment_exists(
        self, experiment: ExperimentConfig, output_dir: str
    ) -> bool:
        """Check if an experiment file already exists in the output directory.

        Args:
            experiment: The experiment configuration
            output_dir: The output directory path

        Returns:
            True if experiment file exists, False otherwise
        """
        output_base_path = Path(output_dir)

        # Generate the same safe filename that would be used for the output
        safe_name = "".join(
            c if (c.isalnum() or c in (" ", "-", "_")) else "_" if c == "." else ""
            for c in experiment.name
        ).rstrip()
        safe_name = safe_name.replace(" ", "_")

        # Check for consolidated file
        consolidated_filename = f"{safe_name}.experiment.json"
        consolidated_path = output_base_path / consolidated_filename

        if consolidated_path.exists():
            self.log.info(f"Experiment file already exists: {consolidated_path}")
            return True

        return False

    def load_data_from_source(
        self, input_path: str, experiment_type: str = "qa", queries_source: str = None
    ) -> List[Dict]:
        """Load data from various input sources: groundtruth files, transcript files, or directories."""
        input_path = Path(input_path)

        self.log.info(f"Loading data from source: {input_path}, type={experiment_type}")
        self.log.info(
            f"Input path exists: {input_path.exists()}, is_file: {input_path.is_file()}, suffix: {input_path.suffix}"
        )

        try:
            if input_path.is_file():
                if input_path.suffix == ".json":
                    # Handle groundtruth JSON files
                    return self._load_from_groundtruth_file(
                        str(input_path), experiment_type
                    )
                else:
                    # Handle individual transcript files
                    return self._load_from_transcript_file(
                        str(input_path), experiment_type, queries_source
                    )
            elif input_path.is_dir():
                # Handle directories of transcript files
                return self._load_from_transcript_directory(
                    str(input_path), experiment_type, queries_source
                )
            else:
                raise FileNotFoundError(f"Input path not found: {input_path}")

        except Exception as e:
            self.log.error(f"Error loading data from source: {e}")
            raise

    def _load_queries_from_groundtruth(self, groundtruth_file: str) -> List[str]:
        """Extract queries from a groundtruth file for use with raw transcripts."""
        with open(groundtruth_file, "r", encoding="utf-8") as f:
            groundtruth_data = json.load(f)

        analysis = groundtruth_data.get("analysis", {})
        qa_pairs = analysis.get("qa_pairs", [])

        if not qa_pairs:
            raise ValueError(
                f"No QA pairs found in groundtruth file: {groundtruth_file}"
            )

        queries = []
        for qa_pair in qa_pairs:
            query = qa_pair.get("query", qa_pair.get("question", ""))
            if query:
                queries.append(query)

        if not queries:
            raise ValueError(
                f"No valid queries found in groundtruth file: {groundtruth_file}"
            )

        return queries

    def _get_default_queries(self) -> List[str]:
        """Return default questions for QA experiments on raw transcripts."""
        return [
            "What were the main topics discussed in this meeting?",
            "What action items were assigned and to whom?",
            "What decisions were made during this meeting?",
            "Who participated in this meeting and what were their roles?",
            "What are the next steps or follow-up items?",
        ]

    def _load_from_groundtruth_file(
        self, groundtruth_file: str, experiment_type: str
    ) -> List[Dict]:
        """Load data from a groundtruth JSON file (individual or consolidated)."""
        self.log.info(
            f"Loading groundtruth file: {groundtruth_file} for experiment type: {experiment_type}"
        )

        with open(groundtruth_file, "r", encoding="utf-8") as f:
            groundtruth_data = json.load(f)

        analysis = groundtruth_data.get("analysis", {})
        metadata = groundtruth_data.get("metadata", {})

        # Check if this is a consolidated groundtruth file
        is_consolidated = "consolidated_from" in metadata or "source_files" in metadata

        self.log.info(f"Metadata keys: {list(metadata.keys())}")
        self.log.info(f"Is consolidated: {is_consolidated}")

        if is_consolidated:
            return self._load_from_consolidated_groundtruth(
                groundtruth_data, experiment_type
            )
        else:
            return self._load_from_individual_groundtruth(
                groundtruth_data, experiment_type
            )

    def _load_from_individual_groundtruth(
        self, groundtruth_data: Dict, experiment_type: str
    ) -> List[Dict]:
        """Load data from an individual groundtruth file."""
        analysis = groundtruth_data.get("analysis", {})
        metadata = groundtruth_data.get("metadata", {})

        if experiment_type == "qa":
            # Extract QA pairs from groundtruth
            qa_pairs = analysis.get("qa_pairs", [])

            if not qa_pairs:
                raise ValueError(
                    "No QA pairs found in groundtruth file for QA experiment"
                )

            data = []
            for qa_pair in qa_pairs:
                data.append(
                    {
                        "type": "qa",
                        "query": qa_pair.get("query", qa_pair.get("question", "")),
                        "ground_truth": qa_pair.get(
                            "response", qa_pair.get("answer", "")
                        ),
                    }
                )

            return data

        elif experiment_type == "summarization":
            # Extract transcript content and summaries from groundtruth
            summaries = analysis.get("summaries", {})

            if not summaries:
                raise ValueError(
                    "No summaries found in groundtruth file for summarization experiment"
                )

            # Get the source transcript content
            source_file = metadata.get("source_file", "")

            # Read transcript content
            if not source_file or not Path(source_file).exists():
                raise ValueError(f"Source transcript file not found: {source_file}")

            with open(source_file, "r", encoding="utf-8") as f:
                transcript_content = f.read().strip()

            if not transcript_content:
                raise ValueError(f"Empty transcript file: {source_file}")

            data = [
                {
                    "type": "summarization",
                    "transcript": transcript_content,
                    "groundtruth_summaries": summaries,
                    "source_file": source_file,
                }
            ]

            return data

        else:
            raise ValueError(f"Unsupported experiment type: {experiment_type}")

    def _load_from_consolidated_groundtruth(
        self, groundtruth_data: Dict, experiment_type: str
    ) -> List[Dict]:
        """Load data from a consolidated groundtruth file."""
        analysis = groundtruth_data.get("analysis", {})
        metadata = groundtruth_data.get("metadata", {})

        self.log.info(
            f"Loading consolidated groundtruth for experiment type: {experiment_type}"
        )
        self.log.info(f"Metadata keys: {list(metadata.keys())}")
        self.log.info(f"Analysis keys: {list(analysis.keys())}")

        if experiment_type == "qa":
            # For consolidated QA files, extract QA pairs from all items
            data = []

            # Get source file information from metadata for document loading
            source_files_map = {}
            if "source_files" in metadata:
                for source_info in metadata["source_files"]:
                    doc_id = source_info.get("transcript_id", "")
                    source_file = source_info.get("source_file", "")
                    if doc_id and source_file:
                        source_files_map[doc_id] = source_file

            # Cache for document content to avoid loading same document multiple times
            document_content_cache = {}

            # Check if analysis contains direct qa_pairs (can be dict or list)
            if "qa_pairs" in analysis:
                qa_pairs = analysis["qa_pairs"]

                # Handle dict format (consolidated files key by document ID)
                if isinstance(qa_pairs, dict):
                    for doc_id, doc_qa_pairs in qa_pairs.items():
                        # Try to load the source document content
                        document_content = ""
                        source_file = source_files_map.get(doc_id, "")

                        # Check cache first
                        if source_file in document_content_cache:
                            document_content = document_content_cache[source_file]
                        elif source_file:
                            source_path = Path(source_file)
                            if source_path.exists():
                                try:
                                    # Handle PDF files
                                    if source_path.suffix.lower() == ".pdf":
                                        self.log.info(
                                            f"PDF file detected: {source_path}"
                                        )
                                        # Use local PDF extraction
                                        document_content = self._extract_text_from_pdf(
                                            str(source_path)
                                        )
                                    # Handle text files
                                    else:
                                        with open(
                                            source_path, "r", encoding="utf-8"
                                        ) as f:
                                            document_content = f.read()

                                    # Cache the content
                                    document_content_cache[source_file] = (
                                        document_content
                                    )

                                except Exception as e:
                                    self.log.warning(
                                        f"Failed to load document {source_path}: {e}"
                                    )
                                    document_content = ""
                            else:
                                self.log.warning(
                                    f"Source document not found: {source_path}"
                                )

                        for qa_pair in doc_qa_pairs:
                            data.append(
                                {
                                    "type": "qa",
                                    "query": qa_pair.get(
                                        "query", qa_pair.get("question", "")
                                    ),
                                    "ground_truth": qa_pair.get(
                                        "response", qa_pair.get("answer", "")
                                    ),
                                    "source_item": doc_id,
                                    "document_content": document_content,
                                    "source_file": source_file,
                                }
                            )
                # Handle list format (non-consolidated files)
                elif isinstance(qa_pairs, list):
                    for qa_pair in qa_pairs:
                        data.append(
                            {
                                "type": "qa",
                                "query": qa_pair.get(
                                    "query", qa_pair.get("question", "")
                                ),
                                "ground_truth": qa_pair.get(
                                    "response", qa_pair.get("answer", "")
                                ),
                            }
                        )

            # Also check for nested structure (qa_pairs within individual summaries)
            summaries = analysis.get("summaries", {})
            for item_id, item_data in summaries.items():
                if "qa_pairs" in item_data:
                    for qa_pair in item_data["qa_pairs"]:
                        data.append(
                            {
                                "type": "qa",
                                "query": qa_pair.get(
                                    "query", qa_pair.get("question", "")
                                ),
                                "ground_truth": qa_pair.get(
                                    "response", qa_pair.get("answer", "")
                                ),
                                "source_item": item_id,
                            }
                        )

            if not data:
                raise ValueError(
                    "No QA pairs found in consolidated groundtruth file for QA experiment"
                )

            return data

        elif experiment_type == "summarization":
            # For consolidated summarization files, create separate items for each source
            summaries = analysis.get("summaries", {})

            self.log.info(f"Found {len(summaries)} summaries in consolidated file")

            if not summaries:
                raise ValueError(
                    "No summaries found in consolidated groundtruth file for summarization experiment"
                )

            data = []
            source_files_info = metadata.get("source_files", [])

            self.log.info(f"Found {len(source_files_info)} source files in metadata")

            # Create a mapping of transcript_id to source file info
            source_file_map = {}
            for source_info in source_files_info:
                transcript_id = source_info.get("transcript_id", "")
                source_file_map[transcript_id] = source_info
                self.log.info(
                    f"Mapped transcript_id '{transcript_id}' to source file '{source_info.get('source_file', '')}'"
                )

            self.log.info(
                f"Created source file map with {len(source_file_map)} entries"
            )

            for item_id, item_summaries in summaries.items():
                # Get source file information
                source_info = source_file_map.get(item_id, {})
                source_file = source_info.get("source_file", "")

                self.log.info(
                    f"Processing item {item_id}, source file: '{source_file}'"
                )

                if not source_file:
                    self.log.warning(
                        f"No source file found for item {item_id}, skipping"
                    )
                    continue

                # Normalize path separators for current platform
                source_file = source_file.replace("\\", "/")
                source_path = Path(source_file)

                self.log.info(
                    f"Normalized source path: '{source_path}', exists: {source_path.exists()}"
                )

                # Read transcript content
                if not source_path.exists():
                    self.log.warning(
                        f"Source transcript file not found: {source_path}, skipping {item_id}"
                    )
                    continue

                with open(source_path, "r", encoding="utf-8") as f:
                    transcript_content = f.read().strip()

                if not transcript_content:
                    self.log.warning(
                        f"Empty transcript file: {source_path}, skipping {item_id}"
                    )
                    continue

                data.append(
                    {
                        "type": "summarization",
                        "transcript": transcript_content,
                        "groundtruth_summaries": item_summaries,
                        "source_file": str(source_path),
                        "item_id": item_id,
                    }
                )

            if not data:
                raise ValueError(
                    "No valid data items found in consolidated groundtruth file for summarization experiment"
                )

            return data

        else:
            raise ValueError(f"Unsupported experiment type: {experiment_type}")

    def _load_from_transcript_file(
        self, transcript_file: str, experiment_type: str, queries_source: str = None
    ) -> List[Dict]:
        """Load data from a single transcript file."""
        with open(transcript_file, "r", encoding="utf-8") as f:
            transcript_content = f.read().strip()

        if not transcript_content:
            raise ValueError(f"Empty transcript file: {transcript_file}")

        if experiment_type == "qa":
            # Get queries from groundtruth source
            if not queries_source:
                queries = self._get_default_queries()
            else:
                queries = self._load_queries_from_groundtruth(queries_source)
                self.log.info(
                    f"Loaded {len(queries)} queries from groundtruth source: {queries_source}"
                )

            # For QA experiments on raw transcripts, we can't provide ground truth
            # The experiment will generate responses that can be manually evaluated
            return [
                {
                    "type": "qa_raw",
                    "transcript": transcript_content,
                    "source_file": transcript_file,
                    "queries": queries,
                }
            ]

        elif experiment_type == "summarization":
            return [
                {
                    "type": "summarization",
                    "transcript": transcript_content,
                    "source_file": transcript_file,
                }
            ]

        else:
            raise ValueError(f"Unsupported experiment type: {experiment_type}")

    def _load_from_transcript_directory(
        self, transcript_dir: str, experiment_type: str, queries_source: str = None
    ) -> List[Dict]:
        """Load data from a directory of transcript files."""
        transcript_dir = Path(transcript_dir)

        # Find all text files in directory (recursively)
        transcript_files = list(transcript_dir.rglob("*.txt"))
        if not transcript_files:
            raise ValueError(f"No .txt files found in directory: {transcript_dir}")

        data = []
        for transcript_file in transcript_files:
            file_data = self._load_from_transcript_file(
                str(transcript_file), experiment_type, queries_source
            )
            data.extend(file_data)

        return data

    def run_experiment(
        self,
        experiment: ExperimentConfig,
        data_items: List[Dict],
        output_dir: str,
        delay_seconds: float = 1.0,
    ) -> str:
        """Run a single experiment with the given data items."""
        # Start timing the experiment
        experiment_start_time = time.time()

        self.log.info(
            f"Running experiment: {experiment.name} (type: {experiment.experiment_type})"
        )

        # Create LLM client
        client = self.create_llm_client(experiment)

        # Set up output directories for incremental writing
        output_base_path = Path(output_dir)
        output_base_path.mkdir(parents=True, exist_ok=True)

        # Generate safe filename from experiment name
        safe_name = "".join(
            c if (c.isalnum() or c in (" ", "-", "_")) else "_" if c == "." else ""
            for c in experiment.name
        ).rstrip()
        safe_name = safe_name.replace(" ", "_")

        # Create intermediate results directory
        intermediate_dir = output_base_path / f"{safe_name}.intermediate"
        intermediate_dir.mkdir(parents=True, exist_ok=True)

        # Process each data item
        results = []
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}
        errors = []
        item_timings = []  # Track timing for each item

        for i, data_item in enumerate(data_items):
            item_start_time = time.time()
            data_type = data_item["type"]
            self.log.info(
                f"Processing item {i+1}/{len(data_items)} (type: {data_type})"
            )

            # Process based on experiment and data type
            if data_type == "qa":
                # Process Q&A pair with ground truth
                # Check if document content is available
                document_content = data_item.get("document_content", "")

                if experiment.llm_type.lower() == "claude":
                    if document_content:
                        # Include document context with the question
                        result = self.process_question_claude(
                            client,
                            data_item["query"],
                            experiment.system_prompt,
                            document_content,
                        )
                    else:
                        result = self.process_question_claude(
                            client, data_item["query"], experiment.system_prompt
                        )
                elif experiment.llm_type.lower() == "lemonade":
                    if document_content:
                        result = self.process_question_lemonade(
                            client,
                            data_item["query"],
                            experiment.system_prompt,
                            experiment.max_tokens,
                            experiment.temperature,
                            document_content,
                        )
                    else:
                        result = self.process_question_lemonade(
                            client,
                            data_item["query"],
                            experiment.system_prompt,
                            experiment.max_tokens,
                            experiment.temperature,
                        )

                # Create QA result entry
                result_entry = {
                    "query": data_item["query"],
                    "ground_truth": data_item["ground_truth"],
                    "response": result["response"],
                }

                # Add thinking tokens if present
                if "thinking" in result and result["thinking"]:
                    result_entry["thinking"] = result["thinking"]

            elif data_type == "qa_raw":
                # Process raw transcript with predefined questions
                qa_results = []
                total_result = {
                    "response": "",
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "cost": {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
                    "error": None,
                }

                for query in data_item["queries"]:
                    if experiment.llm_type.lower() == "claude":
                        # Create context-aware prompt with transcript
                        context_prompt = f"{experiment.system_prompt}\n\nTranscript:\n{data_item['transcript']}\n\nQuestion: {query}\n\nAnswer:"
                        # For Claude, we can use the context as system prompt
                        query_result = {
                            "response": "",
                            "usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                            },
                            "cost": {
                                "input_cost": 0.0,
                                "output_cost": 0.0,
                                "total_cost": 0.0,
                            },
                            "error": None,
                        }
                        try:
                            response_data = client.get_completion_with_usage(
                                context_prompt
                            )
                            response = response_data["content"]
                            if isinstance(response, list):
                                response_text = (
                                    response[0].text
                                    if hasattr(response[0], "text")
                                    else str(response[0])
                                )
                            else:
                                response_text = (
                                    response.text
                                    if hasattr(response, "text")
                                    else str(response)
                                )

                            query_result = {
                                "response": response_text.strip(),
                                "usage": response_data["usage"],
                                "cost": response_data["cost"],
                                "error": None,
                            }
                        except Exception as e:
                            self.log.error(f"Error processing QA with Claude: {e}")
                            query_result["response"] = f"ERROR: {str(e)}"
                            query_result["error"] = str(e)

                    elif experiment.llm_type.lower() == "lemonade":
                        # For Lemonade, use ChatML formatting
                        query_result = {
                            "response": "",
                            "usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                            },
                            "cost": {
                                "input_cost": 0.0,
                                "output_cost": 0.0,
                                "total_cost": 0.0,
                            },
                            "error": None,
                        }
                        try:
                            # Format with ChatML template for QA tasks, transcript as document context
                            formatted_prompt = format_prompt_with_template(
                                model=client.model,
                                system_prompt=experiment.system_prompt,
                                user_content=query,
                                document_content=data_item["transcript"],
                                use_chat_template=True,  # Use chat template for QA tasks
                            )

                            response_data = client.completions(
                                model=client.model,  # Use model from experiment config
                                prompt=formatted_prompt,
                                max_tokens=experiment.max_tokens,
                                temperature=experiment.temperature,
                                stream=False,
                            )

                            # Extract text from the response
                            response_text = ""
                            if "choices" in response_data and response_data["choices"]:
                                response_text = response_data["choices"][0].get(
                                    "text", ""
                                )

                            # Extract thinking tokens if present
                            extracted = extract_thinking_from_response(response_text)

                            query_result["response"] = extracted["response"]
                            # Store thinking for this query if present
                            if extracted["thinking"]:
                                query_result["thinking"] = extracted["thinking"]

                        except Exception as e:
                            self.log.error(f"Error processing QA with Lemonade: {e}")
                            query_result["response"] = f"ERROR: {str(e)}"
                            query_result["error"] = str(e)

                    qa_result_item = {
                        "query": query,
                        "response": query_result["response"],
                    }
                    # Add thinking if present
                    if "thinking" in query_result and query_result["thinking"]:
                        qa_result_item["thinking"] = query_result["thinking"]
                    qa_results.append(qa_result_item)

                    # Accumulate usage/cost
                    if query_result["usage"]:
                        for key in total_result["usage"]:
                            total_result["usage"][key] += query_result["usage"].get(
                                key, 0
                            )
                    if query_result["cost"]:
                        for key in total_result["cost"]:
                            total_result["cost"][key] += query_result["cost"].get(
                                key, 0.0
                            )
                    if query_result["error"]:
                        if total_result["error"]:
                            total_result["error"] += f"; {query_result['error']}"
                        else:
                            total_result["error"] = query_result["error"]

                result = total_result
                result_entry = {
                    "transcript": (
                        data_item["transcript"][:500] + "..."
                        if len(data_item["transcript"]) > 500
                        else data_item["transcript"]
                    ),
                    "source_file": data_item.get("source_file", ""),
                    "qa_results": qa_results,
                }

            elif data_type == "summarization":
                # Process summarization task using independent calls for each component
                if experiment.llm_type.lower() == "claude":
                    combined = experiment.parameters.get("combined_prompt", False)
                    result = self.process_summarization_claude(
                        client,
                        data_item["transcript"],
                        experiment.system_prompt,
                        combined,
                    )
                elif experiment.llm_type.lower() == "lemonade":
                    combined = experiment.parameters.get("combined_prompt", False)
                    # Extract parameters to pass (excluding combined_prompt)
                    extra_params = {
                        k: v
                        for k, v in experiment.parameters.items()
                        if k != "combined_prompt"
                    }
                    result = self.process_summarization_lemonade(
                        client,
                        data_item["transcript"],
                        experiment.system_prompt,
                        experiment.max_tokens,
                        experiment.temperature,
                        combined,
                        extra_params,
                    )

                # Use the structured response directly from independent calls
                generated_summaries = result["response"]

                # Create summarization result entry
                result_entry = {
                    "transcript": (
                        data_item["transcript"][:500] + "..."
                        if len(data_item["transcript"]) > 500
                        else data_item["transcript"]
                    ),
                    "generated_summaries": generated_summaries,
                    "source_file": data_item.get("source_file", ""),
                }

                # Add ground truth summaries if available (from groundtruth files)
                if "groundtruth_summaries" in data_item:
                    result_entry["groundtruth_summaries"] = data_item[
                        "groundtruth_summaries"
                    ]

                # Add thinking tokens if present (for combined mode)
                if "thinking" in result and result["thinking"]:
                    result_entry["thinking"] = result["thinking"]

            else:
                self.log.error(f"Unsupported data type: {data_type}")
                continue

            # Accumulate usage and cost data
            if result["usage"]:
                for key in total_usage:
                    total_usage[key] += result["usage"].get(key, 0)
            if result["cost"]:
                for key in total_cost:
                    total_cost[key] += result["cost"].get(key, 0.0)

            if result["error"]:
                errors.append(f"Item {i+1}: {result['error']}")

            # Add processing time to result entry
            item_time = time.time() - item_start_time
            result_entry["processing_time_seconds"] = round(item_time, 3)
            item_timings.append(item_time)

            results.append(result_entry)

            # Write intermediate result immediately for crash recovery
            try:
                intermediate_file = (
                    intermediate_dir / f"item_{i+1:04d}_{data_type}.json"
                )
                intermediate_data = {
                    "item_index": i,
                    "data_type": data_type,
                    "data_item": data_item,
                    "result": result_entry,
                    "usage": result.get("usage", {}),
                    "cost": result.get("cost", {}),
                    "error": result.get("error"),
                    "timestamp": datetime.now().isoformat(),
                    "processing_time_seconds": round(item_time, 3),
                }

                with open(intermediate_file, "w", encoding="utf-8") as f:
                    json.dump(intermediate_data, f, indent=2)

                # Update progress file
                progress_file = intermediate_dir / "progress.json"
                progress_data = {
                    "experiment_name": experiment.name,
                    "total_items": len(data_items),
                    "completed_items": i + 1,
                    "progress_percent": round((i + 1) / len(data_items) * 100, 1),
                    "total_usage": total_usage.copy(),
                    "total_cost": total_cost.copy(),
                    "errors_count": len(errors),
                    "last_updated": datetime.now().isoformat(),
                    "estimated_remaining_time": None,
                }

                # Calculate estimated remaining time
                if i > 0:
                    avg_time_per_item = sum(item_timings) / len(item_timings)
                    remaining_items = len(data_items) - (i + 1)
                    estimated_remaining = remaining_items * avg_time_per_item
                    progress_data["estimated_remaining_time"] = round(
                        estimated_remaining, 1
                    )

                with open(progress_file, "w", encoding="utf-8") as f:
                    json.dump(progress_data, f, indent=2)

                self.log.info(
                    f"Progress: {i+1}/{len(data_items)} items completed ({progress_data['progress_percent']}%)"
                )

            except Exception as e:
                self.log.warning(f"Failed to write intermediate result {i+1}: {e}")

            # Add delay between requests to avoid rate limiting
            if delay_seconds > 0 and i < len(data_items) - 1:
                time.sleep(delay_seconds)

        # Calculate total experiment time
        total_experiment_time = time.time() - experiment_start_time

        # Determine inference type (cloud vs local)
        inference_type = "cloud" if experiment.llm_type.lower() == "claude" else "local"

        # Create output data in format expected by eval tool
        output_data = {
            "metadata": {
                "experiment_name": experiment.name,
                "experiment_type": experiment.experiment_type,
                "llm_type": experiment.llm_type,
                "model": experiment.model,
                "inference_type": inference_type,  # Add inference type
                "system_prompt": experiment.system_prompt,
                "max_tokens": experiment.max_tokens,
                "temperature": experiment.temperature,
                "parameters": experiment.parameters,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "similarity_threshold": 0.7,  # Default threshold for eval
                "total_items": len(data_items),
                "total_usage": total_usage,
                "total_cost": total_cost,
                "errors": errors,
                "timing": {
                    "total_experiment_time_seconds": round(total_experiment_time, 3),
                    "per_item_times_seconds": [round(t, 3) for t in item_timings],
                    "average_per_item_seconds": (
                        round(np.mean(item_timings), 3) if item_timings else 0
                    ),
                    "max_per_item_seconds": (
                        round(max(item_timings), 3) if item_timings else 0
                    ),
                    "min_per_item_seconds": (
                        round(min(item_timings), 3) if item_timings else 0
                    ),
                },
            },
            "analysis": {},
        }

        # Set analysis data based on experiment type and data type
        if experiment.experiment_type == "qa":
            # Check if we have traditional QA results or raw transcript QA results
            if results and "qa_results" in results[0]:
                output_data["analysis"]["transcript_qa_results"] = results
            else:
                output_data["analysis"]["qa_results"] = results
        elif experiment.experiment_type == "summarization":
            output_data["analysis"]["summarization_results"] = results

        # Determine output structure based on data items
        # (output_base_path and safe_name already created earlier for incremental writing)

        # Check if we have multiple items with individual source files (hierarchical structure needed)
        has_individual_items = any(
            "source_file" in result for result in results if isinstance(result, dict)
        )
        has_item_ids = any(
            "item_id" in data_item
            for data_item in data_items
            if isinstance(data_item, dict)
        )

        if has_individual_items or has_item_ids:
            # Create hierarchical structure - save individual files and consolidated
            individual_files = self._save_individual_experiment_files(
                output_data, data_items, results, output_base_path, safe_name
            )

            # Create consolidated file at root
            consolidated_filename = f"{safe_name}.experiment.json"
            consolidated_path = output_base_path / consolidated_filename

            # Add consolidation metadata
            output_data["metadata"]["consolidated_from"] = len(individual_files)
            output_data["metadata"]["individual_files"] = individual_files

            with open(consolidated_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, indent=2)

            self.log.info(
                f"Consolidated experiment results saved to: {consolidated_path}"
            )
            self.log.info(f"Individual experiment files: {len(individual_files)}")

            # Clean up intermediate files after successful completion
            self._cleanup_intermediate_files(intermediate_dir)

            return str(consolidated_path)
        else:
            # Single file output (traditional behavior)
            result_filename = f"{safe_name}.experiment.json"
            result_path = output_base_path / result_filename

            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, indent=2)

            self.log.info(f"Experiment results saved to: {result_path}")

            # Clean up intermediate files after successful completion
            self._cleanup_intermediate_files(intermediate_dir)

            return str(result_path)

    def _save_individual_experiment_files(
        self,
        base_output_data: Dict,
        data_items: List[Dict],
        results: List[Dict],
        output_base_path: Path,
        safe_experiment_name: str,
    ) -> List[str]:
        """Save individual experiment files maintaining directory hierarchy."""
        individual_files = []

        for i, (data_item, result) in enumerate(zip(data_items, results)):
            # Determine output path based on source file or item_id
            if "item_id" in data_item:
                # From consolidated groundtruth - use item_id to determine path
                item_id = data_item["item_id"]
                # Create directory structure like emails/file_name or meetings/file_name
                if "/" in item_id:
                    relative_dir = item_id.split("/")[0] if "/" in item_id else ""
                    file_base = item_id.split("/")[-1] if "/" in item_id else item_id
                else:
                    # Guess directory from item_id pattern
                    if "email" in item_id.lower():
                        relative_dir = "emails"
                    elif "meeting" in item_id.lower():
                        relative_dir = "meetings"
                    else:
                        relative_dir = "misc"
                    file_base = item_id
            elif "source_file" in data_item:
                # From individual files - extract relative path from source file
                source_file = Path(data_item["source_file"])
                if "test_data" in source_file.parts:
                    # Extract relative path from test_data structure
                    test_data_index = source_file.parts.index("test_data")
                    relative_parts = source_file.parts[test_data_index + 1 :]
                    if len(relative_parts) > 1:
                        relative_dir = "/".join(relative_parts[:-1])
                        file_base = source_file.stem
                    else:
                        relative_dir = ""
                        file_base = source_file.stem
                else:
                    relative_dir = ""
                    file_base = source_file.stem
            else:
                # Fallback - no hierarchical structure
                relative_dir = ""
                file_base = f"item_{i+1}"

            # Create individual output data
            individual_output_data = {
                "metadata": base_output_data["metadata"].copy(),
                "analysis": {},
            }

            # Adjust metadata for individual file
            individual_output_data["metadata"]["total_items"] = 1
            individual_output_data["metadata"]["source_item"] = data_item.get(
                "item_id", ""
            )
            individual_output_data["metadata"]["source_file"] = data_item.get(
                "source_file", ""
            )

            # Add single result to analysis
            if base_output_data["metadata"]["experiment_type"] == "qa":
                if "qa_results" in result:
                    individual_output_data["analysis"]["transcript_qa_results"] = [
                        result
                    ]
                else:
                    individual_output_data["analysis"]["qa_results"] = [result]
            elif base_output_data["metadata"]["experiment_type"] == "summarization":
                individual_output_data["analysis"]["summarization_results"] = [result]

            # Create output directory and file
            if relative_dir:
                output_dir = output_base_path / relative_dir
                output_dir.mkdir(parents=True, exist_ok=True)
            else:
                output_dir = output_base_path

            individual_filename = f"{file_base}.{safe_experiment_name}.experiment.json"
            individual_path = output_dir / individual_filename

            with open(individual_path, "w", encoding="utf-8") as f:
                json.dump(individual_output_data, f, indent=2)

            # Store relative path for consolidation metadata
            if relative_dir:
                relative_path = f"{relative_dir}/{individual_filename}"
            else:
                relative_path = individual_filename

            individual_files.append(relative_path)

        return individual_files

    def _cleanup_intermediate_files(self, intermediate_dir: Path) -> None:
        """Clean up intermediate files after successful completion."""
        try:
            import shutil

            if intermediate_dir.exists():
                shutil.rmtree(intermediate_dir)
                self.log.info(f"Cleaned up intermediate files from: {intermediate_dir}")
        except Exception as e:
            self.log.warning(
                f"Failed to clean up intermediate directory {intermediate_dir}: {e}"
            )

    def run_all_experiments(
        self,
        input_path: str,
        output_dir: str,
        delay_seconds: float = 1.0,
        queries_source: str = None,
        skip_existing: bool = False,
    ) -> Tuple[List[str], int]:
        """Run all experiments defined in the config file.

        Returns:
            tuple: (result_files, skipped_count) where result_files is a list of output file paths
                   and skipped_count is the number of experiments that were skipped
        """
        # Start timing all experiments
        all_experiments_start_time = time.time()

        self.log.info(
            f"Starting batch experiments with {len(self.experiments)} configurations"
        )

        # Run each experiment
        result_files = []
        skipped_count = 0
        for i, experiment in enumerate(self.experiments):
            # Check if we should skip this experiment
            if skip_existing and self.check_experiment_exists(experiment, output_dir):
                self.log.info(
                    f"Skipping experiment {i+1}/{len(self.experiments)}: {experiment.name} (already exists)"
                )
                skipped_count += 1

                # Add the existing file to result_files for consolidated report
                output_base_path = Path(output_dir)
                safe_name = "".join(
                    (
                        c
                        if (c.isalnum() or c in (" ", "-", "_"))
                        else "_" if c == "." else ""
                    )
                    for c in experiment.name
                ).rstrip()
                safe_name = safe_name.replace(" ", "_")
                consolidated_filename = f"{safe_name}.experiment.json"
                consolidated_path = output_base_path / consolidated_filename
                result_files.append(str(consolidated_path))
                continue

            self.log.info(
                f"Running experiment {i+1}/{len(self.experiments)}: {experiment.name} (type: {experiment.experiment_type})"
            )

            # Load data from input source based on experiment type
            data_items = self.load_data_from_source(
                input_path, experiment.experiment_type, queries_source
            )
            self.log.info(
                f"Loaded {len(data_items)} data items from {input_path} for {experiment.experiment_type} experiment"
            )

            result_file = self.run_experiment(
                experiment, data_items, output_dir, delay_seconds
            )
            result_files.append(result_file)

            # Add delay between experiments
            if delay_seconds > 0 and i < len(self.experiments) - 1:
                self.log.info(f"Waiting {delay_seconds}s before next experiment...")
                time.sleep(delay_seconds)

        # Calculate total time for all experiments
        total_time = time.time() - all_experiments_start_time

        if skipped_count > 0:
            self.log.info(
                f"Completed {len(result_files) - skipped_count} new experiments, skipped {skipped_count} existing"
            )
        else:
            self.log.info(
                f"Completed {len(result_files)} out of {len(self.experiments)} experiments"
            )
        self.log.info(f"Total execution time: {round(total_time, 2)} seconds")

        # Create consolidated experiments report at root level
        if len(result_files) > 1:
            consolidated_report_path = self._create_consolidated_experiments_report(
                result_files, output_dir, input_path, total_time
            )
            self.log.info(
                f"Consolidated experiments report saved to: {consolidated_report_path}"
            )

        return result_files, skipped_count

    def _create_consolidated_experiments_report(
        self,
        result_files: List[str],
        output_dir: str,
        input_path: str,
        total_time: float = None,
    ) -> str:
        """Create a consolidated report of all experiments."""
        output_base_path = Path(output_dir)

        # Load all experiment results
        all_experiments = []
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}

        for result_file in result_files:
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    experiment_data = json.load(f)

                experiment_info = {
                    "experiment_name": experiment_data["metadata"]["experiment_name"],
                    "experiment_type": experiment_data["metadata"]["experiment_type"],
                    "model": experiment_data["metadata"]["model"],
                    "llm_type": experiment_data["metadata"]["llm_type"],
                    "file_path": str(Path(result_file).relative_to(output_base_path)),
                    "timestamp": experiment_data["metadata"]["timestamp"],
                    "total_items": experiment_data["metadata"]["total_items"],
                    "usage": experiment_data["metadata"]["total_usage"],
                    "cost": experiment_data["metadata"]["total_cost"],
                    "individual_files": experiment_data["metadata"].get(
                        "individual_files", []
                    ),
                    "consolidated_from": experiment_data["metadata"].get(
                        "consolidated_from", 0
                    ),
                }

                # Include analysis results based on experiment type
                if "analysis" in experiment_data:
                    analysis = experiment_data["analysis"]
                    if experiment_data["metadata"]["experiment_type"] == "qa":
                        # Include Q&A results
                        if "qa_results" in analysis:
                            experiment_info["qa_results"] = analysis["qa_results"]
                    elif (
                        experiment_data["metadata"]["experiment_type"]
                        == "summarization"
                    ):
                        # Include summarization results
                        if "summaries" in analysis:
                            experiment_info["summaries"] = analysis["summaries"]

                all_experiments.append(experiment_info)

                # Accumulate totals
                for key in total_usage:
                    total_usage[key] += experiment_data["metadata"]["total_usage"].get(
                        key, 0
                    )
                for key in total_cost:
                    total_cost[key] += experiment_data["metadata"]["total_cost"].get(
                        key, 0.0
                    )

            except Exception as e:
                self.log.error(f"Error loading experiment file {result_file}: {e}")
                continue

        # Create consolidated report
        consolidated_report = {
            "metadata": {
                "report_type": "consolidated_experiments",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "input_source": input_path,
                "output_directory": output_dir,
                "total_experiments": len(all_experiments),
                "total_usage": total_usage,
                "total_cost": total_cost,
            },
            "experiments": all_experiments,
        }

        # Add total execution time if provided
        if total_time is not None:
            consolidated_report["metadata"]["total_execution_time_seconds"] = round(
                total_time, 3
            )

        # Save consolidated report
        consolidated_filename = "consolidated_experiments_report.json"
        consolidated_path = output_base_path / consolidated_filename

        with open(consolidated_path, "w", encoding="utf-8") as f:
            json.dump(consolidated_report, f, indent=2)

        return str(consolidated_path)

    def create_sample_config(self, output_path: str):
        """Create a sample configuration file."""
        sample_config = {
            "description": "Batch experiment configuration for transcript evaluation (both Q&A and summarization)",
            "experiments": [
                {
                    "name": "Claude-Sonnet-QA-Standard",
                    "llm_type": "claude",
                    "model": DEFAULT_CLAUDE_MODEL,
                    "experiment_type": "qa",
                    "system_prompt": "You are a helpful assistant that answers questions about meeting transcripts. Provide accurate, concise answers based on the transcript content.",
                    "max_tokens": 512,
                    "temperature": 0.1,
                    "parameters": {},
                    "_comment": "Cloud inference - will incur API costs",
                },
                {
                    "name": "Claude-Sonnet-Summarization-Standard",
                    "llm_type": "claude",
                    "model": DEFAULT_CLAUDE_MODEL,
                    "experiment_type": "summarization",
                    "system_prompt": "You are an expert meeting analyst. Analyze the transcript carefully and provide clear, accurate information based on the content.",
                    "max_tokens": 512,
                    "temperature": 0.1,
                    "parameters": {},
                    "_comment": "Cloud inference - will incur API costs",
                },
                {
                    "name": "Claude-Sonnet-QA-Detailed",
                    "llm_type": "claude",
                    "model": DEFAULT_CLAUDE_MODEL,
                    "experiment_type": "qa",
                    "system_prompt": "You are an expert meeting analyst. Provide comprehensive, detailed answers about meeting transcripts including context, participants, and implications. Be thorough and precise.",
                    "max_tokens": 1024,
                    "temperature": 0.2,
                    "parameters": {},
                },
                {
                    "name": "Lemonade-Llama-QA-Standard",
                    "llm_type": "lemonade",
                    "model": "llama3.2:3b",
                    "experiment_type": "qa",
                    "system_prompt": "Answer questions about meeting transcripts clearly and accurately. Focus on the key information requested.",
                    "max_tokens": 512,
                    "temperature": 0.1,
                    "parameters": {"host": "localhost", "port": 8000},
                    "_comment": "Local inference - FREE, runs on your hardware",
                },
                {
                    "name": "Lemonade-Llama-Summarization-Creative",
                    "llm_type": "lemonade",
                    "model": "llama3.2:3b",
                    "experiment_type": "summarization",
                    "system_prompt": "You are a creative meeting analyst. Analyze the transcript thoughtfully and provide insightful information that captures key insights and implications.",
                    "max_tokens": 512,
                    "temperature": 0.7,
                    "parameters": {"host": "localhost", "port": 8000},
                    "_comment": "Local inference - FREE, runs on your hardware",
                },
            ],
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(sample_config, f, indent=2)

        self.log.info(f"Sample configuration saved to: {output_path}")

    def create_config_from_groundtruth(
        self, groundtruth_file: str, output_file: str
    ) -> None:
        """Create experiment configuration from groundtruth file metadata."""
        try:
            # Load groundtruth file
            with open(groundtruth_file, "r", encoding="utf-8") as f:
                groundtruth_data = json.load(f)

            metadata = groundtruth_data.get("metadata", {})
            analysis = groundtruth_data.get("analysis", {})

            # Extract key information
            use_case = metadata.get("use_case", "qa")
            original_model = metadata.get("claude_model", DEFAULT_CLAUDE_MODEL)
            original_prompt = metadata.get("system_prompt", "")
            max_tokens = metadata.get("max_tokens", 512 if use_case == "qa" else 1024)
            temperature = metadata.get("temperature", 0.1)

            # Determine appropriate system prompt if not in metadata
            if not original_prompt:
                if use_case == "qa":
                    original_prompt = "You are an expert meeting analyst. Answer questions about the transcript accurately and concisely based only on the provided information."
                elif use_case == "summarization":
                    original_prompt = "You are an expert meeting analyst. Create a concise summary of the transcript including key topics, decisions, and action items."
                else:
                    original_prompt = "You are an expert analyst. Process the provided content according to the task requirements."

            # Create base experiment configuration
            experiments = []

            # Original configuration
            base_name = original_model.replace("claude-", "").replace("-", "-").title()
            experiments.append(
                {
                    "name": f"{base_name}-Original",
                    "llm_type": "claude",
                    "model": original_model,
                    "experiment_type": use_case,
                    "system_prompt": original_prompt,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "parameters": {},
                }
            )

            # Add model variations with same prompt
            model_variants = [
                ("claude-3-haiku-20240307", "Haiku"),
                ("claude-3-opus-20240229", "Opus"),
                (DEFAULT_CLAUDE_MODEL, "Sonnet-4.5"),
            ]

            for model, name in model_variants:
                if model != original_model:  # Don't duplicate original
                    experiments.append(
                        {
                            "name": f"Claude-{name}-Same-Prompt",
                            "llm_type": "claude",
                            "model": model,
                            "experiment_type": use_case,
                            "system_prompt": original_prompt,
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                            "parameters": {},
                        }
                    )

            # Add temperature variations for original model
            if temperature != 0.0:
                experiments.append(
                    {
                        "name": f"{base_name}-Creative",
                        "llm_type": "claude",
                        "model": original_model,
                        "experiment_type": use_case,
                        "system_prompt": original_prompt,
                        "max_tokens": max_tokens,
                        "temperature": min(
                            CREATIVE_TEMPERATURE_MAX,
                            temperature + CREATIVE_TEMPERATURE_INCREMENT,
                        ),
                        "parameters": {},
                    }
                )

            if temperature != 0.0:
                experiments.append(
                    {
                        "name": f"{base_name}-Deterministic",
                        "llm_type": "claude",
                        "model": original_model,
                        "experiment_type": use_case,
                        "system_prompt": original_prompt,
                        "max_tokens": max_tokens,
                        "temperature": 0.0,
                        "parameters": {},
                    }
                )

            # Create configuration structure
            groundtruth_name = Path(groundtruth_file).stem
            config = {
                "description": f"Configuration generated from groundtruth metadata: {groundtruth_name}",
                "source_groundtruth": groundtruth_file,
                "generated_at": datetime.now().isoformat(),
                "original_metadata": metadata,
                "experiments": experiments,
            }

            # Save configuration
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            self.log.info(
                f"Generated experiment configuration with {len(experiments)} experiments"
            )
            self.log.info(f"Configuration saved to: {output_path}")

            return str(output_path)

        except Exception as e:
            self.log.error(f"Error creating config from groundtruth: {e}")
            raise


def main():
    """Command line interface for batch experiments."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run batch experiments with different LLM configurations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create sample configuration file
  python -m gaia.eval.batch_experiment --create-sample-config experiment_config.json

  # Run batch experiments on transcript directory
  python -m gaia.eval.batch_experiment -c experiment_config.json -i ./transcripts -o ./experiments

  # Run batch experiments on transcript directory with custom queries from groundtruth
  python -m gaia.eval.batch_experiment -c experiment_config.json -i ./transcripts -q ./groundtruth/meeting.qa.groundtruth.json -o ./experiments

  # Run batch experiments on groundtruth file
  python -m gaia.eval.batch_experiment -c experiment_config.json -i ./groundtruth/transcript.qa.groundtruth.json -o ./experiments

  # Run batch experiments on consolidated groundtruth file
  python -m gaia.eval.batch_experiment -c experiment_config.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments

  # Run with custom delay between requests
  python -m gaia.eval.batch_experiment -c experiment_config.json -i ./transcripts -o ./experiments --delay 2.0
        """,
    )

    parser.add_argument(
        "-c", "--config", type=str, help="Path to experiment configuration JSON file"
    )
    parser.add_argument(
        "-i",
        "--input",
        type=str,
        help="Path to input data: transcript file, directory of transcripts, or groundtruth JSON file",
    )
    parser.add_argument(
        "-q",
        "--queries-source",
        type=str,
        help="Path to groundtruth JSON file to extract queries from (for QA experiments on raw transcripts)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default="./experiments",
        help="Output directory for experiment results (default: ./experiments)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between requests to avoid rate limiting (default: 1.0)",
    )
    parser.add_argument(
        "--create-sample-config",
        type=str,
        help="Create a sample configuration file at the specified path",
    )
    parser.add_argument(
        "--create-config-from-groundtruth",
        type=str,
        help="Create configuration from groundtruth file metadata (provide groundtruth file path)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration of all experiments, even if they already exist (default: skip existing)",
    )

    args = parser.parse_args()

    # Create sample config if requested
    if args.create_sample_config:
        runner = BatchExperimentRunner.__new__(BatchExperimentRunner)
        runner.log = get_logger(__name__)
        runner.create_sample_config(args.create_sample_config)
        print(f"✅ Sample configuration created: {args.create_sample_config}")
        print("Edit this file to define your experiments, then run:")
        print(
            f"  python -m gaia.eval.batch_experiment -c {args.create_sample_config} -i <input_path> -o <output_dir>"
        )
        return

    # Create config from groundtruth if requested
    if args.create_config_from_groundtruth:
        # Determine output filename if not provided in the argument
        groundtruth_path = Path(args.create_config_from_groundtruth)
        default_output = f"{groundtruth_path.stem}.config.json"

        runner = BatchExperimentRunner.__new__(BatchExperimentRunner)
        runner.log = get_logger(__name__)
        config_path = runner.create_config_from_groundtruth(
            args.create_config_from_groundtruth, default_output
        )
        print(f"✅ Configuration created from groundtruth metadata: {config_path}")
        print("Review and edit the configuration, then run:")
        print(
            f"  python -m gaia.eval.batch_experiment -c {config_path} -i <input_path> -o <output_dir>"
        )
        return

    # Validate required arguments
    if not args.config or not args.input:
        parser.error(
            "Both --config and --input are required (unless using --create-sample-config or --create-config-from-groundtruth)"
        )

    # Run batch experiments
    runner = BatchExperimentRunner(args.config)
    # By default skip existing experiments, unless --force is specified
    skip_existing = not args.force
    result_files, skipped_count = runner.run_all_experiments(
        input_path=args.input,
        output_dir=args.output_dir,
        delay_seconds=args.delay,
        queries_source=args.queries_source,
        skip_existing=skip_existing,
    )

    # Report results with skip information
    if skipped_count > 0:
        new_count = len(result_files) - skipped_count
        print(
            f"✅ Completed {len(result_files)} experiments ({new_count} new, {skipped_count} skipped)"
        )
    else:
        print(f"✅ Completed {len(result_files)} experiments")

    print(f"  Results saved to: {args.output_dir}")
    print(f"  Generated files:")
    for result_file in result_files:
        print(f"    - {Path(result_file).name}")

    print(f"\nNext steps:")
    print(f"  1. Evaluate results using: gaia eval -f <result_file>")
    print(f"  2. Generate comparative report: gaia report -d {args.output_dir}")


if __name__ == "__main__":
    exit(main())
