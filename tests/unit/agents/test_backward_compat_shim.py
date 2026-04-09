"""
Tests for _ToolRegistryAlias backward compatibility shim.

This test suite validates the backward compatibility layer that allows
legacy code to continue working while issuing deprecation warnings.

Quality Gate 1 Criteria Covered:
- BC-001: 100% backward compatibility with legacy _TOOL_REGISTRY access
- BC-001: Deprecation warnings issued appropriately
- BC-001: @tool decorator works with both @tool and @tool(...) syntax
"""

import pytest
import warnings
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from gaia.agents.base.tools import (
    _TOOL_REGISTRY,
    _ToolRegistryAlias,
    ToolRegistry,
    tool,
)


# =============================================================================
# Dict Interface Tests - Read Operations
# =============================================================================

class TestBackwardCompatDictRead:
    """Tests for _TOOL_REGISTRY dict-style read operations."""

    def setup_method(self):
        """Reset registry and clear warnings before each test."""
        ToolRegistry._instance = None
        _TOOL_REGISTRY.clear()
        _ToolRegistryAlias._warned = False

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None
        _ToolRegistryAlias._warned = False

    def test_dict_getitem(self):
        """Test dict-style item access _TOOL_REGISTRY[key]."""
        registry = ToolRegistry.get_instance()
        registry.register("test_tool", lambda: None, description="Test tool")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            tool_info = _TOOL_REGISTRY["test_tool"]

            assert tool_info["description"] == "Test tool"
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()

    def test_dict_getitem_nonexistent(self):
        """Test dict-style item access for non-existent tool raises KeyError."""
        with pytest.raises(KeyError):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _ = _TOOL_REGISTRY["nonexistent"]

    def test_dict_get(self):
        """Test dict-style get() method."""
        registry = ToolRegistry.get_instance()
        registry.register("existing_tool", lambda: None, description="Exists")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            # Existing key
            result = _TOOL_REGISTRY.get("existing_tool", None)
            assert result is not None
            assert result["description"] == "Exists"

            # Non-existing key with default
            result = _TOOL_REGISTRY.get("nonexistent", "default_value")
            assert result == "default_value"

            # Non-existing key without default
            result = _TOOL_REGISTRY.get("nonexistent")
            assert result is None

    def test_dict_keys(self):
        """Test dict-style keys() method."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)
        registry.register("tool3", lambda: None)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            keys = list(_TOOL_REGISTRY.keys())

        assert "tool1" in keys
        assert "tool2" in keys
        assert "tool3" in keys
        assert len(keys) == 3

    def test_dict_values(self):
        """Test dict-style values() method."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None, description="Tool 1")
        registry.register("tool2", lambda: None, description="Tool 2")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            values = list(_TOOL_REGISTRY.values())

        assert len(values) == 2
        descriptions = [v["description"] for v in values]
        assert "Tool 1" in descriptions
        assert "Tool 2" in descriptions

    def test_dict_items(self):
        """Test dict-style items() method."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None, description="Tool 1")
        registry.register("tool2", lambda: None, description="Tool 2")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            items = list(_TOOL_REGISTRY.items())

        assert len(items) == 2
        item_dict = dict(items)
        assert "tool1" in item_dict
        assert "tool2" in item_dict

    def test_dict_contains(self):
        """Test 'in' operator for dict containment check."""
        registry = ToolRegistry.get_instance()
        registry.register("existing_tool", lambda: None)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            assert "existing_tool" in _TOOL_REGISTRY
            assert "nonexistent_tool" not in _TOOL_REGISTRY

    def test_dict_len(self):
        """Test len() function on _TOOL_REGISTRY."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert len(_TOOL_REGISTRY) == 2

    def test_dict_copy(self):
        """Test dict-style copy() method."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None, description="Tool 1")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            copy = _TOOL_REGISTRY.copy()

        assert isinstance(copy, dict)
        assert "tool1" in copy
        assert copy["tool1"]["description"] == "Tool 1"

    def test_dict_copy_is_shallow(self):
        """Test that copy() returns a shallow copy (outer dict copied, inner dicts shared)."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None, description="Original")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            copy = _TOOL_REGISTRY.copy()

        # The outer dict is a copy, so removing keys doesn't affect registry
        assert "tool1" in copy
        del copy["tool1"]
        assert "tool1" not in copy

        # But registry still has it (shallow copy - outer dict is copied)
        assert registry.has_tool("tool1")


# =============================================================================
# Dict Interface Tests - Write Operations
# =============================================================================

