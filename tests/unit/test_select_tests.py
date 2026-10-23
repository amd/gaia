# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/select_tests.py picks every test a change can reach, and no fewer.

A test the selector leaves out is a bug that merges green, so each way a test
can depend on a file gets a case here, built in a throwaway repo.
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "util"))

import select_tests as st  # noqa: E402

FILES = {
    "src/gaia/__init__.py": "from gaia.core import base\n",
    "src/gaia/core/__init__.py": "",
    "src/gaia/core/base.py": "VALUE = 1\n",
    "src/gaia/core/consts.py": "LIMIT = 3\n",
    "src/gaia/core/home.py": "from gaia.core.consts import LIMIT\n",
    "src/gaia/core/uses_consts.py": "from gaia.core.consts import LIMIT\n",
    "src/gaia/cli.py": "def run():\n    from gaia.tools import sinks\n    sinks.go()\n",
    "src/gaia/tools/__init__.py": "",
    "src/gaia/tools/sinks.py": "import subprocess\ndef go():\n    pass\n",
    "src/gaia/tools/reader.py": (
        "from pathlib import Path\n"
        "def tiers():\n    return Path(__file__).parent / 'tiers.json'\n"
    ),
    "src/gaia/tools/tiers.json": "{}",
    "util/check_docs.py": (
        "from pathlib import Path\n"
        "def pages():\n    return list(Path('docs').rglob('*.mdx'))\n"
    ),
    "docs/guides/chat.mdx": "# chat",
    "README.md": "readme",
    "tests/__init__.py": "",
    "tests/conftest.py": textwrap.dedent("""
        import pytest

        @pytest.fixture
        def ui_client():
            from gaia.tools.reader import tiers
            return tiers

        @pytest.fixture
        def app(ui_client):
            return ui_client

        @pytest.fixture(autouse=True)
        def _isolate():
            from gaia.core import home
            return home
        """),
    "tests/unit/__init__.py": "",
    "tests/unit/sub/conftest.py": "from gaia.tools.sinks import go\n",
    "tests/unit/sub/test_in_sub.py": "def test_x():\n    pass\n",
    "tests/unit/test_base.py": "from gaia.core.base import VALUE\n",
    "tests/unit/test_consts.py": "from gaia.core.uses_consts import LIMIT\n",
    "tests/unit/test_cli.py": "def test_cli():\n    from gaia import cli\n",
    "tests/unit/test_patch.py": (
        "def test_p(mocker):\n    mocker.patch('gaia.tools.sinks.go')\n"
    ),
    "tests/unit/test_dash_c.py": ("CODE = 'import sys, gaia.core.consts; print(1)'\n"),
    "tests/unit/test_reads_json.py": "NAME = 'tiers.json'\n",
    "tests/unit/test_docs_tool.py": "import check_docs\n",
    "tests/unit/test_fixture.py": "def test_f(ui_client):\n    pass\n",
    "tests/unit/test_unrelated.py": "def test_u():\n    pass\n",
    "tests/unit/test_proc.py": "import subprocess\nfrom gaia.tools.sinks import go\n",
    "tests/unit/test_chain.py": "def test_c(app):\n    pass\n",
    "tests/unit/test_cli_proc.py": (
        "import subprocess, sys\n"
        "def test_c():\n    subprocess.run([sys.executable, '-m', 'gaia.cli'])\n"
    ),
}


@pytest.fixture
def graph(tmp_path):
    for rel, text in FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return st.build_graph(repo=tmp_path, files=sorted(FILES))


def _selected(graph, *changed, coverage=None):
    sel = st.select(graph, changed, coverage)
    assert not sel.full, sel.reason
    return set(sel.tests)


def test_a_direct_import_selects_its_test(graph):
    assert "tests/unit/test_base.py" in _selected(graph, "src/gaia/core/base.py")


def test_module_level_imports_are_followed_transitively(graph):
    hit = _selected(graph, "src/gaia/core/consts.py")
    assert "tests/unit/test_consts.py" in hit


def test_a_test_imports_inside_its_functions_count(graph):
    assert "tests/unit/test_cli.py" in _selected(graph, "src/gaia/cli.py")


def test_a_patch_target_string_is_a_dependency(graph):
    assert "tests/unit/test_patch.py" in _selected(graph, "src/gaia/tools/sinks.py")


