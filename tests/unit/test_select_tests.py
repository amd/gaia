# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/select_tests.py picks every test a change can reach, and no fewer.

A test the selector leaves out is a bug that merges green, so each way a test
can depend on a file gets a case here, built in a throwaway repo.
"""

import re
import subprocess
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
    # Each spawns something, and names a module in a string it never runs.
    "src/gaia/spawn_prefix.py": "import subprocess\nLEVELS = 'gaia.core.levels'\n",
    "src/gaia/spawn_url.py": (
        "import subprocess\nDOCS = 'https://example.com/?m=gaia.core.consts'\n"
    ),
    "src/gaia/spawn_logger.py": (
        "import logging, subprocess\nLOG = logging.getLogger('gaia.core.home')\n"
    ),
    # A level table's keys are logger names; a plain dict's are not.
    "src/gaia/spawn_levels.py": (
        "import logging, subprocess\n"
        "LEVELS = {'gaia.core.home': logging.INFO, 'gaia.core.base': 'DEBUG'}\n"
        "LEVELS['gaia.tools.sinks'] = logging.ERROR\n"
        "ROUTES = {'gaia.core.consts': 'run'}\n"
    ),
    # Spawns something, and names the server only where nothing runs it.
    "src/gaia/daemon_hint.py": (
        '"""Pairs with ``python -m gaia.daemon_server``."""\n'
        "import subprocess\n"
        "def open_logs():\n"
        '    """Logs land where gaia.daemon_server:main writes them."""\n'
        "    subprocess.run(['open', 'logs'])\n"
    ),
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
    st.CONFTEST_SMOKE_TEST: "",
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
    # Scripts and data that tests load by path, where neither imports nor
    # coverage can follow what the script imports or reads.
    "eval/ci_lanes.json": "{}",
    "util/eval_lane_matrix.py": "import json\n",
    "util/eval_gaia_home.py": "import os\n",
    "util/run_eval_lane.py": (
        "import eval_lane_matrix, eval_gaia_home\n" "LANES = 'eval/ci_lanes.json'\n"
    ),
    "tests/unit/eval/test_run_eval_lane.py": (
        "import importlib.util\n"
        "SPEC = importlib.util.spec_from_file_location(\n"
        "    'run_eval_lane', ROOT / 'util' / 'run_eval_lane.py'\n"
        ")\n"
    ),
    "tests/fixtures/email/phishing_fixture.json": "[]",
    "tests/fixtures/email/drafting_gate_thresholds.json": "{}",
    "tests/fixtures/email/perf_gate_thresholds.json": "{}",
    "tests/fixtures/gaia/email/build_mailbox.py": (
        "FIXTURE = ROOT / 'tests/fixtures/email/phishing_fixture.json'\n"
    ),
    "tests/unit/agents/tools/test_email_eval_fixture.py": (
        "import importlib.util\n"
        "SPEC = importlib.util.spec_from_file_location(\n"
        "    'build_mailbox', ROOT / 'tests/fixtures/gaia/email/build_mailbox.py'\n"
        ")\n"
    ),
    "tests/unit/helpers.py": "from gaia.core.base import VALUE\n",
    "tests/integration/test_live.py": "def test_l():\n    pass\n",
    "hub/agents/mail/python/gaia_agent_mail/__init__.py": "",
    "hub/agents/mail/python/gaia_agent_mail/agent.py": "X = 1\n",
    "hub/agents/mail/python/tests/test_agent.py": "def test_a():\n    pass\n",
    "tests/unit/test_mail_agent.py": "from gaia_agent_mail.agent import X\n",
    "tests/unit/test_map_anchor.py": "def test_a():\n    pass\n",
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


# A map with an entry for every shipped .py file, crediting only an anchor
# test: every other test is one the map never ran, so its imports are read
# statically, as the rule tests below expect.
ANCHOR = "tests/unit/test_map_anchor.py"
FULL_MAP = {f: {ANCHOR} for f in FILES if f.endswith(".py") and st.is_measured(f, {})}


def _selected(graph, *changed, coverage=None):
    sel = st.select(graph, changed, FULL_MAP if coverage is None else coverage)
    assert not sel.full, sel.reason
    return set(sel.tests)


def _reach(graph, *changed):
    """The tests the static rules link to a change, before the allowlist."""
    return {f for f in st.affected_files(graph, changed, None) if "/test_" in f}


def _full(graph, *changed, coverage=FULL_MAP, stale=False):
    sel = st.select(graph, changed, coverage, stale=stale)
    assert sel.full and len(sel.tests) == sel.total, sel.reason
    return sel.reason


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
    assert "tests/unit/test_reads_json.py" in _reach(graph, "src/gaia/tools/tiers.json")


def test_a_directory_scan_selects_tests_of_the_scanner(graph):
    # chat.mdx is named by a test, so the selection is precise, and the
    # scanner that lists docs/ comes along.
    hit = _reach(graph, "docs/guides/chat.mdx")
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


def test_a_change_to_a_conftest_import_adds_the_smoke_test(graph, tmp_path):
    # reader is imported only inside a non-autouse fixture body, so no static
    # or map link reaches the smoke test; the guard adds it.
    narrow = {"src/gaia/tools/reader.py": {ANCHOR}}
    assert st.CONFTEST_SMOKE_TEST in _selected(
        graph, "src/gaia/tools/reader.py", coverage=narrow
    )
    assert st.CONFTEST_SMOKE_TEST not in _selected(
        graph, "src/gaia/tools/sinks.py", coverage={"src/gaia/tools/sinks.py": {ANCHOR}}
    )
    (tmp_path / st.CONFTEST_SMOKE_TEST).unlink()
    gone = st.build_graph(
        repo=tmp_path, files=sorted(set(FILES) - {st.CONFTEST_SMOKE_TEST})
    )
    with pytest.raises(SystemExit, match="CONFTEST_SMOKE_TEST"):
        st.select(gone, ["src/gaia/tools/reader.py"], narrow)


def test_a_module_an_autouse_fixture_imports_reaches_every_test(graph):
    sel = st.select(graph, ["src/gaia/core/home.py"], FULL_MAP)
    assert "tests/unit/test_unrelated.py" in sel.tests


def test_an_autouse_fixture_reaches_only_one_hop(graph):
    # home imports consts, but consts reaches only its own tests.
    assert "tests/unit/test_unrelated.py" not in _selected(
        graph, "src/gaia/core/consts.py"
    )


def test_a_cli_run_in_a_child_process_follows_the_clis_lazy_imports(graph):
    # Coverage can't see inside the child, so the map alone would miss this.
    coverage = {"src/gaia/tools/sinks.py": {"tests/unit/test_patch.py"}}
    mapped = _selected(graph, "src/gaia/tools/sinks.py", coverage=coverage)
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


def test_a_source_file_the_map_never_saw_selects_by_its_imports(graph):
    # sinks.py is new since the map, so its importers stand in for coverage.
    coverage = {"src/gaia/cli.py": {"tests/unit/test_cli.py"}}
    sel = st.select(graph, ["src/gaia/tools/sinks.py"], coverage)
    assert not sel.full and "src/gaia/tools/sinks.py" in sel.reason
    assert {"tests/unit/test_patch.py", "tests/unit/test_proc.py"} <= set(sel.tests)
    assert "tests/unit/test_unrelated.py" not in sel.tests
    # A deleted one may still be named by a test, so it runs everything.
    assert "deleted or renamed" in _full(
        graph, "src/gaia/tools/gone.py", coverage=coverage
    )


def test_a_stale_map_runs_the_full_suite(graph):
    assert "stale" in _full(graph, "src/gaia/tools/sinks.py", stale=True)


def test_no_map_runs_the_full_suite(graph):
    assert "no coverage map" in _full(graph, "src/gaia/tools/sinks.py", coverage=None)


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


def test_a_module_named_only_in_a_docstring_is_not_a_spawned_child(graph):
    # Both files spawn; only daemon_client starts the server.
    coverage = {
        "src/gaia/daemon_client.py": {"tests/unit/test_daemon_start.py"},
        "src/gaia/daemon_hint.py": {"tests/unit/test_unrelated.py"},
        "src/gaia/daemon_server.py": {"tests/unit/test_patch.py"},
    }
    hit = _selected(graph, "src/gaia/daemon_server.py", coverage=coverage)
    assert "tests/unit/test_daemon_start.py" in hit
    assert "tests/unit/test_unrelated.py" not in hit


@pytest.mark.parametrize(
    "path, child",
    [
        ("src/gaia/spawn_prefix.py", None),
        ("src/gaia/spawn_url.py", None),
        ("src/gaia/spawn_logger.py", None),
        ("src/gaia/spawn_levels.py", "src/gaia/core/consts.py"),
        ("src/gaia/daemon_client.py", "src/gaia/daemon_server.py"),
    ],
)
def test_only_a_whole_module_name_in_running_code_names_a_child(graph, path, child):
    assert graph.spawn_deps.get(path, set()) == ({child} if child else set())


def test_a_mapped_test_that_reaches_nothing_stays_out(graph):
    coverage = {
        "src/gaia/daemon_client.py": {"tests/unit/test_daemon_start.py"},
        "src/gaia/tools/sinks.py": {"tests/unit/test_patch.py"},
        "src/gaia/cli.py": {"tests/unit/test_cli.py"},
    }
    hit = _selected(graph, "src/gaia/tools/sinks.py", coverage=coverage)
    assert "tests/unit/test_daemon_start.py" not in hit


def test_build_map_ignores_suite_wide_fixture_setup(tmp_path):
    from coverage import CoverageData

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    mod = repo / "src" / "mod.py"
    mod.write_text("\n" * 5, encoding="utf-8")
    suite = [f"tests/unit/test_{n}.py" for n in "abcde"]

    cov = CoverageData(basename=str(tmp_path / ".coverage"))
    for test in suite:
        cov.set_context(f"{test}::test_x|setup")
        cov.add_lines({str(mod): [1]})  # autouse fixture: runs for every test
    cov.set_context(f"{suite[0]}::test_x|run")
    cov.add_lines({str(mod): [2]})
    cov.set_context(f"{suite[1]}::test_x|setup")
    cov.add_lines({str(mod): [3]})  # a fixture only some tests request
    cov.write()

    built = st.build_map(tmp_path / ".coverage", repo=repo)
    reached = {built["tests"][i] for i in built["files"]["src/mod.py"]}
    assert reached == {suite[0], suite[1]}


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
    coverage = {
        "src/gaia/core/consts.py": {"tests/unit/test_consts.py"},
        "src/gaia/core/uses_consts.py": {"tests/unit/test_unrelated.py"},
    }
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
    assert test in _reach(graph, "hub/skills/triage/SKILL.md")
    assert test not in _reach(graph, "hub/skills/s1/SKILL.md")


def test_a_common_name_with_an_unknown_folder_reads_every_file_of_that_name(graph):
    assert "tests/unit/test_any_skill.py" in _reach(graph, "hub/skills/s1/SKILL.md")


def test_an_ignored_name_is_still_a_read_when_its_path_is_spelled_out(graph):
    assert "tests/unit/test_docs_readme.py" in _reach(graph, "docs/README.md")


@pytest.mark.parametrize(
    "test", ["tests/unit/test_email_tools_like.py", "tests/unit/test_phishing_like.py"]
)
def test_the_inbox_triage_skill_selects_the_email_tests_that_read_it(graph, test):
    # The regression: these two were skipped when only SKILL.md was edited.
    hit = _reach(graph, "hub/skills/inbox-triage/SKILL.md")
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
    assert f"tests/unit/{reader}" in _reach(graph, changed)


# Narrowing is an allowlist: only unit tests and mapped shipped .py code may
# narrow. Anything a test can load by path, or a script can read, runs it all.
@pytest.mark.parametrize(
    "changed",
    [
        # The verifier's cases: each reaches a test only through a script that
        # a test loads with spec_from_file_location.
        "eval/ci_lanes.json",
        "util/eval_lane_matrix.py",
        "util/eval_gaia_home.py",
        "tests/fixtures/email/phishing_fixture.json",
        "tests/fixtures/email/drafting_gate_thresholds.json",
        "tests/fixtures/email/perf_gate_thresholds.json",
        # A util/ script, a test helper and a fixture script that aren't tests.
        "util/run_eval_lane.py",
        "tests/unit/helpers.py",
        "tests/fixtures/gaia/email/build_mailbox.py",
        # Data files, named exactly or not. Skills stay here: shipped code
        # loads them by scanning a folder, which the graph can't follow.
        "hub/skills/inbox-triage/SKILL.md",
        "hub/skills/s2/SKILL.md",
        "hub/skills/inbox-triage/references/guide.md",
        "tests/fixtures/data/cases.json",
        "eval/scenarios/rag/basic.yaml",
        "src/gaia/tools/tiers.json",
        # .py files outside what the map measures.
        "tests/integration/test_live.py",
        "hub/agents/mail/python/tests/test_agent.py",
    ],
)
def test_a_change_outside_the_allowlist_runs_the_full_suite(graph, changed):
    _full(graph, changed)


def test_the_verifiers_cases_reach_their_tests_only_through_the_full_suite(graph):
    # Static reading misses these tests: proof the allowlist, not a path
    # rule, is what keeps them.
    assert "tests/unit/eval/test_run_eval_lane.py" not in _reach(
        graph, "eval/ci_lanes.json"
    )
    for changed, test in [
        ("eval/ci_lanes.json", "tests/unit/eval/test_run_eval_lane.py"),
        (
            "tests/fixtures/email/phishing_fixture.json",
            "tests/unit/agents/tools/test_email_eval_fixture.py",
        ),
    ]:
        assert test in st.select(graph, [changed], FULL_MAP).tests


def test_one_change_outside_the_allowlist_runs_everything(graph):
    # A mapped source file and a test pass; the util script beside them doesn't.
    sel = st.select(
        graph,
        ["src/gaia/core/base.py", "tests/unit/test_base.py", "util/eval_gaia_home.py"],
        FULL_MAP,
    )
    assert sel.full and "util/eval_gaia_home.py" in sel.reason


def test_mapped_source_and_unit_tests_together_narrow(graph):
    hit = _selected(graph, "src/gaia/core/base.py", "tests/unit/test_unrelated.py")
    assert {"tests/unit/test_base.py", "tests/unit/test_unrelated.py"} <= hit


def test_a_hub_agent_narrows_only_when_the_map_measured_hub_agents(graph):
    agent = "hub/agents/mail/python/gaia_agent_mail/agent.py"
    src_only = {k: v for k, v in FULL_MAP.items() if k.startswith("src/")}
    assert "outside" in _full(graph, agent, coverage=src_only)
    with_hub = {**src_only, agent: {"tests/unit/test_mail_agent.py"}}
    assert "tests/unit/test_mail_agent.py" in _selected(graph, agent, coverage=with_hub)


def test_a_renamed_data_file_runs_the_full_suite(graph):
    # --no-renames: the old path shows as deleted, whatever its new name.
    _full(graph, "tests/fixtures/data/old_cases.json", "tests/fixtures/data/cases.json")


def test_a_renamed_source_file_runs_the_full_suite(graph):
    reason = _full(graph, "src/gaia/tools/old_sinks.py", "src/gaia/tools/sinks.py")
    assert "deleted or renamed" in reason


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
    _full(graph, changed)


def test_platform_only_keeps_tests_that_touch_the_os(graph):
    sel = st.select(graph, ["src/gaia/tools/sinks.py"], FULL_MAP, platform_only=True)
    assert "tests/unit/test_proc.py" in sel.tests
    assert "tests/unit/test_patch.py" not in sel.tests


# The merge queue has no paths filter, so the selection applies the workflow's.
REPO = Path(__file__).resolve().parents[2]
UNIT_WORKFLOW = REPO / ".github/workflows/test_unit.yml"


@pytest.mark.parametrize(
    "path, matches",
    [
        ("src/gaia/cli.py", True),
        ("src/gaia/deep/nested/mod.py", True),
        ("hub/agents/mail/python/pkg/agent.py", True),
        # `*` is one path segment.
        ("hub/agents/mail/extra/python/pkg/agent.py", False),
        ("hub/components/x/gaia-agent.yaml", True),
        ("hub/components/x/y/gaia-agent.yaml", False),
        # An exact pattern is the whole path, not a prefix.
        ("docs/docs.json", True),
        ("docs/docs.json.bak", False),
        ("README.md", False),
    ],
)
def test_path_filter_reads_globs_as_github_does(path, matches):
    rx = st.path_filter(
        [
            "src/**",
            "hub/agents/*/python/**",
            "hub/components/*/gaia-agent.yaml",
            "docs/docs.json",
        ]
    )
    assert bool(rx.match(path)) is matches


@pytest.mark.parametrize("pattern", ["!src/**", "src/?.py", "src/[ab].py", "a+"])
def test_path_filter_refuses_syntax_it_cannot_read(pattern):
    with pytest.raises(SystemExit, match="filter syntax"):
        st.path_filter([pattern])


def test_a_workflow_without_a_paths_filter_is_refused(tmp_path):
    wf = tmp_path / "wf.yml"
    wf.write_text("on:\n  pull_request:\n  merge_group:\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="no on.pull_request.paths"):
        st.workflow_trigger_paths(wf)


def test_the_unit_workflow_filter_is_read_from_the_real_file():
    paths = st.workflow_trigger_paths(UNIT_WORKFLOW)
    assert "src/**" in paths and "conftest.py" in paths
    # Every full-run file the workflow could see is inside its own filter, so
    # the filter alone never hides one; forces_full_run() covers the rest.
    rx = st.path_filter(paths)
    for path in ("util/select_tests.py", ".github/workflows/test_unit.yml"):
        assert rx.match(path)


@pytest.mark.parametrize(
    "changed",
    [
        ["README.md"],
        [
            "docs/guides/not-a-unit-test-input.mdx",
            "website/src/pages/not-a-unit-test-input.astro",
        ],
    ],
)
def test_a_merge_group_outside_the_filter_runs_nothing(graph, changed):
    sel = st.select(
        graph, changed, FULL_MAP, trigger_paths=st.workflow_trigger_paths(UNIT_WORKFLOW)
    )
    assert not sel.triggered and not sel.full and sel.tests == []
    platform = st.select(
        graph,
        changed,
        FULL_MAP,
        platform_only=True,
        trigger_paths=st.workflow_trigger_paths(UNIT_WORKFLOW),
    )
    assert not platform.triggered and platform.tests == []


def test_a_group_that_runs_keeps_every_file_it_changed(graph):
    # The filter decides only whether the group runs. Once it does, a file
    # outside the filter counts as it does on the PR: tests read such files.
    triggers = st.workflow_trigger_paths(UNIT_WORKFLOW)
    changed = ["eval/scenarios/rag/basic.yaml", "src/gaia/core/base.py"]
    group = st.select(graph, changed, FULL_MAP, trigger_paths=triggers)
    on_its_pr = st.select(graph, changed, FULL_MAP)
    assert group.triggered and group.full
    assert (group.full, group.tests) == (on_its_pr.full, on_its_pr.tests)


# A doc-path test like test_ai_doc_paths_exist: it walks .claude/ and reads the
# root instruction files, none of which test_unit.yml's paths filter lists.
DOC_PATH_TEST = "tests/unit/test_ai_doc_paths.py"
DOC_FILES = {
    **FILES,
    ".claude/skills/demo/SKILL.md": "---\nname: demo\n---\n",
    "CLAUDE.md": "# instructions",
    "AGENTS.md": "# agents",
    "CONTRIBUTING.md": "# contributing",
    "scripts/install.sh": "echo hi",
    DOC_PATH_TEST: (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def test_paths():\n"
        "    for doc in ROOT.joinpath('.claude').rglob('*.md'):\n"
        "        doc.read_text()\n"
        "    for name in ('CLAUDE.md', 'AGENTS.md', 'CONTRIBUTING.md'):\n"
        "        (ROOT / name).read_text()\n"
    ),
}


@pytest.fixture
def doc_graph(tmp_path):
    for rel, text in DOC_FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return st.build_graph(repo=tmp_path, files=sorted(DOC_FILES))


@pytest.mark.parametrize(
    "doc",
    [
        ".claude/skills/demo/SKILL.md",
        "CLAUDE.md",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "scripts/install.sh",
    ],
)
def test_code_plus_a_doc_path_file_still_runs_the_doc_path_tests(doc_graph, doc):
    # The code change alone narrows away from the doc-path test...
    code = "src/gaia/core/base.py"
    assert DOC_PATH_TEST not in st.select(doc_graph, [code], FULL_MAP).tests
    # ...but with the doc file beside it, in a PR or a queue group, it runs.
    triggers = st.workflow_trigger_paths(UNIT_WORKFLOW)
    for sel in (
        st.select(doc_graph, [code, doc], FULL_MAP),
        st.select(doc_graph, [code, doc], FULL_MAP, trigger_paths=triggers),
    ):
        assert DOC_PATH_TEST in sel.tests and sel.full, sel.reason


@pytest.mark.parametrize(
    "doc",
    [".claude/skills/demo/SKILL.md", "CLAUDE.md", "AGENTS.md", "scripts/install.sh"],
)
def test_a_group_of_only_doc_path_files_runs_the_full_suite(doc_graph, doc):
    sel = st.select(
        doc_graph,
        [doc],
        FULL_MAP,
        trigger_paths=st.workflow_trigger_paths(UNIT_WORKFLOW),
    )
    assert sel.triggered and sel.full and DOC_PATH_TEST in sel.tests, sel.reason


@pytest.mark.parametrize(
    "changed",
    [
        # The selector and the workflow that runs it.
        "util/select_tests.py",
        ".github/workflows/test_unit.yml",
        # Any other workflow file.
        ".github/workflows/lint.yml",
        # conftest and shared fixtures.
        "conftest.py",
        "tests/conftest.py",
        "tests/fixtures/data/cases.json",
        "tests/fixtures/gaia/email/build_mailbox.py",
    ],
)
def test_a_merge_group_still_runs_everything_for_unreadable_changes(graph, changed):
    # Batched with a docs change and a mapped source file, as a group would be.
    sel = st.select(
        graph,
        ["docs/guides/not-a-unit-test-input.mdx", "src/gaia/core/base.py", changed],
        FULL_MAP,
        trigger_paths=st.workflow_trigger_paths(UNIT_WORKFLOW),
    )
    assert sel.triggered and sel.full and len(sel.tests) == sel.total, sel.reason


@pytest.mark.parametrize("changed", ["pytest.ini", "tox.ini", "setup.cfg"])
def test_the_filter_never_drops_a_file_that_forces_a_full_run(graph, changed):
    # Not in the workflow's filter, but they configure every test.
    sel = st.select(
        graph,
        ["README.md", changed],
        FULL_MAP,
        trigger_paths=["src/**", "tests/**"],
    )
    assert sel.triggered and sel.full, sel.reason


def test_select_writes_whether_the_change_triggered_the_workflow(
    graph, tmp_path, monkeypatch
):
    monkeypatch.setattr(st, "build_graph", lambda: graph)
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    wf = tmp_path / "wf.yml"
    wf.write_text("on:\n  pull_request:\n    paths: ['src/**']\n", encoding="utf-8")
    base = ["select", "--trigger-paths-from", str(wf), "--github-output", "linux"]
    st.main(base + ["--changed", "README.md"])
    st.main(base + ["--changed", "src/gaia/core/base.py"])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[:4] == [
        "linux_full=false",
        "linux_count=0",
        "linux_triggered=false",
        "linux_os=false",
    ]
    assert lines[6] == "linux_triggered=true"


# Docs, Agent UI, the Go TUI and hub data are only ever read by tests, so each
# reaches just the tests that name it or scan its folder.
RO_FILES = {
    **FILES,
    "examples/unread.md": "nobody reads me",
    "src/gaia/apps/webui/src/App.tsx": "export {}",
    "src/gaia/apps/webui/src/styles/index.css": "body {}",
    "src/gaia/apps/webui/src/i18n/locales/en.json": "{}",
    "src/gaia/apps/webui/package.json": "{}",
    "tui/go.mod": "module x",
    "tui/internal/lemonade/recommended_models.json": "[]",
    "tui/internal/ui/view.go": "package ui",
    "hub/agents/gaia/npm/README.md": "readme",
    "hub/agents/gaia/npm/binaries.lock.json": "{}",
    "hub/agents/mail/python/gaia_agent_mail/prompt.md": "prompt",
    "website/src/pages/not-a-unit-test-input.astro": "---",
    "tests/unit/test_webui_app.py": "APP = ROOT / 'src/gaia/apps/webui/src/App.tsx'\n",
    "tests/unit/test_webui_strings.py": (
        "def test_s():\n"
        "    for f in (ROOT / 'src' / 'gaia' / 'apps' / 'webui').rglob('*.json'):\n"
        "        f.read_text()\n"
    ),
    "tests/unit/test_tui_models.py": "NAME = 'recommended_models.json'\n",
    "tests/unit/test_npm_readme.py": (
        "PATH = ROOT / 'hub' / 'agents' / 'gaia' / 'npm' / 'README.md'\n"
    ),
    # A folder named by path, then a file in it by a computed name.
    "src/gaia/apps/webui/src/i18n/locales/en.ts": "export {}",
    "src/gaia/apps/webui/src/i18n/locales/ar.ts": "export {}",
    "tests/unit/test_locale_fstring.py": (
        "def test_l(webui, locale):\n"
        "    (webui / f'i18n/locales/{locale}.ts').read_text()\n"
    ),
    "tests/unit/test_locale_folder.py": (
        "LOCALES = ROOT / 'src/gaia/apps/webui/src/i18n/locales'\n"
        "def test_l(locale):\n"
        "    (LOCALES / f'{locale}.ts').read_text()\n"
    ),
    "eval/prompts/judge.md": "judge",
    "installer/tui/README.md": "readme",
}


@pytest.fixture
def ro_graph(tmp_path):
    for rel, text in RO_FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return st.build_graph(repo=tmp_path, files=sorted(RO_FILES))


@pytest.mark.parametrize(
    "path, kind",
    [
        ("docs/guides/chat.mdx", "docs"),
        ("README.md", "docs"),
        ("src/gaia/apps/webui/src/App.tsx", "webui"),
        ("src/gaia/apps/webui/src/styles/index.css", "webui"),
        ("tui/internal/ui/view.go", "tui"),
        ("hub/agents/gaia/npm/README.md", "hub data"),
        ("hub/components/x/gaia-agent.yaml", "hub data"),
        # Pinned manifests and lockfiles several lanes read.
        ("src/gaia/apps/webui/package.json", None),
        ("src/gaia/apps/webui/package-lock.json", None),
        ("tui/go.mod", None),
        ("tui/go.sum", None),
        ("hub/agents/gaia/npm/binaries.lock.json", None),
        ("hub/agents/mail/python/pyproject.toml", None),
        # Loaded by shipped code that scans a folder.
        ("hub/skills/triage/SKILL.md", None),
        ("hub/agents/mail/python/gaia_agent_mail/prompt.md", None),
        # Python, fixtures, files that always force a full run, and unknowns.
        ("src/gaia/cli.py", None),
        ("tests/fixtures/notes.md", None),
        ("CLAUDE.md", None),
        (".claude/agents/x.md", None),
        ("src/gaia/agents/prompt.md", None),
        ("website/src/pages/not-a-unit-test-input.astro", None),
        ("eval/ci_lanes.json", None),
        # Prompts, corpora and installer notes are inputs, not docs pages.
        ("eval/prompts/judge.md", None),
        ("eval/corpus/doc.md", None),
        ("installer/tui/README.md", None),
    ],
)
def test_read_only_class_names_the_files_that_only_reach_their_readers(path, kind):
    assert st.read_only_class(path) == kind


@pytest.mark.parametrize(
    "changed, readers",
    [
        ("docs/guides/chat.mdx", {"tests/unit/test_chat_doc.py"}),
        ("src/gaia/apps/webui/src/App.tsx", {"tests/unit/test_webui_app.py"}),
        # Read through a folder scan, not a spelled-out path.
        (
            "src/gaia/apps/webui/src/i18n/locales/en.json",
            {"tests/unit/test_webui_strings.py"},
        ),
        (
            "tui/internal/lemonade/recommended_models.json",
            {"tests/unit/test_tui_models.py"},
        ),
        ("hub/agents/gaia/npm/README.md", {"tests/unit/test_npm_readme.py"}),
        # The folder is named by path; the file by an f-string or a variable.
        (
            "src/gaia/apps/webui/src/i18n/locales/ar.ts",
            {"tests/unit/test_locale_fstring.py", "tests/unit/test_locale_folder.py"},
        ),
    ],
)
def test_a_read_only_file_selects_only_its_readers(ro_graph, changed, readers):
    sel = st.select(ro_graph, [changed], FULL_MAP)
    assert not sel.full, sel.reason
    assert readers <= set(sel.tests)
    assert "tests/unit/test_unrelated.py" not in sel.tests
    assert len(sel.tests) < sel.total


@pytest.mark.parametrize(
    "changed",
    ["examples/unread.md", "tui/internal/ui/view.go", "README.md"],
)
def test_a_read_only_file_nobody_reads_selects_nothing(ro_graph, changed):
    sel = st.select(ro_graph, [changed], FULL_MAP)
    assert not sel.full and sel.tests == [], sel.reason


def test_python_beside_a_doc_page_narrows_to_both_reaches(ro_graph):
    # The common feature PR: code plus its guide no longer runs everything.
    sel = st.select(
        ro_graph, ["src/gaia/core/base.py", "docs/guides/chat.mdx"], FULL_MAP
    )
    assert not sel.full, sel.reason
    assert {"tests/unit/test_base.py", "tests/unit/test_chat_doc.py"} <= set(sel.tests)


@pytest.mark.parametrize(
    "changed",
    [
        "src/gaia/apps/webui/package.json",
        "tui/go.mod",
        "hub/agents/gaia/npm/binaries.lock.json",
        "hub/agents/mail/python/gaia_agent_mail/prompt.md",
        # A file type no rule names still runs everything.
        "website/src/pages/not-a-unit-test-input.astro",
    ],
)
def test_pinned_and_unknown_files_still_run_the_full_suite(ro_graph, changed):
    _full(ro_graph, changed)


def test_a_deleted_read_only_file_runs_the_full_suite(ro_graph):
    # A test may still name the old path; nothing on disk does.
    assert "deleted or renamed" in _full(ro_graph, "docs/guides/removed.mdx")


def test_without_a_map_a_read_only_change_still_runs_everything(ro_graph):
    _full(ro_graph, "docs/guides/chat.mdx", coverage=None)


def test_force_full_runs_everything_and_says_why(graph):
    sel = st.select(
        graph, ["tests/unit/test_base.py"], FULL_MAP, force_full="ci:full label"
    )
    assert sel.full and len(sel.tests) == sel.total and sel.reason == "ci:full label"
    # On Windows and macOS too: the label means the whole suite, not the OS subset.
    platform = st.select(
        graph,
        ["tests/unit/test_base.py"],
        FULL_MAP,
        platform_only=True,
        force_full="ci:full label",
    )
    assert platform.full and len(platform.tests) == platform.total


def test_the_label_reaches_the_workflow_outputs(graph, tmp_path, monkeypatch):
    monkeypatch.setattr(st, "build_graph", lambda: graph)
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    st.main(
        [
            "select",
            "--changed",
            "docs/guides/chat.mdx",
            "--platform-only",
            "--force-full",
            "ci:full label",
            "--github-output",
            "platform",
        ]
    )
    lines = set(out.read_text(encoding="utf-8").splitlines())
    assert {"platform_full=true", "platform_os=true"} <= lines


@pytest.mark.parametrize(
    "changed, runs",
    [
        # Read-only files never need macOS.
        (["docs/guides/chat.mdx"], False),
        (["src/gaia/apps/webui/src/App.tsx"], False),
        (["tui/internal/ui/view.go"], False),
        # Python outside the OS-touching modules, even one that spawns a
        # process: a macOS-only break there surfaces on the push to main.
        (["src/gaia/core/base.py"], False),
        (["src/gaia/tools/sinks.py"], False),
        (["src/gaia/daemon_client.py"], False),
        (["tests/unit/test_base.py"], False),
        # A test that makes OS calls itself.
        (["tests/unit/test_proc.py"], True),
        (["tests/unit/test_cli_proc.py"], True),
        # An OS-touching module, whatever is in it.
        (["src/gaia/daemon/server.py"], True),
        (["src/gaia/connectors/store.py"], True),
        (["src/gaia/utils/process_tree.py"], True),
        (["src/gaia/agents/tools/shell_tools.py"], True),
        (["installer/core/freeze.py"], True),
        (["installer/tui/build-pkg.sh"], True),
        # Files that force a full run, and Python that can't be read.
        (["setup.py"], True),
        (["tests/conftest.py"], True),
        (["src/gaia/gone.py"], True),
        # One OS-touching file among many is enough.
        (["docs/guides/chat.mdx", "src/gaia/daemon/server.py"], True),
    ],
)
def test_macos_runs_on_a_pr_only_for_os_touching_changes(graph, changed, runs):
    assert bool(st.os_reason(changed, graph)) is runs


TUI_WORKFLOW = REPO / ".github/workflows/build_tui.yml"
WEBUI = "src/gaia/apps/webui/"


@pytest.mark.parametrize(
    "changed, full",
    [
        (["src/gaia/apps/webui/src/App.tsx"], False),
        (["src/gaia/apps/webui/src/App.tsx", "src/gaia/apps/webui/src/a.css"], False),
        # A file outside the TUI filter never triggered it, so it can't widen it.
        (["src/gaia/apps/webui/src/App.tsx", "src/gaia/ui/server.py"], False),
        (["src/gaia/apps/webui/src/App.tsx", "tui/internal/ui/view.go"], True),
        (["src/gaia/apps/webui/src/App.tsx", "installer/core/freeze.py"], True),
        (["src/gaia/apps/webui/src/App.tsx", "src/gaia/daemon/server.py"], True),
        (["src/gaia/apps/webui/src/App.tsx", "src/gaia/version.py"], True),
        (["tui/internal/ui/view.go"], True),
        # Nothing in the filter: run everything rather than guess.
        (["docs/guides/chat.mdx"], True),
    ],
)
def test_the_tui_workflow_narrows_only_for_an_agent_ui_change(changed, full):
    patterns = st.workflow_trigger_paths(TUI_WORKFLOW)
    got, reason = st.lane_scope(changed, patterns, [WEBUI])
    assert got is full, reason


def test_the_tui_workflow_runs_in_full_for_the_label():
    patterns = st.workflow_trigger_paths(TUI_WORKFLOW)
    full, reason = st.lane_scope(
        ["src/gaia/apps/webui/src/App.tsx"], patterns, [WEBUI], "ci:full label"
    )
    assert full and reason == "ci:full label"


def test_the_tui_filter_runs_the_workflow_when_its_decision_changes():
    # Editing the script that narrows the TUI workflow must run it in full.
    rx = st.path_filter(st.workflow_trigger_paths(TUI_WORKFLOW))
    assert rx.match("util/select_tests.py")
    assert rx.match(".github/workflows/build_tui.yml")


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path):
    """A repo whose branch edits a webui file and a doc page, while main moves on."""
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.mdx").write_text("a", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    ui = tmp_path / WEBUI / "src"
    ui.mkdir(parents=True)
    (ui / "App.tsx").write_text("x", encoding="utf-8")
    (tmp_path / "docs" / "a.mdx").write_text("b", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "change")
    # A later commit on main must not show up in the branch's diff.
    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "later.py").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "later")
    _git(tmp_path, "checkout", "-q", "feature")
    return tmp_path


def test_changed_files_come_from_git_against_the_merge_base(git_repo):
    changed = st.changed_since("main", repo=git_repo)
    assert sorted(changed) == ["docs/a.mdx", f"{WEBUI}src/App.tsx"]


def test_an_unknown_base_fails_loudly_instead_of_narrowing(git_repo):
    with pytest.raises(SystemExit, match="fetch-depth: 0"):
        st.changed_since("origin/no-such-branch", repo=git_repo)


def test_lane_scope_reads_the_diff_from_git(git_repo, tmp_path_factory, monkeypatch):
    wf = tmp_path_factory.mktemp("wf") / "wf.yml"
    wf.write_text(
        "on:\n  pull_request:\n    paths: ['src/gaia/apps/webui/**', 'tui/**']\n",
        encoding="utf-8",
    )
    out = tmp_path_factory.mktemp("out") / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    real = st.changed_since
    monkeypatch.setattr(st, "changed_since", lambda base: real(base, repo=git_repo))
    args = ["lane-scope", "--base", "main", "--workflow", str(wf)]
    st.main(args + ["--narrow-prefix", WEBUI, "--github-output", "tui"])
    assert out.read_text(encoding="utf-8").splitlines() == ["tui_full=false"]


def test_a_folder_read_does_not_widen_a_python_change(ro_graph):
    # Python reaches its readers through imports, not through folder reads.
    hit = _reach(ro_graph, "src/gaia/core/base.py")
    assert "tests/unit/test_locale_folder.py" not in hit


def test_a_single_folder_name_is_not_a_folder_read(tmp_path):
    files = {
        "docs/a.mdx": "a",
        "tests/unit/test_one_name.py": "DOCS = 'docs'\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    graph = st.build_graph(repo=tmp_path, files=sorted(files))
    assert not graph.folder_reads.get("tests/unit/test_one_name.py")


# The two real tests that read a locale file by a computed name.
LOCALE_READERS = [
    "tests/unit/test_other_server_start_hint_translated.py",
    "tests/unit/test_flagship_tool_labels.py",
]


@pytest.mark.parametrize("locale", ["en", "ar"])
def test_the_real_locale_tests_are_selected_by_a_locale_edit(locale):
    locales = "src/gaia/apps/webui/src/i18n/locales"
    files = sorted(
        LOCALE_READERS
        + [p.relative_to(REPO).as_posix() for p in (REPO / locales).iterdir()]
    )
    graph = st.build_graph(repo=REPO, files=files)
    hit = st.affected_files(graph, [f"{locales}/{locale}.ts"], {})
    assert set(LOCALE_READERS) <= hit


_DARWIN_RE = re.compile(
    r"""sys\.platform\s*(?:==|!=|\.startswith\()\s*\(?["']darwin"""
    r"""|platform\.system\(\)\s*==\s*["']Darwin"""
)


def _unlisted_darwin_modules(sources):
    """The paths, of ``{path: source}``, that branch on macOS but aren't gated."""
    return sorted(
        path
        for path, source in sources.items()
        if _DARWIN_RE.search(source) and not st.is_os_touching_path(path)
    )


def test_an_unlisted_module_that_branches_on_macos_fails_the_check():
    sources = {
        "src/gaia/not_listed_xyz.py": "import sys\nIS_MAC = sys.platform == 'darwin'\n",
        "src/gaia/daemon/listed.py": "import sys\nIS_MAC = sys.platform == 'darwin'\n",
        "src/gaia/no_branch.py": "X = 1\n",
    }
    assert _unlisted_darwin_modules(sources) == ["src/gaia/not_listed_xyz.py"]


def test_every_module_that_branches_on_macos_runs_the_macos_lane():
    # A module with a darwin branch the gate doesn't list would merge without
    # the macOS lane ever running on its PR.
    sources = {
        path.relative_to(REPO).as_posix(): path.read_text(
            encoding="utf-8", errors="ignore"
        )
        for path in (REPO / "src/gaia").rglob("*.py")
    }
    missing = _unlisted_darwin_modules(sources)
    assert not missing, (
        "These modules branch on macOS but the macOS lane skips them on a PR. "
        f"Add them to OS_MODULE_PREFIXES in util/select_tests.py: {missing}"
    )


def test_a_folder_joined_with_a_computed_name_is_a_folder_read(tmp_path):
    files = {
        "docs/guides/chat.mdx": "a",
        "docs/other/x.mdx": "b",
        "tests/unit/test_guide_by_name.py": (
            "def page(name):\n" "    return ROOT / 'docs' / 'guides' / f'{name}.mdx'\n"
        ),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    graph = st.build_graph(repo=tmp_path, files=sorted(files))
    reader = "tests/unit/test_guide_by_name.py"
    assert reader in st.affected_files(graph, ["docs/guides/chat.mdx"], {})
    assert reader not in st.affected_files(graph, ["docs/other/x.mdx"], {})