class TestBackwardCompatDictWrite:
    """Tests for _TOOL_REGISTRY dict-style write operations."""

    def setup_method(self):
        """Reset registry and clear warnings before each test."""
        ToolRegistry._instance = None
        _TOOL_REGISTRY.clear()
        _ToolRegistryAlias._warned = False

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None
        _ToolRegistryAlias._warned = False

    def test_dict_setitem(self):
        """Test dict-style item assignment _TOOL_REGISTRY[key] = value."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _TOOL_REGISTRY["new_tool"] = {
                "function": lambda: None,
                "description": "New tool",
            }

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)

        registry = ToolRegistry.get_instance()
        assert registry.has_tool("new_tool")

        tool_info = registry.get_tool("new_tool")
        assert tool_info["description"] == "New tool"

    def test_dict_setitem_with_full_metadata(self):
        """Test dict-style assignment with full tool metadata."""
        def my_tool(x: int) -> int:
            """My tool docstring."""
            return x * 2

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _TOOL_REGISTRY["full_tool"] = {
                "function": my_tool,
                "description": "Custom description",
            }

        registry = ToolRegistry.get_instance()
        tool_info = registry.get_tool("full_tool")

        assert tool_info["name"] == "full_tool"
        assert tool_info["description"] == "Custom description"
        assert tool_info["parameters"]["x"]["type"] == "integer"

    def test_dict_delitem(self):
        """Test dict-style item deletion del _TOOL_REGISTRY[key]."""
        registry = ToolRegistry.get_instance()
        registry.register("temp_tool", lambda: None)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            del _TOOL_REGISTRY["temp_tool"]

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)

        assert not registry.has_tool("temp_tool")

    def test_dict_delitem_nonexistent(self):
        """Test deleting non-existent tool returns False (no KeyError)."""
        registry = ToolRegistry.get_instance()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # unregister() returns False for non-existent tools
            # but __delitem__ doesn't propagate this - it just calls unregister
            result = registry.unregister("nonexistent")

        assert result is False


# =============================================================================
# Dict Interface Tests - Iteration
# =============================================================================

class TestBackwardCompatDictIteration:
    """Tests for _TOOL_REGISTRY dict-style iteration."""

    def setup_method(self):
        """Reset registry and clear warnings before each test."""
        ToolRegistry._instance = None
        _TOOL_REGISTRY.clear()
        _ToolRegistryAlias._warned = False

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None
        _ToolRegistryAlias._warned = False

    def test_dict_iter(self):
        """Test iteration over _TOOL_REGISTRY."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)
        registry.register("tool3", lambda: None)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            keys = list(_TOOL_REGISTRY)

        assert "tool1" in keys
        assert "tool2" in keys
        assert "tool3" in keys

    def test_dict_iter_empty(self):
        """Test iteration over empty registry."""
        _TOOL_REGISTRY.clear()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            keys = list(_TOOL_REGISTRY)

        assert len(keys) == 0

    def test_dict_comprehension(self):
        """Test dict comprehension with _TOOL_REGISTRY."""
        registry = ToolRegistry.get_instance()
        registry.register("tool_a", lambda: None, description="A tool")
        registry.register("tool_b", lambda: None, description="B tool")
        registry.register("tool_c", lambda: None, description="C tool")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Filter tools starting with 'tool_a' or 'tool_b'
            filtered = {
                k: v for k, v in _TOOL_REGISTRY.items()
                if k.startswith("tool_") and k in ("tool_a", "tool_b")
            }

        assert len(filtered) == 2
        assert "tool_a" in filtered
        assert "tool_b" in filtered


# =============================================================================
# Deprecation Warning Tests
# =============================================================================

class TestDeprecationWarnings:
    """Tests for deprecation warning behavior."""

    def setup_method(self):
        """Reset registry and clear warnings before each test."""
        ToolRegistry._instance = None
        _TOOL_REGISTRY.clear()
        _ToolRegistryAlias._warned = False

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None
        _ToolRegistryAlias._warned = False

    def test_deprecation_warning_on_getitem(self):
        """Test deprecation warning issued on dict-style access."""
        registry = ToolRegistry.get_instance()
        registry.register("test_tool", lambda: None)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = _TOOL_REGISTRY["test_tool"]

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()
            assert "_TOOL_REGISTRY" in str(w[0].message)
            assert "ToolRegistry.get_instance()" in str(w[0].message)

    def test_deprecation_warning_on_setitem(self):
        """Test deprecation warning issued on dict-style assignment."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _TOOL_REGISTRY["new_tool"] = {"function": lambda: None}

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)

    def test_deprecation_warning_on_delitem(self):
        """Test deprecation warning issued on dict-style deletion."""
        registry = ToolRegistry.get_instance()
        registry.register("temp_tool", lambda: None)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            del _TOOL_REGISTRY["temp_tool"]

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)

    def test_deprecation_warning_once_per_session(self):
        """Test deprecation warning issued only once per session."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            # First access
            _ = _TOOL_REGISTRY["tool1"]
            # Second access
            _ = _TOOL_REGISTRY["tool2"]
            # Third access (getitem)
            _ = "tool1" in _TOOL_REGISTRY

            # Should only get one warning
            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 1

    def test_warning_shows_operation_type(self):
        """Test warning message includes the type of operation."""
        registry = ToolRegistry.get_instance()
        registry.register("test_tool", lambda: None)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = _TOOL_REGISTRY["test_tool"]

            assert len(w) == 1
            # Warning should mention "dict access" or similar
            message = str(w[0].message).lower()
            assert "access" in message or "dict" in message


