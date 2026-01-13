#!/usr/bin/env python3
#
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Simple test bench for the fix_code prompt against a local or Claude model.

Usage:
    python fix_code_testbench.py path/to/file.ts "Error message" output.ts \
        [--model local-model] [--use-claude] [--context "..."]
"""

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from openai import OpenAI

DEFAULT_ENDPOINT = "http://localhost:8000/api/v1"
DEFAULT_LOCAL_MODEL = "Qwen3-Coder-30B-A3B-Instruct-GGUF"
CLAUDE_MODEL_NAME = "claude-sonnet-4-5-20250929"

PROMPT_ENGINEERING_GUIDANCE = """
Example remediation pattern:
- For TypeScript assignability errors like "Type 'string' is not assignable to type 'never'", use a double-cast or key cast to align types. For instance, change `target[field] = parsedValue.toISOString();` into `target[field as keyof typeof target] = parsedValue.toISOString() as unknown as never;`. Apply the minimal change necessary to address the reported line.
"""


class FixCodeTestbench:
    """Encapsulates prompt construction, model execution, and response handling."""

    def __init__(
        self,
        *,
        model: str,
        use_claude: bool,
        use_prompt_engineering: bool,
        use_edit_file: bool,
        temperature: float,
        timeout: int,
        context: Optional[str],
        language_override: Optional[str],
    ) -> None:
        self.model = model
        self.use_claude = use_claude
        self.use_prompt_engineering = use_prompt_engineering
        self.use_edit_file = use_edit_file
        self.temperature = temperature
        self.timeout = timeout
        self.context = context
        self.language_override = language_override

    def run(
        self,
        *,
        source_path: Path,
        error_description: str,
        output_path: Path,
        start_line: int,
        end_line: Optional[int],
    ) -> None:
        if not source_path.exists():
            print(f"File not found: {source_path}", file=sys.stderr)
            sys.exit(1)

        full_content = source_path.read_text(encoding="utf-8")
        all_lines = full_content.splitlines()

        start_line = max(start_line, 1)
        resolved_end_line = end_line if end_line is not None else len(all_lines)
        resolved_end_line = min(resolved_end_line, len(all_lines))
        if start_line > resolved_end_line:
            print(
                f"Invalid line range: start ({start_line}) must be <= end ({resolved_end_line}).",
                file=sys.stderr,
            )
            sys.exit(1)

        snippet = "\n".join(all_lines[start_line - 1 : resolved_end_line])
        language, language_label = self.detect_language(str(source_path))

        if self.use_edit_file:
            prompt = self.build_edit_prompt(
                file_path=os.fspath(source_path),
                error_description=error_description,
                snippet=snippet,
                start_line=start_line,
                end_line=resolved_end_line,
                language_label=language_label,
            )
        else:
            prompt = self.build_prompt(
                file_path=os.fspath(source_path),
                error_description=error_description,
                code=snippet,
                language=language,
                language_label=language_label,
            )

        print("=== Prompt ===")
        print(prompt)

        raw_response = self.call_model(prompt)

        print("=== RAW RESPONSE ===")
        print(raw_response)
        if self.use_edit_file:
            self._handle_edit_mode(
                raw_response=raw_response,
                snippet=snippet,
                all_lines=all_lines,
                start_line=start_line,
                end_line=resolved_end_line,
                full_content=full_content,
                source_path=source_path,
                output_path=output_path,
            )
        else:
            self._handle_replace_mode(
                raw_response=raw_response,
                language=language,
                code=snippet,
                start_line=start_line,
                end_line=resolved_end_line,
                source_path=source_path,
                output_path=output_path,
            )

    def _handle_edit_mode(
        self,
        *,
        raw_response: str,
        snippet: str,
        all_lines: list[str],
        start_line: int,
        end_line: int,
        full_content: str,
        source_path: Path,
        output_path: Path,
    ) -> None:
        try:
            old_content, new_content = self.extract_edit_replacement(raw_response)
        except ValueError as exc:
            print(f"Failed to parse edit_file response: {exc}", file=sys.stderr)
            sys.exit(3)

        if old_content not in snippet:
            print("old_content was not found in the selected snippet.", file=sys.stderr)
            sys.exit(4)

        new_snippet = snippet.replace(old_content, new_content, 1)
        new_snippet_lines = new_snippet.splitlines()
        new_lines = (
            all_lines[: start_line - 1] + new_snippet_lines + all_lines[end_line:]
        )
        cleaned_full = "\n".join(new_lines)
        if full_content.endswith("\n") and not cleaned_full.endswith("\n"):
            cleaned_full += "\n"

        diff = difflib.unified_diff(
            full_content.splitlines(),
            cleaned_full.splitlines(),
            fromfile=str(source_path),
            tofile=str(output_path),
            lineterm="",
        )
        print("=== Diff (full file) ===")
        print("\n".join(diff))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(cleaned_full, encoding="utf-8")

    def _handle_replace_mode(
        self,
        *,
        raw_response: str,
        language: str,
        code: str,
        start_line: int,
        end_line: int,
        source_path: Path,
        output_path: Path,
    ) -> None:
        cleaned = self.clean_response(raw_response, language)

        print("=== Diff (cleaned vs original) ===")
        original_snippet_lines = code.splitlines()
        cleaned_snippet_lines = cleaned.splitlines()
        diff = difflib.unified_diff(
            original_snippet_lines,
            cleaned_snippet_lines,
            fromfile=f"{source_path}:{start_line}-{end_line}",
            tofile=f"{output_path}:{start_line}-{end_line}",
            lineterm="",
        )
        print("\n".join(diff))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(cleaned, encoding="utf-8")

    def get_extra_guidance(self) -> str:
        """Return the additional instructions used by prompt engineering."""
        return PROMPT_ENGINEERING_GUIDANCE if self.use_prompt_engineering else ""

    def detect_language(self, file_path: str) -> tuple[str, str]:
        """Infer the language + pretty label used in prompts."""
        if self.language_override:
            lang = self.language_override.lower()
            label = "TypeScript" if "ts" in lang else lang.capitalize()
            return lang, label

        lower = file_path.lower()
        if lower.endswith((".ts", ".tsx")):
            return "typescript", "TypeScript"
        if lower.endswith(".py"):
            return "python", "Python"
        return "typescript", "TypeScript"

    def build_prompt(
        self,
        *,
        file_path: str,
        error_description: str,
        code: str,
        language: str,
        language_label: str,
    ) -> str:
        """Replicate the fix_code prompt inside the agent."""
        context_text = self.context.strip() if self.context else ""
        extra_guidance = self.get_extra_guidance()
        return f"""Fix the following {language_label} code error:

