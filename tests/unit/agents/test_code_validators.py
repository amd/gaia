# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for code validators — syntax, antipattern, AST, and requirements."""

import ast
import textwrap
from pathlib import Path

import pytest

from gaia.agents.code.validators.antipattern_checker import AntipatternChecker
from gaia.agents.code.validators.ast_analyzer import ASTAnalyzer
from gaia.agents.code.validators.requirements_validator import RequirementsValidator
from gaia.agents.code.validators.syntax_validator import SyntaxValidator

# ===================================================================
# SyntaxValidator
# ===================================================================


class TestSyntaxValidator:
    """SyntaxValidator.validate / validate_dict / helpers."""

    @pytest.fixture()
    def validator(self):
        return SyntaxValidator()

    # -- validate --

    def test_valid_code(self, validator):
        result = validator.validate("x = 1\nprint(x)\n")
        assert result.is_valid is True
        assert result.errors == []

    def test_empty_code(self, validator):
        result = validator.validate("")
        assert result.is_valid is True

    def test_syntax_error_detected(self, validator):
        result = validator.validate("def foo(\n")
        assert result.is_valid is False
        assert len(result.errors) > 0

    def test_syntax_error_includes_line_number(self, validator):
        result = validator.validate("x = 1\ndef foo(\n")
        assert any("Line" in e for e in result.errors)

    # -- validate_dict --

    def test_validate_dict_valid(self, validator):
        d = validator.validate_dict("a = 1")
        assert d["status"] == "success"
        assert d["is_valid"] is True
        assert d["message"] == "Syntax is valid"

    def test_validate_dict_invalid(self, validator):
        d = validator.validate_dict("def (")
        assert d["status"] == "error"
        assert d["is_valid"] is False
        assert len(d["errors"]) > 0

    # -- get_syntax_errors --

    def test_get_syntax_errors_none(self, validator):
        assert validator.get_syntax_errors("x = 1") == []

    def test_get_syntax_errors_returns_syntax_error(self, validator):
        errors = validator.get_syntax_errors("def (")
        assert len(errors) == 1
        assert isinstance(errors[0], SyntaxError)

    # -- check_indentation --

    def test_indentation_clean(self, validator):
        code = "def f():\n    pass\n"
        assert validator.check_indentation(code) == []

    def test_indentation_mixed_tabs_spaces(self, validator):
        code = "def f():\n \tpass\n"
        warnings = validator.check_indentation(code)
        assert any("Mixed tabs and spaces" in w for w in warnings)

    def test_indentation_non_standard(self, validator):
        code = "def f():\n   pass\n"
        warnings = validator.check_indentation(code)
        assert any("Non-standard indentation" in w for w in warnings)

    # -- validate_imports --

    def test_validate_imports_clean(self, validator):
        code = "import os\nimport sys\n"
        assert validator.validate_imports(code) == []

    def test_validate_imports_wildcard(self, validator):
        code = "from os import *\n"
        warnings = validator.validate_imports(code)
        assert any("Wildcard import" in w for w in warnings)

    def test_validate_imports_duplicate(self, validator):
        code = "import os\nimport os\n"
        warnings = validator.validate_imports(code)
        assert any("Duplicate import" in w for w in warnings)

    # -- check_line_length --

    def test_line_length_ok(self, validator):
        assert validator.check_line_length("x = 1") == []

    def test_line_length_exceeded(self, validator):
        long_line = "x = " + "a" * 90
        warnings = validator.check_line_length(long_line, max_length=88)
        assert len(warnings) == 1
        assert "Line too long" in warnings[0]


# ===================================================================
# AntipatternChecker
# ===================================================================


