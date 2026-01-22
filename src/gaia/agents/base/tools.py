# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tool registry and decorator for agent tools.
"""

import inspect
import logging
from typing import Callable, Dict

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Tool registry to store registered tools
_TOOL_REGISTRY = {}


def tool(
    func: Callable = None,
    *,
    atomic: bool = False,
    **kwargs,  # pylint: disable=unused-argument
) -> Callable:
    """
    Decorator to register a function as a tool.
    Similar to smolagents tool decorator but simpler.

    Supports both @tool and @tool(...) syntax for backward compatibility.
    Extra keyword arguments are ignored.

    Args:
        func: Function to register as a tool (when used as @tool)
        atomic: If True, marks this tool as atomic (can execute without multi-step planning)
        **kwargs: Optional arguments (ignored, for backward compatibility)

    Returns:
        The original function or decorator, unchanged
    """

    def decorator(f: Callable) -> Callable:
        # Extract function name and signature for the tool registry
        tool_name = f.__name__
        sig = inspect.signature(f)
        params = {}

        for name, param in sig.parameters.items():
            param_info = {
                "type": "unknown",
                "required": param.default == inspect.Parameter.empty,
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

        # Register the tool with atomic metadata
        _TOOL_REGISTRY[tool_name] = {
            "name": tool_name,
            "description": f.__doc__ or "",
            "parameters": params,
            "function": f,
            "atomic": atomic,
        }

        # Return the function unchanged
        return f

    # Support both @tool and @tool(...) syntax
    if func is not None:
        # Called as @tool without parentheses
        return decorator(func)
    else:
        # Called as @tool(...) with arguments - return the decorator
        return decorator
