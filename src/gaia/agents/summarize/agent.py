# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
SummarizerAgent: GAIA agent for advanced text/document summarization.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from gaia.agents.base import Agent
from gaia.chat.sdk import ChatConfig, ChatSDK
from gaia.logger import get_logger
from gaia.rag.sdk import RAGSDK

from .prompts import (
    DETECTION_PROMPT_TEMPLATE,
    DOCUMENT_SUMMARY_TEMPLATE,
    ITERATIVE_SUMMARY_TEMPLATE,
    SUMMARY_STYLES,
    SYSTEM_PROMPTS,
)


class Chunker:
    def __init__(self):
        self.logger = get_logger(__name__)
        # Simple sentence splitter to avoid NLTK dependency
        self._sentence_split_regex = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

    def count_tokens(self, text: str) -> int:
        """Simple estimation, Lemonade Server does not expose tokenize endpoint."""
        chars = len(text)
        words = len(text.split())
        est_by_chars = chars // 4
        est_by_words = int(words * 1.3)
        num_tokens = max(est_by_chars, est_by_words)

        self.logger.info(f"Approximated token count: {num_tokens} tokens")
        return num_tokens

    def chunk_text(self, text: str, max_tokens: int, overlap_tokens: int) -> List[str]:
        if not text:
            return []

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks = []
        current_chunk = []
        current_tokens = 0

        for para in paragraphs:
            para_tokens = self.count_tokens(para)

            # Split very long paragraphs into sentences using simple heuristics
            units = [para]
            if para_tokens > max_tokens:
                units = [
                    s.strip()
                    for s in self._sentence_split_regex.split(para)
                    if s.strip()
                ]

            for unit in units:
                unit_tokens = self.count_tokens(unit)

                if current_tokens + unit_tokens > max_tokens:
                    # Output current chunk
                    if current_chunk:
                        chunk_text = " ".join(current_chunk)
                        chunks.append(chunk_text)
                        self.logger.info(
                            f"Created chunk {len(chunks)}: {len(chunk_text)} chars"
                        )

                    # Prepare next chunk with overlap
                    if overlap_tokens > 0:
                        overlap = []
                        overlap_count = 0
                        for u in reversed(current_chunk):
                            t = self.count_tokens(u)
                            if overlap_count + t > overlap_tokens:
                                break
                            overlap.insert(0, u)
                            overlap_count += t
                        current_chunk = overlap
                        current_tokens = sum(self.count_tokens(x) for x in overlap)
                    else:
                        current_chunk = []
                        current_tokens = 0

                # Add new unit
                current_chunk.append(unit)
                current_tokens += unit_tokens

        # push last chunk
        if current_chunk:
            chunk_text = " ".join(current_chunk)
            chunks.append(chunk_text)
            try:
                self.logger.info(
                    f"Created chunk {len(chunks)}: {len(chunk_text)} chars"
                )
            except Exception as e:
                self.logger.warning(f"Failed to log chunk creation: {e}")

        self.logger.info(f"Total chunks created: {len(chunks)}")
        return chunks