class TestAntipatternChecker:
    """AntipatternChecker.check / check_dict / naming / complexity."""

    @pytest.fixture()
    def checker(self):
        return AntipatternChecker()

    # -- check --

    def test_clean_code(self, checker):
        code = textwrap.dedent("""\
            def greet(name):
                print(f"hi {name}")
        """)
        result = checker.check(Path("clean.py"), code)
        assert result["errors"] == []
        assert result["warnings"] == []

    def test_excessive_function_name(self, checker):
        name = "a" * 81
        code = f"def {name}():\n    pass\n"
        result = checker.check(Path("long.py"), code)
        assert any("chars" in e for e in result["errors"])

    def test_combinatorial_naming(self, checker):
        code = "def get_and_process_and_validate_and_transform():\n    pass\n"
        result = checker.check(Path("combo.py"), code)
        assert any("Combinatorial" in e for e in result["errors"])

    def test_excessive_parameters(self, checker):
        params = ", ".join(f"p{i}" for i in range(8))
        code = f"def func({params}):\n    pass\n"
        result = checker.check(Path("params.py"), code)
        assert any("parameters" in w for w in result["warnings"])

    def test_long_function_warns(self, checker):
        body = "\n".join(f"    x{i} = {i}" for i in range(55))
        code = f"def long_func():\n{body}\n"
        result = checker.check(Path("long_func.py"), code)
        assert any("lines long" in w for w in result["warnings"])

    def test_duplicate_class_definitions(self, checker):
        code = "class Foo:\n    pass\nclass Foo:\n    pass\n"
        result = checker.check(Path("dup.py"), code)
        assert any("Duplicate class" in e for e in result["errors"])

    def test_excessive_file_length(self, checker):
        code = "\n".join(f"x{i} = {i}" for i in range(1010))
        result = checker.check(Path("big.py"), code)
        assert any("lines" in w and "splitting" in w for w in result["warnings"])

    def test_syntax_error_ignored(self, checker):
        result = checker.check(Path("bad.py"), "def (")
        assert result["errors"] == []
        assert result["warnings"] == []

    # -- check_dict --

    def test_check_dict_delegates(self, checker):
        code = "def greet():\n    pass\n"
        result = checker.check_dict(code)
        assert "errors" in result
        assert "warnings" in result

    # -- check_naming_patterns --

    def test_naming_long_function(self, checker):
        code = f"def {'a' * 45}():\n    pass\n"
        tree = ast.parse(code)
        issues = checker.check_naming_patterns(tree)
        assert any("long name" in i for i in issues)

    def test_naming_too_many_underscores(self, checker):
        code = "def a_b_c_d_e_f_g():\n    pass\n"
        tree = ast.parse(code)
        issues = checker.check_naming_patterns(tree)
        assert any("underscores" in i for i in issues)

    def test_class_name_lowercase(self, checker):
        code = "class myclass:\n    pass\n"
        tree = ast.parse(code)
        issues = checker.check_naming_patterns(tree)
        assert any("uppercase" in i for i in issues)

    def test_class_name_too_long(self, checker):
        code = f"class {'A' * 35}:\n    pass\n"
        tree = ast.parse(code)
        issues = checker.check_naming_patterns(tree)
        assert any("long name" in i for i in issues)

    # -- check_function_complexity --

    def test_deep_nesting(self, checker):
        code = textwrap.dedent("""\
            def deep():
                if True:
                    for i in range(1):
                        while True:
                            if True:
                                with open("x"):
                                    pass
        """)
        tree = ast.parse(code)
        func = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)][0]
        issues = checker.check_function_complexity(func)
        assert any("nesting" in i for i in issues)

    def test_many_branches(self, checker):
        ifs = "\n".join(f"    if x == {i}: pass" for i in range(12))
        code = f"def branchy(x):\n{ifs}\n"
        tree = ast.parse(code)
        func = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)][0]
        issues = checker.check_function_complexity(func)
        assert any("branches" in i for i in issues)

    def test_many_loops(self, checker):
        loops = "\n".join(f"    for i{n} in range(1): pass" for n in range(5))
        code = f"def loopy():\n{loops}\n"
        tree = ast.parse(code)
        func = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)][0]
        issues = checker.check_function_complexity(func)
        assert any("loops" in i for i in issues)

    def test_simple_function_no_issues(self, checker):
        code = "def ok(x):\n    return x + 1\n"
        tree = ast.parse(code)
        func = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)][0]
        assert checker.check_function_complexity(func) == []


