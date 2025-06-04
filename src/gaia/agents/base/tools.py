# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tool registry and decorator for agent tools.
"""

<<<<<<< HEAD
from typing import Dict, Any, Callable
=======
from typing import Dict, Callable
>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
import inspect
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Tool registry to store registered tools
_TOOL_REGISTRY = {}

<<<<<<< HEAD
=======

>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
def tool(func: Callable) -> Callable:
    """
    Decorator to register a function as a tool.
    Similar to smolagents tool decorator but simpler.

    Args:
        func: Function to register as a tool

    Returns:
        The original function, unchanged
    """
    # Extract function name and signature for the tool registry
    tool_name = func.__name__
    sig = inspect.signature(func)
    params = {}

    for name, param in sig.parameters.items():
        param_info = {
            "type": "unknown",
<<<<<<< HEAD
            "required": param.default == inspect.Parameter.empty
=======
            "required": param.default == inspect.Parameter.empty,
>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
        }

        # Try to infer type from annotations
        if param.annotation != inspect.Parameter.empty:
            if param.annotation == str:
                param_info["type"] = "string"
            elif param.annotation == int:
                param_info["type"] = "integer"
            elif param.annotation == float:
                param_info["type"] = "number"
            elif param.annotation == bool:
                param_info["type"] = "boolean"
            elif param.annotation == tuple:
                param_info["type"] = "array"
            elif param.annotation == dict or param.annotation == Dict:
                param_info["type"] = "object"

        params[name] = param_info

    # Register the tool
    _TOOL_REGISTRY[tool_name] = {
        "name": tool_name,
        "description": func.__doc__ or "",
        "parameters": params,
<<<<<<< HEAD
        "function": func
    }

    # Return the function unchanged
    return func
=======
        "function": func,
    }

    # Return the function unchanged
    return func
>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