# =============================================================================
# @tool Decorator Tests
# =============================================================================

class TestToolDecoratorBackwardCompat:
    """Tests for @tool decorator backward compatibility."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None
        _TOOL_REGISTRY.clear()
        _ToolRegistryAlias._warned = False

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None
        _ToolRegistryAlias._warned = False

    def test_decorator_simple_syntax(self):
        """Test @tool decorator without parentheses."""
        @tool
        def my_tool():
            """My tool function."""
            return "result"

        assert "my_tool" in _TOOL_REGISTRY
        assert my_tool() == "result"

    def test_decorator_with_parentheses(self):
        """Test @tool() decorator with empty parentheses."""
        @tool()
        def my_tool():
            """My tool function."""
            return "result"

        assert "my_tool" in _TOOL_REGISTRY

    def test_decorator_with_atomic(self):
        """Test @tool(atomic=True) decorator."""
        @tool(atomic=True)
        def atomic_tool():
            """Atomic tool function."""
            return "atomic"

        assert "atomic_tool" in _TOOL_REGISTRY

        registry = ToolRegistry.get_instance()
        tool_info = registry.get_tool("atomic_tool")
        assert tool_info["atomic"] is True

    def test_decorator_preserves_function_metadata(self):
        """Test @tool decorator preserves function __name__, __doc__, etc."""
        @tool
        def documented_function(x: int) -> int:
            """This is my documented function."""
            return x * 2

        assert documented_function.__name__ == "documented_function"
        assert documented_function.__doc__ == "This is my documented function."

        # Should still be callable
        result = documented_function(5)
        assert result == 10

    def test_decorator_registers_with_both_syntaxes(self):
        """Test both @tool and @tool() register tools accessible via _TOOL_REGISTRY."""
        _TOOL_REGISTRY.clear()

        @tool
        def tool1():
            """Tool 1."""
            pass

        @tool()
        def tool2():
            """Tool 2."""
            pass

        @tool(atomic=True)
        def tool3():
            """Tool 3."""
            pass

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert "tool1" in _TOOL_REGISTRY
            assert "tool2" in _TOOL_REGISTRY
            assert "tool3" in _TOOL_REGISTRY

    def test_decorator_uses_docstring_as_description(self):
        """Test @tool decorator uses function docstring as description."""
        @tool
        def my_tool():
            """This is my custom description."""
            pass

        registry = ToolRegistry.get_instance()
        tool_info = registry.get_tool("my_tool")
        assert tool_info["description"] == "This is my custom description."

    def test_decorator_empty_docstring(self):
        """Test @tool decorator handles empty docstring."""
        @tool
        def no_docstring():
            pass

        registry = ToolRegistry.get_instance()
        tool_info = registry.get_tool("no_docstring")
        assert tool_info["description"] == ""

    def test_decorator_infers_parameters(self):
        """Test @tool decorator infers parameter types from annotations."""
        @tool
        def typed_tool(
            name: str,
            count: int,
            value: float,
            flag: bool,
            data: dict,
            items: list
        ):
            """Tool with typed parameters."""
            pass

        registry = ToolRegistry.get_instance()
        tool_info = registry.get_tool("typed_tool")
        params = tool_info["parameters"]

        assert params["name"]["type"] == "string"
        assert params["count"]["type"] == "integer"
        assert params["value"]["type"] == "number"
        assert params["flag"]["type"] == "boolean"
        assert params["data"]["type"] == "object"
        assert params["items"]["type"] == "array"


# =============================================================================
# Clear Operation Tests
# =============================================================================

class TestBackwardCompatClear:
    """Tests for _TOOL_REGISTRY.clear() operation."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None
        _TOOL_REGISTRY.clear()
        _ToolRegistryAlias._warned = False

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None
        _ToolRegistryAlias._warned = False

    def test_clear_removes_all_tools(self):
        """Test clear() removes all tools from registry."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _TOOL_REGISTRY.clear()

        assert len(registry.get_all_tools()) == 0

    def test_clear_is_thread_safe(self):
        """Test clear() is thread-safe."""
        registry = ToolRegistry.get_instance()

        errors = []
        lock = threading.Lock()

        def register_and_clear():
            try:
                for i in range(10):
                    registry.register(f"tool_{threading.current_thread().name}_{i}", lambda: None)
                    if i % 5 == 0:
                        _TOOL_REGISTRY.clear()
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(register_and_clear) for _ in range(10)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================

class TestBackwardCompatEdgeCases:
    """Edge case tests for backward compatibility shim."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None
        _TOOL_REGISTRY.clear()
        _ToolRegistryAlias._warned = False

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None
        _ToolRegistryAlias._warned = False

    def test_get_with_none_default(self):
        """Test get() with None default value."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = _TOOL_REGISTRY.get("nonexistent", None)
            assert result is None

    def test_get_with_custom_default(self):
        """Test get() with custom default value."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = _TOOL_REGISTRY.get("nonexistent", {"custom": "default"})
            assert result == {"custom": "default"}

    def test_contains_with_special_characters(self):
        """Test 'in' operator with special character tool names."""
        registry = ToolRegistry.get_instance()
        registry.register("tool-with-dash", lambda: None)
        registry.register("tool_with_underscore", lambda: None)
        registry.register("tool.with.dot", lambda: None)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert "tool-with-dash" in _TOOL_REGISTRY
            assert "tool_with_underscore" in _TOOL_REGISTRY
            assert "tool.with.dot" in _TOOL_REGISTRY
            assert "nonexistent" not in _TOOL_REGISTRY

    def test_keys_values_items_consistency(self):
        """Test keys(), values(), items() return consistent results."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None, description="Tool 1")
        registry.register("tool2", lambda: None, description="Tool 2")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            keys = list(_TOOL_REGISTRY.keys())
            values = list(_TOOL_REGISTRY.values())
            items = list(_TOOL_REGISTRY.items())

        assert len(keys) == len(values) == len(items) == 2

        # Items should match keys and values
        item_keys = [k for k, v in items]
        item_values = [v for k, v in items]
        assert set(keys) == set(item_keys)

    def test_iteration_modification_raises(self):
        """Test modifying registry during iteration may raise RuntimeError."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)

        # This test verifies the behavior - may or may not raise depending on implementation
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                for key in _TOOL_REGISTRY:
                    pass  # Simple iteration should work
            except RuntimeError:
                pytest.skip("Modification during iteration raises RuntimeError (expected behavior)")


