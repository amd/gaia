# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Build-time check of registered Python tools inside a frozen executable."""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

from gaia.agents.base.tools import _TOOL_REGISTRY

assert getattr(sys, "frozen", False), "This check must run as a frozen binary"
with TemporaryDirectory() as directory:
    root = Path(directory).resolve()
    os.chdir(root)
    os.environ["GAIA_PROJECT_ROOT"] = str(root)
    os.environ["HOME"] = str(root)
    os.environ["GAIA_HOME"] = str(root / ".gaia")
    os.environ["GAIA_MEMORY_DISABLED"] = "1"
    with (
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig"),
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
    ):
        agent = ChatAgent(ChatAgentConfig(silent_mode=True, allowed_paths=[str(root)]))
    code = "import sys; print(6 * 7); print(sys.executable)"
    script = root / "example.py"
    script.write_text(code)
    for name, args in [
        ("run_python", {"code": code}),
        ("execute_python_file", {"file_path": str(script)}),
    ]:
        result = _TOOL_REGISTRY[name]["function"](**args)
        assert result["status"] == "success", (name, result)
        assert result["stdout"].splitlines() == [
            "42",
            os.environ["GAIA_PYTHON_EXECUTABLE"],
        ], (name, result)
print("Frozen Python tools passed with the external interpreter.")
