# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``replace_function`` must replace one definition and nothing else.

It used to find the end of the target by scanning forward for the next line at
the same indent starting with ``def``/``class``. Everything in between — module
constants, the *next* function's decorators — sat inside the replaced span and
was deleted, while the tool returned ``success``. Two more defects rode along:
the target's own decorators sat *above* ``start_line``, so they survived and were
silently re-applied to the replacement; and ``ast.walk`` took the first
same-named function anywhere in the module.

These tests assert on the resulting file, not on the return value — the old code
reported success for every one of them.
"""

from __future__ import annotations

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin

MODULE = """\
import functools


@functools.cache
def foo():
    return 1


CONSTANT = 42


@functools.cache
def bar():
    return CONSTANT


class Runner:
    def run(self):
        return "method run"


def run():
    return "module run"
"""


@pytest.fixture
def replace():
    """The registered ``replace_function`` tool, with no PathValidator attached."""
    mixin = FileIOToolsMixin()
    mixin.console = None
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        mixin.register_file_io_tools()
        fn = _TOOL_REGISTRY["replace_function"]["function"]
        yield fn
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def module(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(MODULE, encoding="utf-8")
    return target


def test_a_module_constant_between_two_functions_survives(replace, module):
    result = replace(str(module), "foo", "def foo():\n    return 99", backup=False)
    after = module.read_text(encoding="utf-8")

    assert result["status"] == "success"
    assert "CONSTANT = 42" in after
    assert "return 99" in after


def test_the_next_functions_decorator_survives(replace, module):
    replace(str(module), "foo", "def foo():\n    return 99", backup=False)
    after = module.read_text(encoding="utf-8")

    assert "@functools.cache\ndef bar():" in after


def test_the_targets_own_decorator_is_replaced_not_re_applied(replace, module):
    """The replacement fully defines the function; a dropped decorator is a
    deliberate change the caller asked for, not a silent carry-over."""
    replace(str(module), "foo", "def foo():\n    return 99", backup=False)
    after = module.read_text(encoding="utf-8")

    assert "@functools.cache\ndef foo():" not in after
    assert after.count("@functools.cache") == 1  # bar's, and only bar's


def test_a_bare_name_resolves_to_the_module_level_function(replace, module):
    replace(str(module), "run", 'def run():\n    return "REPLACED"', backup=False)
    after = module.read_text(encoding="utf-8")

    assert 'return "method run"' in after  # Runner.run untouched
    assert 'return "REPLACED"' in after


def test_a_method_level_name_is_not_clobbered_by_a_bare_name(replace, tmp_path):
    """Two same-named methods and no module-level one: refuse, don't guess."""
    source = (
        'class Alpha:\n    def run(self):\n        return "alpha"\n\n\n'
        'class Beta:\n    def run(self):\n        return "beta"\n'
    )
    target = tmp_path / "classes.py"
    target.write_text(source, encoding="utf-8")

    result = replace(str(target), "run", "whatever", backup=False)

    assert result["status"] == "error"
    assert "Alpha.run" in result["error"] and "Beta.run" in result["error"]
    assert target.read_text(encoding="utf-8") == source


def test_a_qualified_name_replaces_exactly_that_method(replace, tmp_path):
    source = (
        'class Alpha:\n    def run(self):\n        return "alpha"\n\n\n'
        'class Beta:\n    def run(self):\n        return "beta"\n'
    )
    target = tmp_path / "classes.py"
    target.write_text(source, encoding="utf-8")

    result = replace(
        str(target),
        "Alpha.run",
        '    def run(self):\n        return "PATCHED"',
        backup=False,
    )
    after = target.read_text(encoding="utf-8")

    assert result["status"] == "success"
    assert 'return "PATCHED"' in after
    assert 'return "beta"' in after


def test_a_module_level_function_guarded_by_if_is_still_module_level(replace, tmp_path):
    """``if TYPE_CHECKING:`` does not open a scope, so the name stays bare."""
    source = (
        "import typing\n\nif typing.TYPE_CHECKING:\n"
        "    async def guarded():\n        return 1\n\n\n"
        "def after():\n    return 2\n"
    )
    target = tmp_path / "guarded.py"
    target.write_text(source, encoding="utf-8")

    result = replace(
        str(target),
        "guarded",
        "    async def guarded():\n        return 99",
        backup=False,
    )
    after = target.read_text(encoding="utf-8")

    assert result["status"] == "success"
    assert "return 99" in after
    assert "def after():\n    return 2" in after


CLASS_WITH_ATTR = """\
class Alpha:
    x = 1

    def run(self):
        return "alpha"
"""


def test_a_de_indented_method_replacement_is_refused(replace, tmp_path):
    """Parsing clean is not the same as landing in the right scope.

    Without the indentation the replacement is a valid module-level ``def``, so
    the syntax gate passes while ``Alpha.run`` quietly leaves its class.
    """
    target = tmp_path / "alpha.py"
    target.write_text(CLASS_WITH_ATTR, encoding="utf-8")

    result = replace(
        str(target),
        "Alpha.run",
        'def run(self):\n    return "PATCHED"',
        backup=False,
    )

    assert result["status"] == "error"
    assert "Alpha.run" in result["error"] and "indent" in result["error"].lower()
    assert target.read_text(encoding="utf-8") == CLASS_WITH_ATTR


def test_the_correctly_indented_replacement_of_the_same_method_succeeds(
    replace, tmp_path
):
    """The guard must not cost the legitimate edit."""
    target = tmp_path / "alpha.py"
    target.write_text(CLASS_WITH_ATTR, encoding="utf-8")

    result = replace(
        str(target),
        "Alpha.run",
        '    def run(self):\n        return "PATCHED"',
        backup=False,
    )
    after = target.read_text(encoding="utf-8")

    assert result["status"] == "success"
    assert 'return "PATCHED"' in after
    assert "    x = 1" in after
    assert after.startswith("class Alpha:")


def test_a_replacement_that_renames_the_function_is_refused(replace, module):
    """`replace_function` replaces one definition; it does not rename one."""
    result = replace(str(module), "foo", "def renamed():\n    return 99", backup=False)

    assert result["status"] == "error"
    assert "does not define 'foo'" in result["error"]
    assert module.read_text(encoding="utf-8") == MODULE


def test_a_missing_function_is_still_reported_as_not_found(replace, module):
    result = replace(str(module), "nope", "def nope(): pass", backup=False)

    assert result["status"] == "error"
    assert "not found" in result["error"]
    assert module.read_text(encoding="utf-8") == MODULE


def test_the_backup_holds_the_original_without_a_path_validator(replace, module):
    """The manual ``.bak`` path is the one every un-validated agent takes."""
    result = replace(str(module), "foo", "def foo():\n    return 99", backup=True)

    backup = module.parent / f"{module.name}.bak"
    assert result["backup_path"] == str(backup)
    assert backup.read_text(encoding="utf-8") == MODULE


def test_replacing_the_last_function_keeps_everything_above_it(replace, module):
    replace(str(module), "run", 'def run():\n    return "last"', backup=False)
    after = module.read_text(encoding="utf-8")

    assert "CONSTANT = 42" in after
    assert "def bar():" in after
    assert "class Runner:" in after
    assert after.endswith('return "last"\n')
