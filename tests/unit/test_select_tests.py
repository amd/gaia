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
    "src/gaia/daemon_client.py": (
        "import subprocess, sys\n"
        "def start():\n"
        "    subprocess.Popen([sys.executable, '-m', 'gaia.daemon_server'])\n"
    ),
    "src/gaia/daemon_server.py": "def main():\n    from gaia.core import consts\n",
    "util/check_docs.py": (
        "from pathlib import Path\n"
        "def pages():\n    return list(Path('docs').rglob('*.mdx'))\n"
    ),
    "docs/guides/chat.mdx": "# chat",
    "README.md": "readme",
    "docs/README.md": "docs readme",
    "hub/skills/triage/SKILL.md": "---\nname: triage\n---\n",
    # More SKILL.md files than MAX_BASENAME_FANOUT, so the bare name says nothing.
    **{f"hub/skills/s{i}/SKILL.md": "" for i in range(30)},
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
    "tests/unit/test_skill_chain.py": (
        "from pathlib import Path\n"
        "PATH = Path(__file__).parents[2] / 'hub' / 'skills' / 'triage' / 'SKILL.md'\n"
    ),
    "tests/unit/test_skill_literal.py": (
        "from pathlib import Path\n"
        "PATH = Path(__file__).parents[2] / 'hub/skills/triage/SKILL.md'\n"
    ),
    "tests/unit/test_skill_join.py": (
        "import os\n"
        "PATH = os.path.join(ROOT, 'hub', 'skills', 'triage', 'SKILL.md')\n"
    ),
    "tests/unit/test_any_skill.py": (
        "def read(name):\n    return ROOT / name / 'SKILL.md'\n"
    ),
    "tests/unit/test_docs_readme.py": "PATH = ROOT / 'docs' / 'README.md'\n",
    "tests/unit/test_other_skill.py": (
        "PATH = ROOT / 'hub' / 'skills' / 's1' / 'SKILL.md'\n"
    ),
    # The two shapes the real email tests use to read the inbox-triage skill.
    "hub/skills/inbox-triage/SKILL.md": "---\nname: inbox-triage\n---\n",
    "hub/skills/inbox-triage/references/guide.md": "guide",
    "hub/skills/inbox-triage/references/unread.md": "nobody names me",
    "tests/unit/test_email_tools_like.py": (
        "def test_t(root):\n"
        "    path = root / 'hub' / 'skills' / 'inbox-triage' / 'SKILL.md'\n"
    ),
    "tests/unit/test_phishing_like.py": (
        "from pathlib import Path\n"
        "def test_p():\n"
        "    skill = (\n"
        "        Path(__file__).resolve().parents[4] / "
        "'hub/skills/inbox-triage/SKILL.md'\n"
        "    ).read_text()\n"
    ),
    "tests/unit/test_skill_refs.py": (
        "PATH = ROOT / 'hub' / 'skills' / 'inbox-triage' / 'references' / 'guide.md'\n"
    ),
    "tests/fixtures/data/cases.json": "[]",
    "tests/fixtures/other/cases.json": "[]",
    "tests/unit/test_cases.py": (
        "FIX = ROOT / 'tests' / 'fixtures' / 'data' / 'cases.json'\n"
    ),
    # A bare name shared by two files says nothing about which one.
    "tests/unit/test_bare_cases.py": "NAME = 'cases.json'\n",
    "docs/guides/talk.mdx": "# talk",
    "tests/unit/test_chat_doc.py": "DOC = ROOT / 'docs/guides/chat.mdx'\n",
    "eval/scenarios/rag/basic.yaml": "id: basic",
    "eval/scenarios/rag/other.yaml": "id: other",
    "tests/unit/test_scenarios.py": (
        "BASIC = ROOT / 'eval' / 'scenarios' / 'rag' / 'basic.yaml'\n"
        "ALL = list((ROOT / 'eval' / 'scenarios').rglob('*.yaml'))\n"
    ),
    "tests/unit/test_proc.py": "import subprocess\nfrom gaia.tools.sinks import go\n",
    "tests/unit/test_chain.py": "def test_c(app):\n    pass\n",
    "tests/unit/test_daemon_start.py": "def test_s():\n    pass\n",
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
    # chat.mdx is named by a test, so the selection is precise, and the
    # scanner that lists docs/ comes along.
    hit = _selected(graph, "docs/guides/chat.mdx")
    assert {"tests/unit/test_docs_tool.py", "tests/unit/test_chat_doc.py"} <= hit


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
    # test of cli is selected for a sinks change; a map that ran test_cli and
    # saw sinks without it leaves it out.
    assert "tests/unit/test_cli.py" in _selected(graph, "src/gaia/tools/sinks.py")
    coverage = {
        "src/gaia/cli.py": {"tests/unit/test_cli.py"},
        "src/gaia/tools/sinks.py": {"tests/unit/test_patch.py"},
    }
    mapped = _selected(graph, "src/gaia/tools/sinks.py", coverage=coverage)
    assert "tests/unit/test_cli.py" not in mapped