File path: {file_path}
Error: {error_description}

Code:
```{language}
{code}
```

{context_text}

{extra_guidance}

Return ONLY the corrected code, no explanations."""

    def build_edit_prompt(
        self,
        *,
        file_path: str,
        error_description: str,
        snippet: str,
        start_line: int,
        end_line: int,
        language_label: str,
    ) -> str:
        """Prompt instructing the model to output an edit_file tool call."""
        context_text = self.context.strip() if self.context else ""
        extra_guidance = self.get_extra_guidance()
        return f"""You are fixing a {language_label} file. Provide a single JSON tool call that replaces the problematic code.

File path: {file_path}
Error: {error_description}
Snippet lines: {start_line}-{end_line}

Current snippet:
```
{snippet}
```

{context_text}

{extra_guidance}

Respond with ONLY JSON (no prose, no code fences) in the following shape:
{{
  "tool": "edit_file",
  "tool_args": {{
    "file_path": "{file_path}",
    "old_content": "<exact text to replace>",
    "new_content": "<replacement text>"
  }}
}}

Requirements:
- Copy old_content exactly as it currently appears in the file (including whitespace).
- Provide the corrected replacement in new_content.
- Apply the minimal change necessary to resolve the error."""

    def call_model(self, prompt: str) -> str:
        """Dispatch the prompt to the requested model backend."""
        if self.use_claude:
            return self._call_claude(prompt)
        return self._call_local_model(prompt)

    def _call_local_model(self, prompt: str) -> str:
        """Send prompt to the local OpenAI-compatible endpoint."""
        client = OpenAI(
            base_url=DEFAULT_ENDPOINT, api_key="not-needed", timeout=self.timeout
        )
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
        )
        return response.choices[0].message.content.strip()

    def _call_claude(self, prompt: str) -> str:
        """Send prompt to Claude Sonnet 4.5."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print(
                "ANTHROPIC_API_KEY environment variable is required for Claude.",
                file=sys.stderr,
            )
            sys.exit(1)

        import anthropic

        client = anthropic.Anthropic(
            api_key=api_key, max_retries=3, timeout=self.timeout
        )
        response = client.messages.create(
            model=CLAUDE_MODEL_NAME,
            max_tokens=4096,
            temperature=self.temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    def clean_response(self, raw: str, language: str) -> str:
        """Strip fenced code blocks if the model wraps the answer."""
        fenced = f"```{language}"
        if fenced in raw:
            return raw.split(fenced, 1)[1].split("```", 1)[0].strip()
        if "```" in raw:
            return raw.split("```", 1)[1].split("```", 1)[0].strip()
        return raw

    @staticmethod
    def _escape_json_strings(text: str) -> str:
        """Replace literal newlines inside JSON string literals with escape sequences."""
        result: list[str] = []
        in_string = False
        escaped = False
        for ch in text:
            if in_string:
                if escaped:
                    result.append(ch)
                    escaped = False
                    continue
                if ch == "\\":
                    result.append("\\")
                    escaped = True
                    continue
                if ch == '"':
                    result.append(ch)
                    in_string = False
                    continue
                if ch == "\n":
                    result.append("\\n")
                    continue
                if ch == "\r":
                    result.append("\\r")
                    continue
                result.append(ch)
            else:
                if ch == '"':
                    result.append(ch)
                    in_string = True
                else:
                    result.append(ch)
        return "".join(result)

    @staticmethod
    def _encode_loose_json_fields(text: str) -> str:
        """JSON-encode multiline old/new content blocks produced without escapes."""
        for field in ("old_content", "new_content"):
            text = FixCodeTestbench._encode_field_value(text, field)
        return text

    @staticmethod
    def _encode_field_value(text: str, field: str) -> str:
        pattern = re.compile(
            r'("'
            + re.escape(field)
            + r'"\s*:\s*")(?P<value>.*?)"(?P<suffix>\s*(?:,\s*"[^"\\]+?"|\}))',
            re.DOTALL,
        )
        match = pattern.search(text)
        if not match:
            return text
        encoded_value = json.dumps(match.group("value"))[1:-1]
        start, end = match.span()
        replacement = f'{match.group(1)}{encoded_value}"{match.group("suffix")}'
        return text[:start] + replacement + text[end:]

    def extract_edit_replacement(self, response_text: str) -> tuple[str, str]:
        """Parse the JSON tool call and return old/new content."""
        text = response_text.strip()
        if "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()

        candidates = []
        seen: set[str] = set()

        def _add_candidate(candidate: str) -> None:
            if candidate not in seen:
                candidates.append(candidate)
                seen.add(candidate)

        _add_candidate(text)
        _add_candidate(self._escape_json_strings(text))
        loose = self._encode_loose_json_fields(text)
        _add_candidate(loose)
        _add_candidate(self._escape_json_strings(loose))

        last_exc: Optional[json.JSONDecodeError] = None
        data = None
        for candidate in candidates:
            try:
                data = json.loads(candidate)
                break
            except json.JSONDecodeError as exc:
                last_exc = exc

        if data is None:
            raise ValueError(f"Response was not valid JSON: {last_exc}") from last_exc

        if data.get("tool") != "edit_file":
            raise ValueError("Response missing tool='edit_file'.")
        tool_args = data.get("tool_args") or {}
        old_content = tool_args.get("old_content")
        new_content = tool_args.get("new_content")
        if old_content is None or new_content is None:
            raise ValueError("Response missing old_content/new_content fields.")
        return old_content, new_content


def main() -> None:
    parser = argparse.ArgumentParser(description="Experiment with fix_code prompts.")
    parser.add_argument("file", help="Path to the source file to repair")
    parser.add_argument("error", help="Error description fed into the prompt")
    parser.add_argument("output_file", help="Where to write the patched file")
    parser.add_argument(
        "--model",
        default=DEFAULT_LOCAL_MODEL,
        help="Local model identifier (ignored when --use-claude)",
    )
    parser.add_argument(
        "--use-claude",
        action="store_true",
        help="Send the prompt to Claude Sonnet 4.5 instead of the local endpoint.",
    )
    parser.add_argument(
        "--language",
        help="Override language (python/typescript/etc.). Auto-detected otherwise.",
    )
    parser.add_argument(
        "--context",
        help="Optional additional context appended to the prompt (e.g., validation logs).",
    )
    parser.add_argument(
        "--use-prompt-engineering",
        action="store_true",
        help="Injects additional targeted guidance into the prompt.",
    )
    parser.add_argument(
        "--use-edit-file",
        action="store_true",
        help="Ask the model to emit an edit_file tool call over the full file.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Sampling temperature (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="HTTP timeout for the completion request (default: %(default)s)",
    )
    parser.add_argument(
        "--start-line",
        type=int,
        default=1,
        help="First line of the file to include in the prompt (default: 1).",
    )
    parser.add_argument(
        "--end-line",
        type=int,
        default=None,
        help="Last line of the file to include in the prompt (default: end of file).",
    )
    args = parser.parse_args()

    bench = FixCodeTestbench(
        model=args.model,
        use_claude=args.use_claude,
        use_prompt_engineering=args.use_prompt_engineering,
        use_edit_file=args.use_edit_file,
        temperature=args.temperature,
        timeout=args.timeout,
        context=args.context,
        language_override=args.language,
    )
    bench.run(
        source_path=Path(args.file),
        error_description=args.error,
        output_path=Path(args.output_file),
        start_line=args.start_line,
        end_line=args.end_line,
    )


if __name__ == "__main__":
    main()
