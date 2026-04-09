# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pipeline tools package for GAIA ConfigurableAgent.

Tools are registered lazily -- import a specific module to trigger @tool
registration.  Do NOT add eager imports of the ops modules here; that would
pull every tool into the global ``_TOOL_REGISTRY`` and may collide with
CodeAgent's identically-named tools in the same process.
"""

# Map YAML tool names to the module containing their implementation.
# ConfigurableAgent._load_tool_module() uses this to resolve imports.
TOOL_MODULE_MAP = {
    "file_read": "gaia.tools.file_ops",
    "file_write": "gaia.tools.file_ops",
    "file_list": "gaia.tools.file_ops",
    "bash_execute": "gaia.tools.shell_ops",
    "run_tests": "gaia.tools.shell_ops",
    "search_codebase": "gaia.tools.code_ops",
    "git_operations": "gaia.tools.code_ops",
}
