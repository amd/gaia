#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Requirements.txt validation to detect hallucinated packages."""

import re
from pathlib import Path
from typing import Any, Dict, List


class RequirementsValidator:
    """Validates requirements.txt files for hallucinated packages."""

    # Patterns indicating hallucinated packages (from HRMS issue)
    HALLUCINATION_PATTERNS = [
        r".*-ibm-cloud-ibm-cloud.*",  # Recursive IBM
        r".*-azure-.*-azure.*",  # Recursive Azure
        r".*-gcp-.*-gcp.*",  # Recursive GCP
        r".*(\\w{4,})-\\1-\\1.*",  # Same word 3+ times
        r"flask-graphql-.*-.*-.*-.*-.*",  # 5+ segments
    ]

    def validate(self, req_file: Path, fix: bool = False) -> Dict[str, Any]:
        """Validate requirements.txt for hallucinated packages.

        Args:
            req_file: Path to requirements.txt file
            fix: Whether to auto-fix issues

        Returns:
            Dictionary with validation results
        """
        content = req_file.read_text()
        errors = []
        warnings = []
        lines = content.strip().split("\n")
        valid_lines = []
        seen_packages = set()

        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                valid_lines.append(line)
                continue

            # Extract package name
            package_match = re.match(r"^([a-zA-Z0-9][a-zA-Z0-9._-]*)", line)
            if not package_match:
                errors.append(f"Line {line_num}: Invalid format")
                continue

            package_name = package_match.group(1).lower()

            # Check for hallucination
            is_hallucinated = False
            for pattern in self.HALLUCINATION_PATTERNS:
                if re.match(pattern, package_name):
                    errors.append(
                        f"Line {line_num}: Hallucinated package: {package_name[:50]}..."
                    )
                    is_hallucinated = True
                    break

            if is_hallucinated:
                continue

            # Check for excessive length
            if len(package_name) > 60:
                errors.append(
                    f"Line {line_num}: Package name too long ({len(package_name)} chars)"
                )
                continue

            # Check duplicates
            if package_name in seen_packages:
                warnings.append(f"Line {line_num}: Duplicate package: {package_name}")
                continue

            seen_packages.add(package_name)
            valid_lines.append(line)

        # Check total count
        if len(seen_packages) > 50:
            errors.append(
                f"Too many packages ({len(seen_packages)}). Likely hallucinated."
            )
        elif len(seen_packages) > 30:
            warnings.append(f"Many packages ({len(seen_packages)})")

        # Auto-fix if requested
        fixed_content = None
        if fix and errors:
            fixed_content = "\n".join(valid_lines)
            req_file.write_text(fixed_content)

        return {
            "is_valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "packages": len(seen_packages),
            "fixed_content": fixed_content,
        }

    def check_package_validity(self, package_name: str) -> bool:
        """Check if a package name appears valid.

        Args:
            package_name: Name of the package to check

        Returns:
            True if package name appears valid
        """
        # Check against hallucination patterns
        for pattern in self.HALLUCINATION_PATTERNS:
            if re.match(pattern, package_name.lower()):
                return False

        # Check length
        if len(package_name) > 60:
            return False

        # Check for valid characters
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", package_name):
            return False

        return True

    def suggest_common_packages(self, project_type: str) -> List[str]:
        """Suggest common packages for a project type.

        Args:
            project_type: Type of project (e.g., 'web', 'data', 'ml')

        Returns:
            List of suggested package names
        """
        suggestions = {
            "web": ["flask", "django", "fastapi", "requests", "beautifulsoup4"],
            "data": ["pandas", "numpy", "matplotlib", "seaborn", "jupyter"],
            "ml": ["scikit-learn", "tensorflow", "torch", "transformers", "datasets"],
            "test": ["pytest", "pytest-cov", "mock", "hypothesis", "tox"],
            "general": ["black", "pylint", "mypy", "python-dotenv", "pyyaml"],
        }

        return suggestions.get(project_type, suggestions["general"])