class SummarizerAgent(Agent):

    DEFAULT_MODEL = "Qwen3-4B-Instruct-2507-GGUF"

    def __init__(
        self,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        max_ctx_size: int = 8192,
        styles: Optional[List[str]] = None,
        combined_prompt: bool = False,
        use_claude: bool = False,
        use_chatgpt: bool = False,
    ):
        self.model = model or self.DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.styles = styles or ["executive", "participants", "action_items"]
        self.combined_prompt = combined_prompt
        self.use_claude = use_claude
        self.use_chatgpt = use_chatgpt
        self.log = get_logger(__name__)
        chat_config = ChatConfig(
            model=self.model,
            max_tokens=self.max_tokens,
            use_claude=self.use_claude,
            use_chatgpt=self.use_chatgpt,
            show_stats=True,
        )
        self.chat_sdk = ChatSDK(chat_config)
        self.rag_sdk = RAGSDK()
        self.chunker = Chunker()
        self.llm_client = self.chat_sdk.llm_client
        self.rag_sdk.llm_client = self.llm_client
        self.max_retries = 3
        self.retry_delay = 1.0
        # Default 8192 balances context size with TTFT for responsive UI.
        # Can be increased for larger documents if TTFT is not critical.
        self.max_ctx_size = max_ctx_size
        self.overlap_tokens_ratio = 0.05
        self.chunk_tokens = int(self.max_ctx_size * 0.7)
        self.overlap_tokens = int(self.chunk_tokens * self.overlap_tokens_ratio)

        # Load prompts from prompts.py
        self.summary_styles = SUMMARY_STYLES
        self.system_prompts = SYSTEM_PROMPTS
        self.iterative_summary_template = ITERATIVE_SUMMARY_TEMPLATE
        self.document_summary_template = DOCUMENT_SUMMARY_TEMPLATE
        self.detection_prompt_template = DETECTION_PROMPT_TEMPLATE

        # Initialize parent class after setting required attributes
        super().__init__()

        # Disk cache for extracted text
        self._text_cache_dir = Path(".gaia") / "text_cache"
        try:
            self._text_cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise RuntimeError("Failed to create text cache directory") from e

    def _get_system_prompt(self, content_type: Optional[str] = None) -> str:
        """Return the system prompt for the agent.

        Args:
            content_type: Optional content type (email, transcript, pdf).
                         If None, returns default transcript prompt.

        Returns:
            System prompt string for the specified content type.
        """
        if content_type is None:
            content_type = "transcript"
        return self.system_prompts.get(
            content_type, self.system_prompts.get("transcript", "")
        )

    def _register_tools(self) -> None:
        """Register tools for the agent. No tools needed for summarizer."""

    def _prepare_chat(self, input_type: str) -> None:
        """Clear prior chat context and set system prompt for the given input type."""
        try:
            self.chat_sdk.clear_history()
        except Exception as e:
            self.log.warning(f"Failed to clear chat history: {e}")
        system_prompt = self._get_system_prompt(input_type)
        if not system_prompt:
            raise KeyError(f"Missing system prompt for '{input_type}' in prompts")
        self.chat_sdk.config.system_prompt = system_prompt

    def _validate_styles(self, styles: Any) -> None:
        """Validate provided style or list of styles against prompt definitions."""
        allowed = set((self.summary_styles or {}).keys())
        provided = styles if isinstance(styles, list) else [styles]
        invalid = [s for s in provided if s not in allowed]
        if invalid:
            allowed_list = ", ".join(sorted(allowed))
            raise ValueError(
                f"Unsupported style(s): {', '.join(invalid)}. Allowed styles: {allowed_list}"
            )

    def _should_use_iterative(self, text: str) -> bool:
        """Decide if iterative summarization is needed based on estimated tokens."""
        # Reserve 25% of context for prompts, instructions, and output
        # Apply additional 15% safety margin to account for token estimation variance
        effective_limit = int(self.max_ctx_size * 0.75 * 0.87)  # 0.87 = 1/1.15
        content_tokens = self.chunker.count_tokens(text)
        should_iterate = content_tokens > effective_limit

        if should_iterate:
            self.log.info(
                f"Using iterative summarization: {content_tokens} tokens > {effective_limit} effective limit "
                f"(65% of {self.max_ctx_size} max context with safety margin)"
            )

        return should_iterate

    def _iterative_summarize(
        self,
        text: str,
        style: str = "brief",
        content_type: str = "pdf",
    ) -> Dict[str, Any]:
        """Iteratively fold large text; reuse streaming generator to avoid duplication."""
        final_text = ""
        final_stats: Dict[str, Any] = {}
        for evt in self._iterative_summary_events(text, content_type, style):
            if evt.get("is_complete"):
                final_text = evt.get("text", "")
                final_stats = evt.get("performance", {})
        return {"text": final_text, "performance": final_stats}

    def _summarize_content(
        self,
        content: str,
        input_file: Optional[str],
        input_type: str,
        styles: Optional[List[str]],
        combined_prompt: Optional[bool],
    ) -> Dict[str, Any]:
        """Summarize content choosing iterative vs direct path, returning structured output."""
        should_iterate = self._should_use_iterative(content)

        if should_iterate:
            if input_type == "pdf":
                self.log.info("Large content detected; using iterative summarization")
                brief = self._iterative_summarize(
                    content, "brief", content_type=input_type
                )
                return self.summarize(
                    brief.get("text", ""),
                    input_file,
                    input_type=input_type,
                    styles=styles,
                    combined_prompt=combined_prompt,
                )
            else:
                self.log.warning(
                    f"Content is large enough for iterative summarization but input type is '{input_type}'. "
                    f"Attempting direct summarization which may exceed token limits. "
                    f"Consider splitting the content manually or converting to PDF."
                )

        return self.summarize(
            content,
            input_file,
            input_type=input_type,
            styles=styles,
            combined_prompt=combined_prompt,
        )

    def _stream_summary_content(self, content: str, input_type: str, style: str):
        """Stream summary for content, using iterative folding for large inputs."""
        self._prepare_chat(input_type)
        if not self._should_use_iterative(content):
            prompt = self.generate_summary_prompt(content, input_type, style)
            for chunk in self.chat_sdk.send_stream(prompt):
                if chunk.is_complete:
                    yield {
                        "text": "",
                        "is_complete": True,
                        "performance": chunk.stats or {},
                    }
                else:
                    yield {"text": chunk.text, "is_complete": False}
            return
        # Large inputs: delegate to unified iterative streaming generator
        yield from self._iterative_summary_events(content, input_type, style)

    def _stream_chunk_and_accumulate(
        self,
        prompt: str,
        chunk_index: int,
        total_chunks: int,
        label: str = "LLM Prompt",
    ):
        """Helper to stream a chunk's LLM response and return the accumulated text."""
        self.log.info(
            f"[{label} - chunk {chunk_index+1}/{total_chunks}] {prompt[:500]}..."
        )
        streamed_text = ""
        for part in self.chat_sdk.send_stream(prompt):
            if part.is_complete:
                return streamed_text.strip()
            else:
                streamed_text += part.text
                yield {"text": part.text, "is_complete": False}
        return streamed_text.strip()

    def _iterative_summary_events(self, content: str, input_type: str, style: str):
        """Unified generator for iterative summarization: streams per-chunk and yields final stats."""
        self._prepare_chat(input_type)
        summary_so_far = ""
        chunk_tokens = int(self.max_ctx_size * 0.7)
        overlap_tokens = int(chunk_tokens * self.overlap_tokens_ratio)
        chunks = self.chunker.chunk_text(content, chunk_tokens, overlap_tokens)
        for i, chunk in enumerate(chunks):
            style_instruction = (self.summary_styles or {}).get(style)
            if not style_instruction:
                raise KeyError(f"Missing style '{style}' in prompts")
            if i == 0:
                base_prompt = self.document_summary_template.format(
                    style_instruction=style_instruction, document_text=chunk
                )
            else:
                base_prompt = self.iterative_summary_template.format(
                    style_instruction=style_instruction,
                    previous_summary=summary_so_far,
                    new_chunk=chunk,
                )
            try:
                completed = yield from self._stream_chunk_and_accumulate(
                    base_prompt, i, len(chunks)
                )
                if completed:
                    summary_so_far = (
                        summary_so_far + ("\n" if summary_so_far else "") + completed
                    )
                yield {"text": "\n", "is_complete": False}
            except Exception as e:
                self.log.error(f"Failed to process chunk {i+1}/{len(chunks)}: {e}")
                raise
        try:
            perf_stats = self.llm_client.get_performance_stats()
        except Exception as e:
            self.log.warning(f"Failed to retrieve performance stats: {e}")
            perf_stats = {}
        yield {
            "text": summary_so_far,
            "is_complete": True,
            "performance": {
                "total_tokens": perf_stats.get("input_tokens", 0)
                + perf_stats.get("output_tokens", 0),
                "prompt_tokens": perf_stats.get("input_tokens", 0),
                "completion_tokens": perf_stats.get("output_tokens", 0),
                "time_to_first_token_ms": int(
                    perf_stats.get("time_to_first_token", 0) * 1000
                ),
                "tokens_per_second": perf_stats.get("tokens_per_second", 0),
            },
        }

    def detect_content_type(self, content: str, input_type: str = "auto") -> str:
        if input_type != "auto":
            return input_type

        email_patterns = [
            r"From:\s*[\w\s]+",
            r"To:\s*[\w\s]+",
            r"Subject:\s*[\w\s]+",
            r"Dear\s+[A-Z]+",
            r"Sincerely,\s*[A-Z]+",
            r"Best regards,\s*[A-Z]+",
            r"Re:\s*[\w\s]+",
            r"cc:\s*[\w\s]+",
        ]

        transcript_patterns = [
            r"\w+\s*:\s*[^\n]+",
            r"\[.*:\d{1,2}:\d{2}\]",
            r"\(\d{1,2}:\d{2}\)",
            r"Meeting\s+Transcript",
            r"Project\s+Update",
            r"Action\s+item",
            r"Summary\s+of\s+discussion",
            r"discuss\s+about",
            r"can you give us an update",
            r"how's\s+the\s+design\s+coming",
            r"any\s+blockers",
            r"next\s+step",
            r"review\s+before\s+development",
        ]

        email_score = sum(
            1
            for pattern in email_patterns
            if re.search(pattern, content, re.IGNORECASE)
        )
        transcript_score = sum(
            1
            for pattern in transcript_patterns
            if re.search(pattern, content, re.IGNORECASE)
        )

        if email_score >= 2:
            detected_type = "email"
        elif transcript_score >= 3:
            detected_type = "transcript"
        else:
            # Fall back to LLM only if score is ambiguous
            if self.detection_prompt_template:
                detection_prompt = self.detection_prompt_template.format(
                    text_excerpt=content
                )

            # Add strict output constraints
            for attempt in range(self.max_retries):
                try:
                    response = self.llm_client.generate(
                        detection_prompt, model=self.model
                    )
                    text = (response or "").strip().lower()
                    m = re.findall(r"[a-z]+", text)
                    detected_type = m[0] if m else ""
                    if detected_type not in ["transcript", "email"]:
                        if "transcript" in text:
                            detected_type = "transcript"
                        elif "email" in text:
                            detected_type = "email"
                        else:
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
                        detected_type = "transcript"
            else:
                detected_type = "transcript"  # fallback if loop exits normally

        self.log.info(f"Auto-detected content type: {detected_type}")
        return detected_type

    def generate_summary_prompt(
        self, content: str, content_type: str, style: str
    ) -> str:
        style_instruction = (self.summary_styles or {}).get(style)
        if not style_instruction:
            raise KeyError(f"Missing style '{style}' in prompts")
        if style == "participants" and content_type == "email":
            prompt = f"""Extract the sender and all recipients from this email.\n\nFormat your response as JSON:\n{{\n    \"sender\": \"sender email/name\",\n    \"recipients\": [\"recipient1\", \"recipient2\"],\n    \"cc\": [\"cc1\", \"cc2\"] (if any),\n    \"bcc\": [\"bcc1\"] (if any)\n}}\n\nEmail content:\n{content}"""
        elif style == "action_items":
            prompt = f"""Extract all action items from this {content_type}.\n\n{style_instruction}\n\nFormat each action item with:\n- The specific action required\n- Who is responsible (if mentioned)\n- Any deadline or timeline (if mentioned)\n\nIf no action items are found, respond with \"No specific action items identified.\"\n\nContent:\n{content}"""
        else:
            prompt = f"""Analyze this {content_type} and {style_instruction}\n\nContent:\n{content}"""
        return prompt

    def generate_combined_prompt(
        self, content: str, content_type: str, styles: List[str]
    ) -> str:
        sections = []
        for style in styles:
            style_instruction = (self.summary_styles or {}).get(style)
            if not style_instruction:
                raise KeyError(f"Missing style '{style}' in prompts")
            sections.append(f"- {style.upper()}: {style_instruction}")
        prompt = f"""Analyze this {content_type} and generate the following summaries:\n\n{chr(10).join(sections)}\n\nFormat your response with clear section headers for each style.\n\nContent:\n{content}"""
        return prompt

    def summarize_with_style(
        self, content: str, content_type: str, style: str
    ) -> Dict[str, Any]:
        start_time = time.time()
        system_prompt = self._get_system_prompt(content_type)
        style_instruction = (self.summary_styles or {}).get(style)
        if not style_instruction:
            raise KeyError(f"Missing style '{style}' in prompts")
        # Merge style guidance into the system prompt for consistent behavior
        self.chat_sdk.config.system_prompt = system_prompt
        prompt = self.generate_summary_prompt(content, content_type, style)
        response = None
        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self.chat_sdk.send(prompt)
                break
            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                if "token" in error_msg and "limit" in error_msg:
                    self.log.warning(
                        "Token limit exceeded. Attempting with reduced content..."
                    )
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
        try:
            perf_stats = self.llm_client.get_performance_stats()
        except Exception as e:
            self.log.warning(f"Failed to get performance stats: {e}")
            perf_stats = {}
        processing_time_ms = int((time.time() - start_time) * 1000)
        result = {"text": response.text}
        if style == "action_items":
            lines = response.text.strip().split("\n")
            items = []
            for line in lines:
                line = line.strip()
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
                try:
                    participants_data = json.loads(response.text)
                    result.update(participants_data)
                except (json.JSONDecodeError, ValueError, KeyError):
                    pass
            else:
                lines = response.text.strip().split("\n")
                participants = []
                for line in lines:
                    line = line.strip()
                    if line and not line.lower().startswith("participants:"):
                        participants.append(line)
                if participants:
                    result["participants"] = participants
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
        start_time = time.time()
        system_prompt = self._get_system_prompt(content_type)
        self.chat_sdk.config.system_prompt = system_prompt
        prompt = self.generate_combined_prompt(content, content_type, styles)
        response = self.chat_sdk.send(prompt)
        perf_stats = self.llm_client.get_performance_stats()
        processing_time_ms = int((time.time() - start_time) * 1000)
        response_text = response.text
        results = {}
        for style in styles:
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
                if not results:
                    results[style] = {"text": response_text.strip()}
                continue
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
        style_count = len(styles)
        for style in results:
            results[style]["performance"] = {
                **base_perf,
                "total_tokens": base_perf["total_tokens"] // style_count,
                "completion_tokens": base_perf["completion_tokens"] // style_count,
            }
        return results

    def summarize(
        self,
        content: str,
        input_file: Optional[str] = None,
        input_type: str = "auto",
        styles: Optional[List[str]] = None,
        combined_prompt: Optional[bool] = None,
    ) -> Dict[str, Any]:
        # Ensure no prior conversation context leaks into this summary
        try:
            self.chat_sdk.clear_history()
        except Exception as e:
            self.log.warning(f"Failed to clear chat history: {e}")
        start_time = time.time()
        content_type = self.detect_content_type(content, input_type)
        applicable_styles = styles or self.styles.copy()
        # Early validation: fail fast with clear guidance if a style is unsupported
        self._validate_styles(applicable_styles)
        if content_type == "email" and "participants" in applicable_styles:
            pass
        if (
            combined_prompt if combined_prompt is not None else self.combined_prompt
        ) and len(applicable_styles) > 1:
            summaries = self.summarize_combined(
                content, content_type, applicable_styles
            )
        else:
            summaries = {}
            for style in applicable_styles:
                summaries[style] = self.summarize_with_style(
                    content, content_type, style
                )
        total_processing_time = int((time.time() - start_time) * 1000)
        if len(applicable_styles) == 1:
            style = applicable_styles[0]
            output = {
                "metadata": {
                    "input_file": input_file or "stdin",
                    "input_type": content_type,
                    "model": self.model,
                    "timestamp": datetime.now().isoformat(),
                    "processing_time_ms": total_processing_time,
                    "summary_style": style,
                },
                "summary": summaries[style],
                "performance": summaries[style].get("performance", {}),
                "original_content": content,
            }
        else:
            output = {
                "metadata": {
                    "input_file": input_file or "stdin",
                    "input_type": content_type,
                    "model": self.model,
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
                        "model": self.model,
                        "use_local": not (self.use_claude or self.use_chatgpt),
                        "use_claude": self.use_claude,
                        "use_chatgpt": self.use_chatgpt,
                    },
                },
                "original_content": content,
            }
        return output

    def summarize_stream(
        self, content: str, input_type: str = "auto", style: str = "brief"
    ) -> Generator[Dict[str, Any], None, None]:
        """Stream a single-style summary, using iterative folding for large inputs."""
        self._validate_styles(style)
        yield from self._stream_summary_content(content, input_type, style)

    def _ensure_path(self, file_path) -> Path:
        """Convert file_path to Path object if it's not already."""
        return file_path if isinstance(file_path, Path) else Path(file_path)

    def get_summary_content_from_file(self, file_path: Path) -> str:
        """Extract content to be summarized from a file."""
        file_path = self._ensure_path(file_path)
        abs_path = str(file_path.absolute())
        ext = file_path.suffix.lower()
        if ext == ".pdf":
            # Check disk cache first
            test_cache_path = self._resolve_text_cache_paths(abs_path)
            if test_cache_path and test_cache_path.exists():
                self.log.info(f"[Cache] Using cached PDF text for {file_path.name}")
                return test_cache_path.read_text(encoding="utf-8").strip()

            # Extract fresh text
            pdf_text, _, _ = (
                self.rag_sdk._extract_text_from_pdf(  # pylint: disable=protected-access
                    file_path
                )
            )
            text = pdf_text.strip()
            # Write cache atomically
            cache_path = test_cache_path or self._resolve_text_cache_paths(abs_path)
            if cache_path and text:
                tmp_path = cache_path.with_suffix(".tmp")
                tmp_path.write_text(text, encoding="utf-8")
                try:
                    tmp_path.replace(cache_path)
                except Exception:
                    cache_path.write_text(text, encoding="utf-8")
                self.log.info(f"[Cache] Stored PDF text for {file_path.name}")
            return text
        else:
            # Read as UTF-8, fall back to common encodings
            try:
                text = file_path.read_text(encoding="utf-8").strip()
            except UnicodeDecodeError:
                for encoding in ["latin-1", "cp1252"]:
                    try:
                        text = file_path.read_text(encoding=encoding).strip()
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    text = ""

            # Cache non-PDF text using same content-hash scheme
            if text:
                cache_path = self._resolve_text_cache_paths(abs_path)
                if cache_path:
                    tmp_path = cache_path.with_suffix(".tmp")
                    tmp_path.write_text(text, encoding="utf-8")
                    try:
                        tmp_path.replace(cache_path)
                    except Exception:
                        cache_path.write_text(text, encoding="utf-8")
                    self.log.info(f"[Cache] Stored text for {file_path.name}")
            return text

    def _resolve_text_cache_paths(self, file_path: str) -> Optional[Path]:
        """Return test_cache path for given file content hash, or None.

        test_cache: '<digest>.txt' in cache dir. Legacy removed.
        """
        try:
            p = Path(file_path)
            if not p.exists():
                return None
            import hashlib

            h = hashlib.sha256()
            with p.open("rb") as f:
                while True:
                    b = f.read(1024 * 1024)
                    if not b:
                        break
                    h.update(b)
            digest = h.hexdigest()
            test_cache = self._text_cache_dir.joinpath(f"{digest}.txt")
            return test_cache
        except Exception:
            return None

    def summarize_file(
        self,
        file_path: Path,
        styles: Optional[List[str]] = None,
        combined_prompt: Optional[bool] = None,
        input_type: str = "auto",
    ) -> Dict[str, Any]:
        file_path = self._ensure_path(file_path)
        self.log.info(f"Summarizing file: {file_path}")
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 10:
            self.log.warning(
                f"Large file ({file_size_mb:.1f}MB) may exceed token limits"
            )
        try:
            content = self.get_summary_content_from_file(file_path)
            if not content.strip():
                raise ValueError(f"No extractable text found in {file_path}")
            return self._summarize_content(
                content,
                str(file_path),
                input_type="pdf" if file_path.suffix.lower() == ".pdf" else input_type,
                styles=styles,
                combined_prompt=combined_prompt,
            )
        except Exception as e:
            self.log.error(f"Error processing file {file_path}: {e}")
            raise

    def summarize_directory(
        self,
        dir_path: Path,
        styles: Optional[List[str]] = None,
        combined_prompt: Optional[bool] = None,
        input_type: str = "auto",
    ) -> List[Dict[str, Any]]:
        self.log.info(f"Summarizing directory: {dir_path}")
        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {dir_path}")
        if not dir_path.is_dir():
            raise ValueError(f"Path is not a directory: {dir_path}")
        results = []
        errors = []
        text_extensions = [".txt", ".md", ".log", ".pdf", ".email", ".transcript"]
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
                result = self.summarize_file(
                    file_path,
                    styles=styles,
                    combined_prompt=combined_prompt,
                    input_type=input_type,
                )
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