# The map is a nightly snapshot. Where it has nothing to say about a file, the
# selection must widen to the no-map rule, never narrow to what it does know.
_KNOWS_CLI_AND_SINKS = {
    "src/gaia/cli.py": {"tests/unit/test_patch.py"},
    "src/gaia/tools/sinks.py": {"tests/unit/test_patch.py"},
}


def test_a_test_the_map_never_ran_follows_lazy_imports(graph):
    # test_cli was added after the nightly run, so the map can't vouch for it.
    hit = _selected(graph, "src/gaia/tools/sinks.py", coverage=_KNOWS_CLI_AND_SINKS)
    assert "tests/unit/test_cli.py" in hit


def test_a_source_file_the_map_never_saw_follows_lazy_imports(graph):
    # sinks.py is new since the map (or never ran): cli imports it lazily, and
    # the map's test of cli can't have been credited with it.
    coverage = {"src/gaia/cli.py": {"tests/unit/test_cli.py"}}
    hit = _selected(graph, "src/gaia/tools/sinks.py", coverage=coverage)
    assert "tests/unit/test_cli.py" in hit


def test_a_stale_map_follows_lazy_imports_everywhere(graph):
    coverage = {
        "src/gaia/cli.py": {"tests/unit/test_cli.py"},
        "src/gaia/tools/sinks.py": {"tests/unit/test_patch.py"},
    }
    sel = st.select(graph, ["src/gaia/tools/sinks.py"], coverage, stale=True)
    assert not sel.full
    assert "tests/unit/test_cli.py" in sel.tests
    assert "stale" in sel.reason


def test_a_child_module_shipped_code_spawns_selects_the_tests_that_ran_it(graph):
    # daemon_client starts `python -m gaia.daemon_server`; coverage credits the
    # server to nobody, so the tests that ran the client stand in for it.
    coverage = {
        "src/gaia/daemon_client.py": {"tests/unit/test_daemon_start.py"},
        "src/gaia/daemon_server.py": {"tests/unit/test_unrelated.py"},
    }
    hit = _selected(graph, "src/gaia/daemon_server.py", coverage=coverage)
    assert "tests/unit/test_daemon_start.py" in hit


def test_a_spawned_childs_lazy_imports_count_too(graph):
    # The server imports consts inside main(), in the child coverage can't see.
    coverage = {
        "src/gaia/daemon_client.py": {"tests/unit/test_daemon_start.py"},
        "src/gaia/core/consts.py": {"tests/unit/test_consts.py"},
    }
    hit = _selected(graph, "src/gaia/core/consts.py", coverage=coverage)
    assert "tests/unit/test_daemon_start.py" in hit


def test_a_mapped_test_that_reaches_nothing_stays_out(graph):
    coverage = {
        "src/gaia/daemon_client.py": {"tests/unit/test_daemon_start.py"},
        "src/gaia/tools/sinks.py": {"tests/unit/test_patch.py"},
        "src/gaia/cli.py": {"tests/unit/test_cli.py"},
    }
    hit = _selected(graph, "src/gaia/tools/sinks.py", coverage=coverage)
    assert "tests/unit/test_daemon_start.py" not in hit


