#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Gaia Summarizer Application - Processes meeting transcripts and emails to generate summaries
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from gaia.chat.sdk import ChatConfig, ChatSDK
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.logger import get_logger


def validate_email_address(email: str) -> bool:
    """Validate email address format"""
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(email_pattern, email.strip()) is not None


def validate_email_list(email_list: str) -> List[str]:
    """Validate and parse comma-separated email list"""
    if not email_list:
        return []

    emails = [e.strip() for e in email_list.split(",") if e.strip()]
    invalid_emails = [e for e in emails if not validate_email_address(e)]

    if invalid_emails:
        raise ValueError(f"Invalid email address(es): {', '.join(invalid_emails)}")

    return emails


# Summary style definitions
SUMMARY_STYLES = {
    "brief": "Generate a concise 2-3 sentence summary highlighting the most important points.",
    "detailed": "Generate a comprehensive summary with all key details, context, and nuances.",
    "bullets": "Generate key points in a clear bullet-point format, focusing on actionable items.",
    "executive": "Generate a high-level executive summary focusing on decisions, outcomes, and strategic implications.",
    "participants": "Extract and list all meeting participants with their roles if mentioned.",
    "action_items": "Extract all action items with owners and deadlines where specified.",
}

# System prompts for different content types
SYSTEM_PROMPTS = {
    "transcript": "You are a professional meeting summarizer. Analyze meeting transcripts to extract key information, decisions, and action items. Be precise and comprehensive.",
    "email": "You are a professional email summarizer. Analyze emails to extract key information, requests, and required actions. Focus on the sender's intent and recipient's needed response.",
}


@dataclass
class SummaryConfig:
    """Configuration for summarization"""

    model: str = DEFAULT_MODEL_NAME
    max_tokens: int = 1024
    input_type: Literal["transcript", "email", "auto"] = "auto"
    styles: List[str] = None
    combined_prompt: bool = False
    use_claude: bool = False
    use_chatgpt: bool = False

    def __post_init__(self):
        if self.styles is None:
            self.styles = ["executive", "participants", "action_items"]
        # Validate styles
        valid_styles = set(SUMMARY_STYLES.keys())
        invalid_styles = [s for s in self.styles if s not in valid_styles]
        if invalid_styles:
            raise ValueError(
                f"Invalid style(s): {', '.join(invalid_styles)}. Valid styles: {', '.join(valid_styles)}"
            )

        # Auto-detect OpenAI models (gpt-*) to use ChatGPT
        if self.model.lower().startswith("gpt"):
            self.use_chatgpt = True