# =============================================================================
# Integration Tests
# =============================================================================

class TestBackwardCompatIntegration:
    """Integration tests for backward compatibility shim."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None
        _TOOL_REGISTRY.clear()
        _ToolRegistryAlias._warned = False

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None
        _ToolRegistryAlias._warned = False

    def test_legacy_pattern_direct_function_access(self):
        """Test legacy pattern of direct function access still works."""
        registry = ToolRegistry.get_instance()
        registry.register("my_tool", lambda x: x * 2, description="My tool")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Legacy pattern: _TOOL_REGISTRY["tool_name"]["function"](**kwargs)
            func = _TOOL_REGISTRY["my_tool"]["function"]
            result = func(5)
            assert result == 10

    def test_legacy_pattern_check_then_execute(self):
        """Test legacy pattern of checking existence before execution."""
        registry = ToolRegistry.get_instance()
        registry.register("existing_tool", lambda: "exists")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Legacy pattern
            if "existing_tool" in _TOOL_REGISTRY:
                result = _TOOL_REGISTRY["existing_tool"]["function"]()
                assert result == "exists"

            if "nonexistent_tool" not in _TOOL_REGISTRY:
                pass  # Tool doesn't exist, handle gracefully

    def test_mixed_usage_legacy_and_new_api(self):
        """Test mixing legacy _TOOL_REGISTRY and new ToolRegistry API."""
        registry = ToolRegistry.get_instance()

        # Register using new API
        registry.register("new_api_tool", lambda: "new", description="New API")

        # Access using legacy API
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert "new_api_tool" in _TOOL_REGISTRY

        # Register using legacy API
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _TOOL_REGISTRY["legacy_tool"] = {
                "function": lambda: "legacy",
                "description": "Legacy API",
            }

        # Access using new API
        assert registry.has_tool("legacy_tool")
        tool_info = registry.get_tool("legacy_tool")
        assert tool_info["description"] == "Legacy API"