# ===================================================================
# ASTAnalyzer
# ===================================================================


class TestASTAnalyzer:
    """ASTAnalyzer.parse_code, extract_*, get_docstring."""

    @pytest.fixture()
    def analyzer(self):
        return ASTAnalyzer()

    # -- parse_code --

    def test_parse_valid_code(self, analyzer):
        code = textwrap.dedent("""\
            import os

            X = 42

            def greet(name: str) -> str:
                \"\"\"Say hello.\"\"\"
                return f"hi {name}"

            class Foo:
                \"\"\"A class.\"\"\"
                pass
        """)
        parsed = analyzer.parse_code(code)
        assert parsed.is_valid is True
        assert parsed.errors == []

        names = {s.name for s in parsed.symbols}
        assert "greet" in names
        assert "Foo" in names
        assert "os" in names
        assert "X" in names

    def test_parse_invalid_code(self, analyzer):
        parsed = analyzer.parse_code("def (")
        assert parsed.is_valid is False
        assert len(parsed.errors) > 0

    def test_parse_extracts_imports(self, analyzer):
        code = "import os\nfrom pathlib import Path\n"
        parsed = analyzer.parse_code(code)
        assert "import os" in parsed.imports
        assert "from pathlib import Path" in parsed.imports

    def test_function_signature_with_types(self, analyzer):
        code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        parsed = analyzer.parse_code(code)
        func_sym = [s for s in parsed.symbols if s.name == "add"][0]
        assert "a: int" in func_sym.signature
        assert "-> int" in func_sym.signature

    def test_function_signature_varargs(self, analyzer):
        code = "def f(*args, **kwargs):\n    pass\n"
        parsed = analyzer.parse_code(code)
        func_sym = [s for s in parsed.symbols if s.name == "f"][0]
        assert "*args" in func_sym.signature
        assert "**kwargs" in func_sym.signature

    def test_async_function_detected(self, analyzer):
        code = "async def fetch():\n    pass\n"
        parsed = analyzer.parse_code(code)
        names = {s.name for s in parsed.symbols if s.type == "function"}
        assert "fetch" in names

    def test_class_docstring_extracted(self, analyzer):
        code = 'class Foo:\n    """Foo docs."""\n    pass\n'
        parsed = analyzer.parse_code(code)
        cls = [s for s in parsed.symbols if s.name == "Foo"][0]
        assert cls.docstring == "Foo docs."

    def test_module_level_variable(self, analyzer):
        code = "MY_CONST = 42\n"
        parsed = analyzer.parse_code(code)
        var = [s for s in parsed.symbols if s.name == "MY_CONST"]
        assert len(var) == 1
        assert var[0].type == "variable"

    # -- extract_functions / extract_classes --

    def test_extract_functions(self, analyzer):
        code = "def a():\n    pass\ndef b():\n    pass\n"
        tree = ast.parse(code)
        funcs = analyzer.extract_functions(tree)
        assert len(funcs) == 2

    def test_extract_classes(self, analyzer):
        code = "class A:\n    pass\nclass B:\n    pass\n"
        tree = ast.parse(code)
        classes = analyzer.extract_classes(tree)
        assert len(classes) == 2

    # -- get_docstring --

    def test_get_docstring_from_function(self, analyzer):
        code = 'def f():\n    """Hello."""\n    pass\n'
        tree = ast.parse(code)
        func = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)][0]
        assert analyzer.get_docstring(func) == "Hello."

    def test_get_docstring_none(self, analyzer):
        code = "def f():\n    pass\n"
        tree = ast.parse(code)
        func = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)][0]
        assert analyzer.get_docstring(func) is None


