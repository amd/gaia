#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Gaia Summarizer Application - Thin wrapper that delegates to SummarizerAgent
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from gaia.agents.summarize.agent import SummarizerAgent
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.logger import get_logger


# Utility functions for email validation (used by CLI and other tools)
def validate_email_address(email: str) -> bool:
    """Validate email address format"""
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(email_pattern, email.strip()) is not None


def validate_email_list(email_list: str) -> list[str]:
    """Validate and parse comma-separated email list"""
    if not email_list:
        return []
    emails = [e.strip() for e in email_list.split(",") if e.strip()]
    invalid_emails = [e for e in emails if not validate_email_address(e)]
    if invalid_emails:
        raise ValueError(f"Invalid email address(es): {', '.join(invalid_emails)}")
    return emails


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

        # Auto-detect OpenAI models (gpt-*) to use ChatGPT
        if self.model and self.model.lower().startswith("gpt"):
            self.use_chatgpt = True


class SummarizerApp:
    """Main application class for summarization (delegates to SummarizerAgent)"""

    def __init__(self, config: Optional[SummaryConfig] = None):
        self.config = config or SummaryConfig()
        self.log = get_logger(__name__)
        self.agent = SummarizerAgent(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            styles=self.config.styles,
            combined_prompt=self.config.combined_prompt,
            use_claude=self.config.use_claude,
            use_chatgpt=self.config.use_chatgpt,
        )

    def summarize_file(
        self,
        file_path: Path,
        styles: Optional[List[str]] = None,
        combined_prompt: Optional[bool] = None,
        input_type: str = "auto",
    ) -> Dict[str, Any]:
        # Always convert file_path to Path object if it's a string
        if not isinstance(file_path, Path):
            file_path = Path(file_path)
        return self.agent.summarize_file(
            file_path,
            styles=styles,
            combined_prompt=combined_prompt,
            input_type=input_type,
        )

    def summarize_directory(
        self,
        dir_path: Path,
        styles: Optional[List[str]] = None,
        combined_prompt: Optional[bool] = None,
        input_type: str = "auto",
    ) -> List[Dict[str, Any]]:
        return self.agent.summarize_directory(
            dir_path,
            styles=styles,
            combined_prompt=combined_prompt,
            input_type=input_type,
        )

    def summarize(
        self,
        content: str,
        styles: Optional[List[str]] = None,
        combined_prompt: Optional[bool] = None,
        input_type: str = "auto",
    ) -> Dict[str, Any]:
        return self.agent.summarize(
            content,
            styles=styles,
            combined_prompt=combined_prompt,
            input_type=input_type,
        )