class SummarizerApp:
    """Main application class for summarization"""

    def __init__(self, config: Optional[SummaryConfig] = None):
        """Initialize the summarizer application"""
        self.config = config or SummaryConfig()
        self.log = get_logger(__name__)

        # Initialize base chat SDK
        chat_config = ChatConfig(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            use_claude=self.config.use_claude,
            use_chatgpt=self.config.use_chatgpt,
            show_stats=True,
        )
        self.chat_sdk = ChatSDK(chat_config)

        # Direct access to LLM client for performance stats
        self.llm_client = self.chat_sdk.llm_client

        # Retry configuration
        self.max_retries = 3
        self.retry_delay = 1.0  # seconds

    def detect_content_type(self, content: str) -> str:
        """Auto-detect if content is a transcript or email using LLM"""
        if self.config.input_type != "auto":
            return self.config.input_type

        # First try simple heuristics
        email_patterns = [
            r"From:\s*\S+",
            r"To:\s*\S+",
            r"Subject:\s*\S+",
            r"Dear\s+\w+",
            r"Sincerely,?\s*\n",
            r"Best regards,?\s*\n",
        ]

        transcript_patterns = [
            r"\w+:\s*[^\n]+",  # Speaker: dialogue
            r"\[\d{1,2}:\d{2}\]",  # Time stamps
            r"\(\d{1,2}:\d{2}\)",
        ]

        # Count pattern matches
        email_score = sum(
            1
            for pattern in email_patterns
            if re.search(pattern, content[:500], re.IGNORECASE)
        )
        transcript_score = sum(
            1 for pattern in transcript_patterns if re.search(pattern, content[:500])
        )

        if email_score > transcript_score and email_score >= 2:
            detected_type = "email"
        elif transcript_score > email_score and transcript_score >= 2:
            detected_type = "transcript"
        else:
            # Use LLM as fallback with retry logic
            detection_prompt = (
                """Analyze this text and determine if it's a meeting transcript or an email.
            
            A meeting transcript typically has:
            - Multiple speakers with dialogue
            - Time stamps or speaker labels
            - Conversational flow
            
            An email typically has:
            - From/To/Subject headers or email-like structure
            - Formal greeting and closing
            - Single author perspective
            
            Respond with ONLY one word: 'transcript' or 'email'
            
            Text to analyze:
            """
                + content[:1000]
            )  # Only use first 1000 chars for detection

            for attempt in range(self.max_retries):
                try:
                    response = self.llm_client.generate(
                        detection_prompt, model=self.config.model, max_tokens=10
                    )

                    detected_type = response.strip().lower()
                    if detected_type not in ["transcript", "email"]:
                        # Default to transcript if unclear
                        detected_type = "transcript"
                    break
                except Exception as e:
                    if attempt < self.max_retries - 1:
                        self.log.warning(
                            f"Content type detection attempt {attempt + 1} failed: {e}. Retrying..."
                        )
                        time.sleep(self.retry_delay * (attempt + 1))
                    else:
                        self.log.error(
                            f"Failed to detect content type after {self.max_retries} attempts"
                        )
                        detected_type = "transcript"  # Default fallback

        self.log.info(f"Auto-detected content type: {detected_type}")
        return detected_type

    def generate_summary_prompt(
        self, content: str, content_type: str, style: str
    ) -> str:
        """Generate the prompt for a specific summary style"""
        style_instruction = SUMMARY_STYLES.get(style, SUMMARY_STYLES["brief"])

        if style == "participants" and content_type == "email":
            # Special handling for email participants
            prompt = f"""Extract the sender and all recipients from this email.
            
            Format your response as JSON:
            {{
                "sender": "sender email/name",
                "recipients": ["recipient1", "recipient2"],
                "cc": ["cc1", "cc2"] (if any),
                "bcc": ["bcc1"] (if any)
            }}
            
            Email content:
            {content}"""
        elif style == "action_items":
            prompt = f"""Extract all action items from this {content_type}.
            
            {style_instruction}
            
            Format each action item with:
            - The specific action required
            - Who is responsible (if mentioned)
            - Any deadline or timeline (if mentioned)
            
            If no action items are found, respond with "No specific action items identified."
            
            Content:
            {content}"""
        else:
            prompt = f"""Analyze this {content_type} and {style_instruction}
            
            Content:
            {content}"""

        return prompt

    def generate_combined_prompt(
        self, content: str, content_type: str, styles: List[str]
    ) -> str:
        """Generate a single prompt for multiple summary styles"""
        sections = []
        for style in styles:
            style_instruction = SUMMARY_STYLES.get(style, SUMMARY_STYLES["brief"])
            sections.append(f"- {style.upper()}: {style_instruction}")

        prompt = f"""Analyze this {content_type} and generate the following summaries:

{chr(10).join(sections)}

Format your response with clear section headers for each style.

Content:
{content}"""

        return prompt

    def summarize_with_style(
        self, content: str, content_type: str, style: str
    ) -> Dict[str, Any]:
        """Generate a summary for a specific style with retry logic"""
        start_time = time.time()

        # Set appropriate system prompt
        system_prompt = SYSTEM_PROMPTS.get(content_type, SYSTEM_PROMPTS["transcript"])
        self.chat_sdk.config.system_prompt = system_prompt

        # Generate prompt
        prompt = self.generate_summary_prompt(content, content_type, style)

        # Check if content might exceed token limits
        estimated_tokens = len(content.split()) + len(prompt.split())
        if estimated_tokens > 3000:  # Conservative estimate
            self.log.warning(
                f"Content may exceed token limits. Estimated tokens: {estimated_tokens}"
            )

        # Get summary with retry logic
        response = None
        last_error = None

        for attempt in range(self.max_retries):
            try:
                response = self.chat_sdk.send(prompt)
                break
            except Exception as e:
                last_error = e
                error_msg = str(e).lower()

                # Check for specific error types
                if "token" in error_msg and "limit" in error_msg:
                    # Token limit error - reduce content or max_tokens
                    self.log.warning(
                        f"Token limit exceeded. Attempting with reduced content..."
                    )
                    # Truncate content to 75% of original
                    truncated_content = (
                        content[: int(len(content) * 0.75)]
                        + "\n\n[Content truncated due to length...]"
                    )
                    prompt = self.generate_summary_prompt(
                        truncated_content, content_type, style
                    )
                elif "connection" in error_msg or "timeout" in error_msg:
                    self.log.warning(f"Connection error on attempt {attempt + 1}: {e}")
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay * (attempt + 1))
                        continue
                else:
                    self.log.error(f"Unexpected error on attempt {attempt + 1}: {e}")

                if attempt >= self.max_retries - 1:
                    raise RuntimeError(
                        f"Failed to generate {style} summary after {self.max_retries} attempts: {last_error}"
                    )

        # Get performance stats
        try:
            perf_stats = self.llm_client.get_performance_stats()
        except Exception as e:
            self.log.warning(f"Failed to get performance stats: {e}")
            perf_stats = {}

        # Calculate processing time
        processing_time_ms = int((time.time() - start_time) * 1000)

        # Format result based on style
        result = {"text": response.text}

        # Add style-specific fields
        if style == "action_items":
            # Try to parse action items from response
            lines = response.text.strip().split("\n")
            items = []
            for line in lines:
                line = line.strip()
                # Include all non-empty lines except obvious headers
                if (
                    line
                    and not line.lower().startswith("action items:")
                    and not line.startswith("**Action")
                ):
                    items.append(line)
            if items:
                result["items"] = items

        elif style == "participants":
            if content_type == "email":
                # Try to parse JSON response for email participants
                try:
                    participants_data = json.loads(response.text)
                    result.update(participants_data)
                except:
                    # Fallback to text if not valid JSON
                    pass
            else:
                # Extract participants from transcript response
                lines = response.text.strip().split("\n")
                participants = []
                for line in lines:
                    line = line.strip()
                    # Include all non-empty lines (HTML viewer will format properly)
                    if line and not line.lower().startswith("participants:"):
                        participants.append(line)
                if participants:
                    result["participants"] = participants

        # Add performance data
        result["performance"] = {
            "total_tokens": perf_stats.get("input_tokens", 0)
            + perf_stats.get("output_tokens", 0),
            "prompt_tokens": perf_stats.get("input_tokens", 0),
            "completion_tokens": perf_stats.get("output_tokens", 0),
            "time_to_first_token_ms": int(
                perf_stats.get("time_to_first_token", 0) * 1000
            ),
            "tokens_per_second": perf_stats.get("tokens_per_second", 0),
            "processing_time_ms": processing_time_ms,
        }

        return result

    def summarize_combined(
        self, content: str, content_type: str, styles: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Generate summaries for multiple styles in a single LLM call"""
        start_time = time.time()

        # Set appropriate system prompt
        system_prompt = SYSTEM_PROMPTS.get(content_type, SYSTEM_PROMPTS["transcript"])
        self.chat_sdk.config.system_prompt = system_prompt

        # Generate combined prompt
        prompt = self.generate_combined_prompt(content, content_type, styles)

        # Get combined summary
        response = self.chat_sdk.send(prompt)

        # Get performance stats
        perf_stats = self.llm_client.get_performance_stats()

        # Calculate processing time
        processing_time_ms = int((time.time() - start_time) * 1000)

        # Parse response into sections
        # This is a simple parser - in production, might want more robust parsing
        response_text = response.text
        results = {}

        for style in styles:
            # Look for style header in response
            style_upper = style.upper()
            start_markers = [
                f"{style_upper}:",
                f"**{style_upper}**:",
                f"# {style_upper}",
                f"## {style_upper}",
            ]

            section_start = -1
            for marker in start_markers:
                idx = response_text.find(marker)
                if idx != -1:
                    section_start = idx + len(marker)
                    break

            if section_start == -1:
                # Fallback - use entire response for first style
                if not results:
                    results[style] = {"text": response_text.strip()}
                continue

            # Find end of section (next style header or end of text)
            section_end = len(response_text)
            for next_style in styles:
                if next_style == style:
                    continue
                next_upper = next_style.upper()
                for marker in [
                    f"{next_upper}:",
                    f"**{next_upper}**:",
                    f"# {next_upper}",
                    f"## {next_upper}",
                ]:
                    idx = response_text.find(marker, section_start)
                    if idx != -1 and idx < section_end:
                        section_end = idx

            section_text = response_text[section_start:section_end].strip()
            results[style] = {"text": section_text}

        # Add shared performance data to each result
        base_perf = {
            "total_tokens": perf_stats.get("input_tokens", 0)
            + perf_stats.get("output_tokens", 0),
            "prompt_tokens": perf_stats.get("input_tokens", 0),
            "completion_tokens": perf_stats.get("output_tokens", 0),
            "time_to_first_token_ms": int(
                perf_stats.get("time_to_first_token", 0) * 1000
            ),
            "tokens_per_second": perf_stats.get("tokens_per_second", 0),
            "processing_time_ms": processing_time_ms,
        }

        # Distribute performance metrics proportionally (simplified)
        style_count = len(styles)
        for style in results:
            results[style]["performance"] = {
                **base_perf,
                "total_tokens": base_perf["total_tokens"] // style_count,
                "completion_tokens": base_perf["completion_tokens"] // style_count,
            }

        return results

    def summarize(
        self, content: str, input_file: Optional[str] = None
    ) -> Dict[str, Any]:
        """Main summarization method"""
        start_time = time.time()

        # Detect content type
        content_type = self.detect_content_type(content)

        # Filter applicable styles
        applicable_styles = self.config.styles.copy()
        if content_type == "email" and "participants" in applicable_styles:
            # Keep participants for email but handle differently
            pass

        # Generate summaries
        if self.config.combined_prompt and len(applicable_styles) > 1:
            # Use combined prompt for efficiency
            summaries = self.summarize_combined(
                content, content_type, applicable_styles
            )
        else:
            # Generate each style independently
            summaries = {}
            for style in applicable_styles:
                summaries[style] = self.summarize_with_style(
                    content, content_type, style
                )

        # Calculate aggregate performance
        total_processing_time = int((time.time() - start_time) * 1000)

        # Build output structure
        if len(applicable_styles) == 1:
            # Single style output
            style = applicable_styles[0]
            output = {
                "metadata": {
                    "input_file": input_file or "stdin",
                    "input_type": content_type,
                    "model": self.config.model,
                    "timestamp": datetime.now().isoformat(),
                    "processing_time_ms": total_processing_time,
                    "summary_style": style,
                },
                "summary": summaries[style],
                "performance": summaries[style].get("performance", {}),
                "original_content": content,
            }
        else:
            # Multiple styles output
            output = {
                "metadata": {
                    "input_file": input_file or "stdin",
                    "input_type": content_type,
                    "model": self.config.model,
                    "timestamp": datetime.now().isoformat(),
                    "processing_time_ms": total_processing_time,
                    "summary_styles": applicable_styles,
                },
                "summaries": summaries,
                "aggregate_performance": {
                    "total_tokens": sum(
                        s.get("performance", {}).get("total_tokens", 0)
                        for s in summaries.values()
                    ),
                    "total_processing_time_ms": total_processing_time,
                    "model_info": {
                        "model": self.config.model,
                        "use_local": not (
                            self.config.use_claude or self.config.use_chatgpt
                        ),
                        "use_claude": self.config.use_claude,
                        "use_chatgpt": self.config.use_chatgpt,
                    },
                },
                "original_content": content,
            }

        return output

    def summarize_file(self, file_path: Path) -> Dict[str, Any]:
        """Summarize a single file"""
        self.log.info(f"Summarizing file: {file_path}")

        # Validate file exists
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Check file size
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 10:
            self.log.warning(
                f"Large file ({file_size_mb:.1f}MB) may exceed token limits"
            )

        try:
            content = file_path.read_text(encoding="utf-8")
            if not content.strip():
                raise ValueError(f"File is empty: {file_path}")
            return self.summarize(content, str(file_path))
        except UnicodeDecodeError:
            # Try alternative encodings
            for encoding in ["latin-1", "cp1252"]:
                try:
                    content = file_path.read_text(encoding=encoding)
                    self.log.info(f"Successfully read file with {encoding} encoding")
                    return self.summarize(content, str(file_path))
                except UnicodeDecodeError:
                    continue
            raise ValueError(
                f"Unable to decode file {file_path}. File may be binary or use unsupported encoding."
            )
        except Exception as e:
            self.log.error(f"Error processing file {file_path}: {e}")
            raise

    def summarize_directory(self, dir_path: Path) -> List[Dict[str, Any]]:
        """Summarize all files in a directory"""
        self.log.info(f"Summarizing directory: {dir_path}")

        # Validate directory exists
        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {dir_path}")
        if not dir_path.is_dir():
            raise ValueError(f"Path is not a directory: {dir_path}")

        results = []
        errors = []

        # Find all text files
        text_extensions = [".txt", ".md", ".log", ".email", ".transcript"]
        files = []
        for ext in text_extensions:
            files.extend(dir_path.glob(f"*{ext}"))

        if not files:
            self.log.warning(f"No text files found in {dir_path}")
            return results

        self.log.info(f"Found {len(files)} files to process")

        for i, file_path in enumerate(sorted(files), 1):
            try:
                self.log.info(f"Processing file {i}/{len(files)}: {file_path.name}")
                result = self.summarize_file(file_path)
                results.append(result)
            except Exception as e:
                error_msg = f"Failed to summarize {file_path}: {e}"
                self.log.error(error_msg)
                errors.append(error_msg)
                continue

        if errors:
            self.log.warning(
                f"Completed with {len(errors)} errors:\n" + "\n".join(errors)
            )

        return results