# ===================================================================
# RequirementsValidator
# ===================================================================


class TestRequirementsValidator:
    """RequirementsValidator.validate, check_package_validity, suggest_common_packages."""

    @pytest.fixture()
    def validator(self):
        return RequirementsValidator()

    # -- validate --

    def test_valid_requirements(self, validator, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("flask==2.3.0\nrequests>=2.28\n")
        result = validator.validate(req)
        assert result["is_valid"] is True
        assert result["packages"] == 2
        assert result["errors"] == []

    def test_hallucinated_package_detected(self, validator, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("flask-graphql-a-b-c-d-e\n")
        result = validator.validate(req)
        assert result["is_valid"] is False
        assert any("Hallucinated" in e for e in result["errors"])

    def test_recursive_ibm_pattern(self, validator, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("some-ibm-cloud-ibm-cloud-sdk\n")
        result = validator.validate(req)
        assert result["is_valid"] is False

    def test_package_name_too_long(self, validator, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("a" * 65 + "\n")
        result = validator.validate(req)
        assert result["is_valid"] is False
        assert any("too long" in e for e in result["errors"])

    def test_duplicate_package_warning(self, validator, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("flask\nflask\n")
        result = validator.validate(req)
        assert any("Duplicate" in w for w in result["warnings"])

    def test_comment_lines_ignored(self, validator, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("# comment\nflask\n")
        result = validator.validate(req)
        assert result["is_valid"] is True
        assert result["packages"] == 1

    def test_empty_lines_ignored(self, validator, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("flask\n\nrequests\n")
        result = validator.validate(req)
        assert result["packages"] == 2

    def test_many_packages_warning(self, validator, tmp_path):
        req = tmp_path / "requirements.txt"
        lines = [f"pkg{i}" for i in range(35)]
        req.write_text("\n".join(lines))
        result = validator.validate(req)
        assert any("Many packages" in w for w in result["warnings"])

    def test_too_many_packages_error(self, validator, tmp_path):
        req = tmp_path / "requirements.txt"
        lines = [f"pkg{i}" for i in range(55)]
        req.write_text("\n".join(lines))
        result = validator.validate(req)
        assert any("Too many" in e for e in result["errors"])

    def test_auto_fix_removes_bad_packages(self, validator, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("flask\nflask-graphql-a-b-c-d-e\nrequests\n")
        result = validator.validate(req, fix=True)
        assert result["fixed_content"] is not None
        assert "flask-graphql" not in result["fixed_content"]
        assert "flask" in result["fixed_content"]
        assert "requests" in result["fixed_content"]

    # -- check_package_validity --

    def test_valid_package_name(self, validator):
        assert validator.check_package_validity("flask") is True
        assert validator.check_package_validity("scikit-learn") is True
        assert validator.check_package_validity("python-dotenv") is True

    def test_hallucinated_package_invalid(self, validator):
        assert validator.check_package_validity("x-ibm-cloud-ibm-cloud-y") is False

    def test_too_long_package_invalid(self, validator):
        assert validator.check_package_validity("a" * 61) is False

    def test_invalid_chars_package(self, validator):
        assert validator.check_package_validity("flask@latest") is False

    def test_package_starting_with_hyphen(self, validator):
        assert validator.check_package_validity("-flask") is False

    # -- suggest_common_packages --

    def test_suggest_web(self, validator):
        pkgs = validator.suggest_common_packages("web")
        assert "flask" in pkgs
        assert "django" in pkgs

    def test_suggest_ml(self, validator):
        pkgs = validator.suggest_common_packages("ml")
        assert "torch" in pkgs

    def test_suggest_unknown_falls_back(self, validator):
        pkgs = validator.suggest_common_packages("gaming")
        assert pkgs == validator.suggest_common_packages("general")