@pytest.mark.parametrize("days, stale", [(0, False), (3, False), (4, True)])
def test_load_map_flags_a_map_the_nightly_stopped_refreshing(tmp_path, days, stale):
    import json
    from datetime import datetime, timedelta, timezone

    built = datetime.now(timezone.utc) - timedelta(days=days, minutes=-1)
    path = tmp_path / "map.json"
    path.write_text(
        json.dumps(
            {
                "version": st.MAP_VERSION,
                "built_at": built.isoformat(),
                "tests": ["tests/unit/test_a.py"],
                "files": {"src/gaia/a.py": [0]},
            }
        ),
        encoding="utf-8",
    )
    files, is_stale = st.load_map(path)
    assert files == {"src/gaia/a.py": {"tests/unit/test_a.py"}}
    assert is_stale is stale


def test_the_coverage_map_adds_tests_that_executed_an_affected_file(graph):
    coverage = {"src/gaia/core/uses_consts.py": {"tests/unit/test_unrelated.py"}}
    # consts -> uses_consts (module level) -> the map's test of uses_consts.
    hit = _selected(graph, "src/gaia/core/consts.py", coverage=coverage)
    assert "tests/unit/test_unrelated.py" in hit


@pytest.mark.parametrize(
    "test",
    [
        "tests/unit/test_skill_chain.py",
        "tests/unit/test_skill_literal.py",
        "tests/unit/test_skill_join.py",
    ],
)
def test_a_common_file_name_is_selected_by_its_full_path(graph, test):
    assert test in _selected(graph, "hub/skills/triage/SKILL.md")
    assert test not in _selected(graph, "hub/skills/s1/SKILL.md")


def test_a_common_name_with_an_unknown_folder_reads_every_file_of_that_name(graph):
    assert "tests/unit/test_any_skill.py" in _selected(graph, "hub/skills/s1/SKILL.md")


def test_an_ignored_name_is_still_a_read_when_its_path_is_spelled_out(graph):
    assert "tests/unit/test_docs_readme.py" in _selected(graph, "docs/README.md")


@pytest.mark.parametrize(
    "test", ["tests/unit/test_email_tools_like.py", "tests/unit/test_phishing_like.py"]
)
def test_the_inbox_triage_skill_selects_the_email_tests_that_read_it(graph, test):
    # The regression: these two were skipped when only SKILL.md was edited.
    hit = _selected(graph, "hub/skills/inbox-triage/SKILL.md")
    assert test in hit
    assert "tests/unit/test_unrelated.py" not in hit


@pytest.mark.parametrize(
    "changed, reader",
    [
        ("hub/skills/inbox-triage/references/guide.md", "test_skill_refs.py"),
        ("tests/fixtures/data/cases.json", "test_cases.py"),
        ("eval/scenarios/rag/basic.yaml", "test_scenarios.py"),
        ("docs/guides/chat.mdx", "test_chat_doc.py"),
    ],
)
def test_a_data_file_named_by_its_path_selects_its_reader(graph, changed, reader):
    assert f"tests/unit/{reader}" in _selected(graph, changed)


# Coverage records only .py files, so a data file nothing names exactly could be
# read by anything: run everything rather than guess.
@pytest.mark.parametrize(
    "changed",
    [
        "README.md",
        "hub/skills/s2/SKILL.md",
        "hub/skills/inbox-triage/references/unread.md",
        "tests/fixtures/other/cases.json",
        "eval/scenarios/rag/other.yaml",
        "docs/guides/talk.mdx",
    ],
)
def test_a_data_file_no_string_names_exactly_runs_the_full_suite(graph, changed):
    sel = st.select(graph, [changed])
    assert sel.full and len(sel.tests) == sel.total
    assert "not named exactly" in sel.reason


def test_one_unattributed_data_file_among_precise_ones_runs_everything(graph):
    sel = st.select(
        graph, ["hub/skills/inbox-triage/SKILL.md", "hub/skills/s2/SKILL.md"]
    )
    assert sel.full


def test_a_renamed_data_file_runs_the_full_suite(graph):
    # --no-renames: the old path shows as deleted, whatever its new name.
    sel = st.select(
        graph, ["tests/fixtures/data/old_cases.json", "tests/fixtures/data/cases.json"]
    )
    assert sel.full and "deleted or renamed" in sel.reason


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
