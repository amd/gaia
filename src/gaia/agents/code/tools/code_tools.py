# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Consolidated code tools mixin combining generation, analysis, and helper methods."""

import json
import logging
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CodeToolsMixin:
    """Consolidated mixin providing code generation, analysis, and helper methods.

    This mixin provides tools for:
    - Generating Python functions, classes, and tests
    - Parsing and analyzing Python code structure
    - Validating Python syntax
    - Extracting code symbols (functions, classes, imports)
    - Running pylint analysis on code

    Tools provided:
    - generate_function: Generate Python function with docstring and body
    - generate_class: Generate Python class with methods
    - generate_test: Generate comprehensive unit tests for code
    - parse_python_code: Parse and extract structure from Python code
    - validate_syntax: Validate Python code syntax
    - list_symbols: List symbols (functions, classes) in Python code
    - analyze_with_pylint: Run pylint analysis on Python code or files
    """

    def register_code_tools(self) -> None:
        """Register all code-related tools."""
        from gaia.agents.base.tools import tool

        # ============================================================
        # CODE GENERATION TOOLS
        # ============================================================

        @tool
        def generate_function(
            name: str,
            params: str = "",
            docstring: str = "Function description.",
            body: str = "pass",
            return_type: str = None,
            write_to_file: bool = True,
        ) -> Dict[str, Any]:
            """Generate a Python function and optionally save to file.

            Args:
                name: Function name
                params: Parameter list (e.g., "x, y=0")
                docstring: Function documentation
                body: Function implementation
                return_type: Optional return type hint
                write_to_file: Whether to save to a file (default: True)

            Returns:
                Dictionary with file path or generated code
            """
            try:
                # Add return type hint if provided
                signature = f"def {name}({params})"
                if return_type:
                    signature += f" -> {return_type}"
                signature += ":"

                # Format the body with proper indentation
                body_lines = body.split("\n")
                needs_indent = body_lines and not body_lines[0].startswith(" ")

                if needs_indent:
                    indented_lines = []
                    for line in body_lines:
                        if line.strip():
                            indented_lines.append(f"    {line}")
                        else:
                            indented_lines.append("")
                    indented_body = "\n".join(indented_lines)
                else:
                    indented_body = body

                code = f'''{signature}
    """{docstring}
    """
{indented_body}
'''

                # Validate the generated code
                validation = self._validate_python_syntax(code)

                result = {
                    "status": "success",
                    "is_valid": validation["is_valid"],
                    "errors": validation.get("errors", []),
                    "code": code,
                }

                if write_to_file and validation["is_valid"]:
                    filename = f"{name}_generated.py"
                    filepath = os.path.abspath(filename)

                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(code)

                    result["file_path"] = filepath
                    result["message"] = f"Function '{name}' written to {filepath}"

                return result
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def generate_class(
            name: str,
            docstring: str = "Class description.",
            base_classes: str = "",
            methods: List[Dict[str, str]] = None,
            write_to_file: bool = True,
        ) -> Dict[str, Any]:
            """Generate a Python class and optionally save to file.

            Args:
                name: Class name
                docstring: Class documentation
                base_classes: Optional base classes (e.g., "BaseClass, Mixin")
                methods: List of methods to include
                write_to_file: Whether to save to a file (default: True)

            Returns:
                Dictionary with generated class code
            """
            try:
                # Build class signature
                if base_classes:
                    signature = f"class {name}({base_classes}):"
                else:
                    signature = f"class {name}:"

                code = f'''{signature}
    """{docstring}
    """
'''

                if not methods:
                    code += '''
    def __init__(self):
        """Initialize the class."""
        pass
'''
                else:
                    for method in methods:
                        method_name = method.get("name", "method")
                        method_params = method.get("params", "self")
                        method_doc = method.get("docstring", "Method description.")
                        method_body = method.get("body", "pass")

                        if not method_params.startswith("self"):
                            method_params = (
                                f"self, {method_params}" if method_params else "self"
                            )

                        body_lines = []
                        for line in method_body.split("\n"):
                            if line.strip():
                                body_lines.append(f"        {line}")
                            else:
                                body_lines.append("")
                        indented_body = "\n".join(body_lines)

                        code += f'''
    def {method_name}({method_params}):
        """{method_doc}
        """
{indented_body}
'''

                validation = self._validate_python_syntax(code)

                result = {
                    "status": "success",
                    "is_valid": validation["is_valid"],
                    "errors": validation.get("errors", []),
                    "code": code,
                }

                if write_to_file and validation["is_valid"]:
                    filename = f"{name.lower()}_generated.py"
                    filepath = os.path.abspath(filename)

                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(code)

                    result["file_path"] = filepath
                    result["message"] = f"Class '{name}' written to {filepath}"

                return result
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def generate_test(
            class_name: str = None,
            module_name: str = None,
            function_name: str = None,
            source_code: str = None,
            test_cases: List[str] = None,
            source_file: str = None,
            write_to_file: bool = True,
        ) -> Dict[str, Any]:
            """Generate comprehensive Python unit tests and optionally save to file.

            Args:
                class_name: Name for the test class (optional)
                module_name: Module being tested (optional)
                function_name: Specific function to test (optional)
                source_code: Source code to analyze for test generation (optional)
                test_cases: Manual list of test case names (optional)
                source_file: Path to source file being tested (optional)
                write_to_file: Whether to save to a file (default: True)

            Returns:
                Dictionary with file path or generated test code
            """
            try:
                functions_to_test = []
                if source_code:
                    parsed = self._parse_python_code(source_code)
                    functions_to_test = [
                        s for s in parsed.symbols if s.type == "function"
                    ]

                    if not module_name and functions_to_test:
                        module_name = "tested_module"

                    if not class_name:
                        if function_name:
                            class_name = f"{function_name.title()}"
                        elif functions_to_test:
                            class_name = "GeneratedTests"
                        else:
                            class_name = "Tests"

                class_name = class_name or "Tests"
                module_name = module_name or "module"

                code = f'''import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class Test{class_name}(unittest.TestCase):
    """Test cases for {module_name}."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_data = {{}}

    def tearDown(self):
        """Clean up after tests."""
        pass
'''

                if function_name and functions_to_test:
                    func = next(
                        (f for f in functions_to_test if f.name == function_name), None
                    )
                    if func:
                        code += f'''
    def test_{func.name}_basic(self):
        """Test basic functionality of {func.name}."""
        # Test with typical inputs
        # TODO: Add assertions based on function behavior
        pass

    def test_{func.name}_edge_cases(self):
        """Test edge cases for {func.name}."""
        # Test boundary conditions
        # TODO: Add edge case tests
        pass

    def test_{func.name}_invalid_input(self):
        """Test {func.name} with invalid inputs."""
        # Test error handling
        # TODO: Add error handling tests
        pass
'''
                elif functions_to_test:
                    for func in functions_to_test[:5]:
                        code += f'''
    def test_{func.name}(self):
        """Test {func.name} function."""
        # TODO: Implement test for {func.name}
        # Function signature: {func.signature if func.signature else func.name + '()'}
        pass
'''
                elif test_cases:
                    for test_name in test_cases:
                        code += f'''
    def test_{test_name}(self):
        """Test {test_name}."""
        # TODO: Implement test
        self.fail("Not implemented")
'''
                else:
                    code += '''
    def test_basic_functionality(self):
        """Test basic functionality."""
        # TODO: Implement basic test
        self.assertTrue(True, "Basic test placeholder")

    def test_edge_cases(self):
        """Test edge cases."""
        # TODO: Implement edge case tests
        self.assertTrue(True, "Edge case test placeholder")

    def test_error_handling(self):
        """Test error handling."""
        # TODO: Implement error handling tests
        with self.assertRaises(Exception):
            # Code that should raise exception
            pass
'''

                code += """
if __name__ == "__main__":
    unittest.main(verbosity=2)
"""

                validation = self._validate_python_syntax(code)

                result = {
                    "status": "success",
                    "is_valid": validation["is_valid"],
                    "errors": validation.get("errors", []),
                    "code": code,  # Always include generated code
                    "functions_tested": (
                        [f.name for f in functions_to_test] if functions_to_test else []
                    ),
                    "test_class": f"Test{class_name}",
                }

                if write_to_file and validation["is_valid"]:
                    if source_file:
                        base_name = os.path.splitext(os.path.basename(source_file))[0]
                        filename = f"test_{base_name}.py"
                    elif module_name:
                        filename = f"test_{module_name.lower()}.py"
                    elif function_name:
                        filename = f"test_{function_name.lower()}.py"
                    else:
                        filename = f"test_{class_name.lower() if class_name else 'generated'}.py"

                    filepath = os.path.abspath(filename)

                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(code)

                    result["file_path"] = filepath
                    result["message"] = f"Test file written to {filepath}"

                return result
            except Exception as e:
                return {"status": "error", "error": str(e)}

        @tool
        def list_symbols(code: str, symbol_type: str = None) -> Dict[str, Any]:
            """List symbols (functions, classes, variables) in Python code.

            Args:
                code: Python source code
                symbol_type: Optional filter ('function', 'class', 'variable', 'import')

            Returns:
                Dictionary with list of symbols
            """
            parsed = self._parse_python_code(code)

            symbols = parsed.symbols or []
            if symbol_type:
                symbols = [s for s in symbols if s.type == symbol_type]

            return {
                "status": "success",
                "total_symbols": len(symbols),
                "symbols": [
                    {
                        "name": s.name,
                        "type": s.type,
                        "line": s.line,
                        "signature": s.signature,
                        "docstring": s.docstring,
                    }
                    for s in symbols
                ],
            }

        # ============================================================
        # CODE ANALYSIS TOOLS
        # ============================================================

        @tool
        def parse_python_code(code: str) -> Dict[str, Any]:
            """Parse Python code and extract structure.

            Args:
                code: Python source code

            Returns:
                Dictionary with parsed code information
            """
            parsed = self._parse_python_code(code)
            return {
                "status": "success",
                "is_valid": parsed.is_valid,
                "symbols": (
                    [
                        {
                            "name": s.name,
                            "type": s.type,
                            "line": s.line,
                            "signature": s.signature,
                            "docstring": s.docstring,
                        }
                        for s in parsed.symbols
                    ]
                    if parsed.symbols
                    else []
                ),
                "imports": parsed.imports or [],
                "errors": parsed.errors or [],
            }

        @tool
        def validate_syntax(code: str) -> Dict[str, Any]:
            """Validate Python code syntax.

            Args:
                code: Python code to validate

            Returns:
                Dictionary with validation result
            """
            return self._validate_python_syntax(code)

        @tool
        def analyze_with_pylint(
            file_path: str = None, code: str = None, confidence: str = "HIGH"
        ) -> Dict[str, Any]:
            """Analyze Python code with pylint.

            Args:
                file_path: Path to Python file to analyze (optional)
                code: Python code string to analyze (optional)
                confidence: Minimum confidence level

            Returns:
                Dictionary with pylint analysis results
            """
            try:
                if file_path:
                    path = Path(file_path)
                    if not path.exists():
                        return {
                            "status": "error",
                            "error": f"Path not found: {file_path}",
                        }
                    target_file = str(path)

                    if path.is_dir():
                        import glob

                        py_files = glob.glob(str(path / "**/*.py"), recursive=True)
                        if not py_files:
                            return {
                                "status": "error",
                                "error": f"No Python files found in directory: {file_path}",
                            }
                        target_file = str(path)
                elif code:
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".py", delete=False
                    ) as f:
                        f.write(code)
                        target_file = f.name
                else:
                    return {
                        "status": "error",
                        "error": "Either file_path or code must be provided",
                    }

                cmd = [
                    "python",
                    "-m",
                    "pylint",
                    "--output-format=json",
                    f"--confidence={confidence}",
                    "--disable=import-error,no-member",
                    target_file,
                ]

                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30, check=False
                )

                if result.returncode == 2 and "error: argument" in result.stderr:
                    return {
                        "status": "error",
                        "error": f"Pylint command failed: {result.stderr.strip()}",
                        "command": " ".join(shlex.quote(str(c)) for c in cmd),
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "return_code": result.returncode,
                    }

                issues = []
                if result.stdout:
                    try:
                        issues = json.loads(result.stdout)
                    except json.JSONDecodeError:
                        if result.stderr:
                            logger.warning(f"Pylint stderr: {result.stderr}")
                        if not result.stdout.strip():
                            return {
                                "status": "error",
                                "error": f"Pylint produced no output. stderr: {result.stderr}",
                                "command": " ".join(shlex.quote(str(c)) for c in cmd),
                            }

                if code and os.path.exists(target_file):
                    os.unlink(target_file)

                has_issues = len(issues) > 0

                errors = [i for i in issues if i.get("type") == "error"]
                warnings = [i for i in issues if i.get("type") == "warning"]
                conventions = [i for i in issues if i.get("type") == "convention"]
                refactors = [i for i in issues if i.get("type") == "refactor"]

                return {
                    "status": "success",
                    "total_issues": len(issues),
                    "errors": len(errors),
                    "warnings": len(warnings),
                    "conventions": len(conventions),
                    "refactors": len(refactors),
                    "issues": issues[:50],
                    "clean": not has_issues,
                    "command": " ".join(shlex.quote(str(c)) for c in cmd),
                    "stdout": result.stdout,
                    "stderr": result.stderr if result.stderr else None,
                    "return_code": result.returncode,
                }

            except subprocess.TimeoutExpired:
                return {"status": "error", "error": "Pylint analysis timed out"}
            except subprocess.CalledProcessError as e:
                return {"status": "error", "error": f"Pylint failed: {e.stderr}"}
            except ImportError:
                return {
                    "status": "error",
                    "error": "pylint is not installed. Install with: pip install pylint",
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

    # ============================================================
    # HELPER METHODS (non-tool methods for internal use)
    # ============================================================

    def _generate_code_for_file(
        self, filename: str, purpose: str, context: str = ""
    ) -> str:
        """Generate code for a specific file using LLM with streaming preview.

        Args:
            filename: Name of the file to generate
            purpose: Description of what this file should do
            context: Additional context about the project

        Returns:
            Generated code as string
        """
        prompt = f"""Generate complete, production-ready Python code for: {filename}

Purpose: {purpose}

{context}

Requirements:
1. Include proper copyright header
2. Add comprehensive docstrings
3. Use type hints
4. Follow best practices
5. Include error handling
6. Make it complete and runnable

Generate ONLY the code, no explanations."""

        try:
            # Use streaming for live preview
            self.console.start_file_preview(filename, max_lines=15)

            code_chunks = []
            max_retries = 2
            retry_count = 0

            while retry_count <= max_retries:
                try:
                    max_tokens = 4096 if retry_count == 0 else 2048

                    for chunk in self.chat.send_stream(prompt, max_tokens=max_tokens):
                        if chunk.is_complete:
                            continue
                        chunk_text = chunk.text
                        code_chunks.append(chunk_text)
                        self.console.update_file_preview(chunk_text)

                    break  # Success

                except Exception as e:
                    retry_count += 1
                    if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                        if retry_count <= max_retries:
                            self.console.print_warning(
                                f"⚠️ Generation timeout for {filename}, retrying ({retry_count}/{max_retries})..."
                            )
                            if retry_count == 2:
                                prompt = f"Generate minimal working code for {filename}. Purpose: {purpose}"
                        else:
                            self.console.print_error(
                                f"❌ Generation failed after {max_retries} retries for {filename}"
                            )
                            code_chunks = [
                                self._get_timeout_placeholder(filename, purpose)
                            ]
                            break
                    else:
                        raise

            self.console.stop_file_preview()

            code = "".join(code_chunks).strip()

            # Extract code from markdown blocks if present
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0].strip()
            elif "```" in code:
                code = code.split("```")[1].split("```")[0].strip()

            return code

        except Exception as e:
            logger.error(f"Failed to generate code for {filename}: {e}")
            return self._get_timeout_placeholder(filename, purpose)

    def _fix_code_with_llm(
        self,
        code: str,
        file_path: str,
        error_msg: str,
        context: str = "",
        max_attempts: int = 3,
    ) -> Optional[str]:
        """Fix code using LLM based on error message.

        Args:
            code: Code with errors
            file_path: Path to the file (used to detect language)
            error_msg: Error message from linting/execution
            context: Additional context
            max_attempts: Maximum number of fix attempts

        Returns:
            Fixed code or None if unable to fix
        """
        # Detect language from file extension
        is_typescript = file_path.endswith((".ts", ".tsx"))
        is_python = file_path.endswith(".py")
        is_css = file_path.endswith(".css")
        lang = (
            "typescript"
            if is_typescript
            else "python" if is_python else "css" if is_css else "unknown"
        )
        lang_label = (
            "TypeScript"
            if is_typescript
            else "Python" if is_python else "CSS" if is_css else "unknown"
        )

        for attempt in range(max_attempts):
            prompt = f"""Fix the following {lang_label} code error:

File path: {file_path}
Error: {error_msg}

Code:
```{lang}
{code}
```

{context}

Return ONLY the corrected code, no explanations."""

            try:
                response = self.chat.send(prompt, timeout=600)
                fixed_code = response.text.strip()

                # Extract code from markdown blocks
                if f"```{lang}" in fixed_code:
                    fixed_code = (
                        fixed_code.split(f"```{lang}")[1].split("```")[0].strip()
                    )
                elif "```" in fixed_code:
                    fixed_code = fixed_code.split("```")[1].split("```")[0].strip()

                # Validate the fix (only for Python - TypeScript validated later)
                if is_python:
                    validation = self.syntax_validator.validate_dict(fixed_code)
                    if validation["is_valid"]:
                        return fixed_code
                else:
                    # For TypeScript, return the fix and let tsc validate
                    if fixed_code and fixed_code != code:
                        return fixed_code

            except Exception as e:
                logger.warning(f"Fix attempt {attempt + 1} failed: {e}")

        return None

    def _get_timeout_placeholder(self, filename: str, purpose: str) -> str:
        """Generate placeholder code when LLM times out.

        Args:
            filename: Name of the file
            purpose: Purpose of the file

        Returns:
            Placeholder code
        """
        return f'''# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
{filename}

{purpose}

TODO: Implementation needed - LLM generation timed out.
"""

def main():
    """Main entry point."""
    print("TODO: Implement {filename}")

if __name__ == "__main__":
    main()
'''
