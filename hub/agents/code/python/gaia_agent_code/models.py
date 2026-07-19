#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Data models for the Code Agent.

This module contains all data classes and type definitions used across
the code agent modules.
"""

import ast
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CodeSymbol:
    """Represents a code symbol (function, class, variable)."""

    name: str
    type: str  # 'function', 'class', 'variable', 'import'
    line: int
    docstring: Optional[str] = None
    signature: Optional[str] = None


@dataclass
class ParsedCode:
    """Result of parsing Python code."""

    ast_tree: Optional[ast.Module] = None
    symbols: List[CodeSymbol] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    is_valid: bool = False


@dataclass
class ModuleSpec:
    """Specification for a module in a project."""

    name: str
    purpose: str
    classes: List[Dict[str, Any]] = field(default_factory=list)
    functions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TestSpec:
    """Specification for a test module."""

    name: str
    coverage: str
    test_cases: List[str] = field(default_factory=list)


@dataclass
class ProjectPlan:
    """Complete project plan with architecture and modules."""

    name: str
    architecture: Dict[str, Any]
    modules: List[ModuleSpec]
    tests: List[TestSpec]
    description: str = ""
    project_type: str = "application"


@dataclass
class ValidationResult:
    """Result of code validation."""

    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    fixes_applied: List[str] = field(default_factory=list)
    file_modified: bool = False


@dataclass
class MethodSpec:
    """Specification for a class method."""

    name: str
    params: str = "self"
    docstring: str = "Method description."
    body: str = "pass"
    return_type: Optional[str] = None


@dataclass
class ProjectStructure:
    """Represents a complete project structure."""

    name: str
    files: Dict[str, str]  # filename -> content
    structure: Dict[str, Any]  # nested directory structure
    plan: Optional[ProjectPlan] = None


@dataclass
class WorkflowPlan:
    """Plan for executing a coding workflow."""

    query: str
    steps: List[Dict[str, Any]]
    current_step: int = 0
    completed_steps: List[int] = field(default_factory=list)
    status: str = "pending"  # pending, in_progress, completed, failed


@dataclass
class LintIssue:
    """Represents a linting issue found by pylint."""

    type: str  # error, warning, convention, refactor
    message: str
    file: str
    line: int
    column: int = 0
    symbol: Optional[str] = None


@dataclass
class ExecutionResult:
    """Result of executing Python code."""

    stdout: str
    stderr: str
    return_code: int
    has_errors: bool
    duration_seconds: float
    timed_out: bool = False
    file_path: Optional[str] = None
    command: Optional[str] = None