def test_a_module_named_inside_code_in_a_string_is_a_dependency(graph):
    assert "tests/unit/test_dash_c.py" in _selected(graph, "src/gaia/core/consts.py")


def test_a_file_named_by_a_string_is_read_by_its_namer(graph):
    assert "tests/unit/test_reads_json.py" in _selected(
        graph, "src/gaia/tools/tiers.json"
    )


def test_a_directory_scan_selects_tests_of_the_scanner(graph):
    assert "tests/unit/test_docs_tool.py" in _selected(graph, "docs/guides/chat.mdx")


def test_a_nested_conftest_reaches_every_test_below_it(graph):
    assert "tests/unit/sub/test_in_sub.py" in _selected(
        graph, "src/gaia/tools/sinks.py"
    )


def test_a_top_level_fixture_links_only_the_tests_that_request_it(graph):
    hit = _selected(graph, "src/gaia/tools/reader.py")
    assert "tests/unit/test_fixture.py" in hit
    assert "tests/unit/test_unrelated.py" not in hit


def test_a_fixture_requested_through_another_fixture_is_followed(graph):
    assert "tests/unit/test_chain.py" in _selected(graph, "src/gaia/tools/reader.py")


def test_a_module_an_autouse_fixture_imports_reaches_every_test(graph):
    sel = st.select(graph, ["src/gaia/core/home.py"])
    assert "tests/unit/test_unrelated.py" in sel.tests


def test_an_autouse_fixture_reaches_only_one_hop(graph):
    # home imports consts, but consts reaches only its own tests.
    assert "tests/unit/test_unrelated.py" not in _selected(
        graph, "src/gaia/core/consts.py"
    )


def test_a_cli_run_in_a_child_process_follows_the_clis_lazy_imports(graph):
    # Coverage can't see inside the child, so the map alone would miss this.
    mapped = _selected(graph, "src/gaia/tools/sinks.py", coverage={})
    assert "tests/unit/test_cli_proc.py" in mapped


def test_a_package_init_change_selects_everything_beneath_it(graph):
    hit = _selected(graph, "src/gaia/core/__init__.py")
    assert {"tests/unit/test_base.py", "tests/unit/test_consts.py"} <= hit


def test_the_walk_does_not_continue_through_an_eager_package_init(graph):
    # gaia/__init__ imports gaia.core.base, but a test of gaia.tools does not
    # depend on base just because importing gaia runs that __init__.
    assert "tests/unit/test_patch.py" not in _selected(graph, "src/gaia/core/base.py")


def test_function_level_imports_in_shipped_code_need_the_map_to_be_skipped(graph):
    # cli.run() imports sinks lazily. Without a map that is followed, so a
    # test of cli is selected for a sinks change; with a map it is left to it.
    assert "tests/unit/test_cli.py" in _selected(graph, "src/gaia/tools/sinks.py")
    mapped = _selected(graph, "src/gaia/tools/sinks.py", coverage={})
    assert "tests/unit/test_cli.py" not in mapped


def test_the_coverage_map_adds_tests_that_executed_an_affected_file(graph):
    coverage = {"src/gaia/core/uses_consts.py": {"tests/unit/test_unrelated.py"}}
    # consts -> uses_consts (module level) -> the map's test of uses_consts.
    hit = _selected(graph, "src/gaia/core/consts.py", coverage=coverage)
    assert "tests/unit/test_unrelated.py" in hit


def test_an_unreferenced_file_selects_nothing(graph):
    assert _selected(graph, "README.md") == set()


@pytest.mark.parametrize(
    "changed",
    [
        "tests/conftest.py",
        "setup.py",
        "pyproject.toml",
        "util/select_tests.py",
        ".github/workflows/test_unit.yml",
        "src/gaia/gone.py",
        "src/gaia/tools/old_tiers.json",
    ],
)
def test_changes_whose_reach_cannot_be_read_run_the_full_suite(graph, changed):
    sel = st.select(graph, [changed])
    assert sel.full and len(sel.tests) == sel.total


def test_platform_only_keeps_tests_that_touch_the_os(graph):
    sel = st.select(graph, ["src/gaia/tools/sinks.py"], platform_only=True)
    assert "tests/unit/test_proc.py" in sel.tests
    assert "tests/unit/test_patch.py" not in sel.tests
