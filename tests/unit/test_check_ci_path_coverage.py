# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/check_ci_path_coverage.py.

Most tests build a small fake repository in tmp_path and check what the guard
derives from it. The last class reads the REAL workflow filters and pins the
trigger policy: a docs-only or website-only PR runs only its own lanes, and a
TUI change runs the Go lane. Probe paths are fictional so the guard does not
read them as files this test depends on.
"""

import sys
from pathlib import Path
from typing import Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_ci_path_coverage as cpc  # noqa: E402

NO_ALLOWLIST = cpc.Allowlist({}, {})


def _repo(tmp_path: Path, files: Dict[str, str]) -> cpc.Repo:
    for rel, body in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return cpc.Repo.load(tmp_path, files=list(files))


def _gaps(tmp_path: Path, files: Dict[str, str], allowlist=NO_ALLOWLIST):
    repo = _repo(tmp_path, files)
    return cpc.find_gaps(repo, tmp_path / ".github" / "workflows", allowlist)


def _workflow(paths, steps: str, extra_jobs: str = "") -> str:
    listed = "".join(f"      - '{p}'\n" for p in paths)
    return (
        "on:\n  pull_request:\n    paths:\n"
        + listed
        + "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
        + steps
        + extra_jobs
    )


def _missing(gaps) -> set:
    return {line.split("`")[1] for line in gaps.missed}


# ---------------------------------------------------------------------------
# Path-filter semantics
# ---------------------------------------------------------------------------


class TestGlob:
    @pytest.mark.parametrize(
        "pattern,path,expected",
        [
            ("src/*", "src/a.py", True),
            ("src/*", "src/gaia/a.py", False),
            ("src/**", "src/gaia/a.py", True),
            ("**/conftest.py", "conftest.py", True),
            ("**/conftest.py", "tests/unit/conftest.py", True),
            ("tests/test_rag*.py", "tests/test_rag_integration.py", True),
            ("tests/test_rag*.py", "tests/unit/test_rag.py", False),
            ("*.md", "README.md", True),
            ("*.md", "docs/README.md", False),
            ("src/gaia/apps/*/webui/**", "src/gaia/apps/webui/src/a.tsx", False),
            ("src/gaia/apps/*/webui/**", "src/gaia/apps/example/webui/a.js", True),
        ],
    )
    def test_matches_github_semantics(self, pattern, path, expected):
        assert bool(cpc.glob_to_regex(pattern).fullmatch(path)) is expected


class TestPathFilter:
    def test_last_matching_pattern_wins(self):
        flt = cpc.PathFilter("paths", ("src/**", "!src/gaia/apps/webui/**"))
        assert flt.fires_on("src/gaia/cli.py")
        assert not flt.fires_on("src/gaia/apps/webui/src/App.tsx")

    def test_reinclude_after_negation(self):
        flt = cpc.PathFilter(
            "paths", ("src/**", "!src/gaia/apps/**", "src/gaia/apps/webui/main.cjs")
        )
        assert flt.fires_on("src/gaia/apps/webui/main.cjs")
        assert not flt.fires_on("src/gaia/apps/webui/index.html")

    def test_paths_ignore(self):
        flt = cpc.PathFilter("paths-ignore", ("docs/**",))
        assert not flt.fires_on("docs/a.mdx")
        assert flt.fires_on("src/a.py")
        assert flt.fires_on_any(["docs/a.mdx", "src/a.py"])

    def test_unfiltered_fires_on_everything(self):
        assert cpc.PathFilter("all").fires_on("anything/at/all")


class TestPrFilter:
    def test_no_pull_request_trigger(self):
        assert cpc.pr_filter({True: {"push": None}}) is None

    def test_bare_pull_request_is_unfiltered(self):
        assert cpc.pr_filter({True: {"pull_request": None}}).kind == "all"

    def test_list_form(self):
        assert cpc.pr_filter({"on": ["push", "pull_request"]}).kind == "all"

    def test_paths(self):
        flt = cpc.pr_filter({True: {"pull_request": {"paths": ["src/**"]}}})
        assert flt == cpc.PathFilter("paths", ("src/**",))

    def test_paths_ignore(self):
        flt = cpc.pr_filter({True: {"pull_request": {"paths-ignore": ["docs/**"]}}})
        assert flt.kind == "paths-ignore"


# ---------------------------------------------------------------------------
# Dependencies a workflow proves it has
# ---------------------------------------------------------------------------

WF = ".github/workflows/t.yml"


class TestPytestDependencies:
    FILES = {
        "conftest.py": "",
        "pyproject.toml": "",
        "tests/conftest.py": "",
        "tests/unit/conftest.py": "",
        "tests/unit/test_a.py": "from gaia.llm.client import X\n",
        "src/gaia/__init__.py": "",
        "src/gaia/llm/__init__.py": "",
        "src/gaia/llm/client.py": "from gaia.daemon.broker import Y\n",
        "src/gaia/daemon/__init__.py": "",
        "src/gaia/daemon/broker.py": "",
    }
    STEPS = "      - run: pytest tests/unit/test_a.py -v\n"

    def test_reports_each_missing_dependency(self, tmp_path):
        files = dict(self.FILES, **{WF: _workflow([WF], self.STEPS)})
        assert _missing(_gaps(tmp_path, files)) == {
            "conftest.py",
            "pyproject.toml",
            "tests/conftest.py",
            "tests/unit/conftest.py",
            "tests/unit/test_a.py",
            "src/gaia/llm/client.py",
        }

    def test_transitive_src_imports_are_not_required(self, tmp_path):
        paths = [
            WF,
            "conftest.py",
            "pyproject.toml",
            "tests/**",
            "src/gaia/llm/**",
        ]
        files = dict(self.FILES, **{WF: _workflow(paths, self.STEPS)})
        assert _missing(_gaps(tmp_path, files)) == set()

    def test_matrix_values_are_expanded(self, tmp_path):
        steps = "      - run: pytest tests/unit/test_${{ matrix.name }}.py\n"
        body = _workflow([WF], steps).replace(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n"
            "        name: [a]\n",
        )
        files = dict(self.FILES, **{WF: body})
        assert "tests/unit/test_a.py" in _missing(_gaps(tmp_path, files))


class TestSourceDependencies:
    def test_test_helpers_are_followed_transitively(self, tmp_path):
        files = {
            "tests/test_a.py": "from tests.helpers.one import f\n",
            "tests/helpers/one.py": "from tests.helpers.two import g\n",
            "tests/helpers/two.py": "",
            WF: _workflow(
                [WF, "tests/test_a.py"], "      - run: python tests/test_a.py\n"
            ),
        }
        assert _missing(_gaps(tmp_path, files)) == {
            "tests/helpers/one.py",
            "tests/helpers/two.py",
        }

    def test_bare_import_resolves_into_util(self, tmp_path):
        files = {
            "tests/test_a.py": "import release_tag\n",
            "util/release_tag.py": "",
            WF: _workflow(
                [WF, "tests/test_a.py"], "      - run: python tests/test_a.py\n"
            ),
        }
        assert _missing(_gaps(tmp_path, files)) == {"util/release_tag.py"}

    def test_literal_file_reads_are_required(self, tmp_path):
        files = {
            "tests/test_a.py": (
                "ROOT = None\n"
                "a = ROOT / 'docs' / 'guide.mdx'\n"
                "b = '.github/workflows/other.yml'\n"
            ),
            "docs/guide.mdx": "",
            ".github/workflows/other.yml": "on: push\njobs: {}\n",
            WF: _workflow(
                [WF, "tests/test_a.py"], "      - run: python tests/test_a.py\n"
            ),
        }
        assert _missing(_gaps(tmp_path, files)) == {
            "docs/guide.mdx",
            ".github/workflows/other.yml",
        }

    def test_directory_literals_are_not_expanded(self, tmp_path):
        files = {
            "tests/test_a.py": "FIXTURES = ROOT / 'tests' / 'fixtures'\n",
            "tests/fixtures/big.json": "",
            WF: _workflow(
                [WF, "tests/test_a.py"], "      - run: python tests/test_a.py\n"
            ),
        }
        assert _missing(_gaps(tmp_path, files)) == set()

    def test_literals_in_an_imported_util_helper_are_not_required(self, tmp_path):
        files = {
            "util/run.py": "import helper\n",
            "util/helper.py": "X = 'docs/only_in_unused_function.mdx'\n",
            "docs/only_in_unused_function.mdx": "",
            WF: _workflow([WF, "util/run.py"], "      - run: python util/run.py\n"),
        }
        assert _missing(_gaps(tmp_path, files)) == {"util/helper.py"}


class TestWorkflowStructure:
    def test_local_actions_are_followed(self, tmp_path):
        files = {
            ".github/actions/outer/action.yml": (
                "runs:\n  using: composite\n  steps:\n"
                "    - uses: ./.github/actions/inner\n"
            ),
            ".github/actions/inner/action.yml": "runs:\n  using: composite\n  steps: []\n",
            WF: _workflow([WF], "      - uses: ./.github/actions/outer\n"),
        }
        assert _missing(_gaps(tmp_path, files)) == {
            ".github/actions/outer/action.yml",
            ".github/actions/inner/action.yml",
        }

    def test_reusable_workflows_and_their_steps_are_followed(self, tmp_path):
        files = {
            ".github/workflows/called.yml": (
                "on: workflow_call\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: python util/build.py\n"
            ),
            "util/build.py": "",
            WF: _workflow(
                [WF],
                "      - run: 'true'\n",
                "  call:\n    uses: ./.github/workflows/called.yml\n",
            ),
        }
        assert _missing(_gaps(tmp_path, files)) == {
            ".github/workflows/called.yml",
            "util/build.py",
        }

    def test_installing_the_package_requires_its_manifests(self, tmp_path):
        files = {
            "setup.py": "",
            "pyproject.toml": "",
            "hub/agents/chat/python/pyproject.toml": "",
            WF: _workflow(
                [WF],
                '      - run: uv pip install --system -e ".[dev]" -e hub/agents/chat/python\n',
            ),
        }
        assert _missing(_gaps(tmp_path, files)) == {
            "setup.py",
            "pyproject.toml",
            "hub/agents/chat/python/pyproject.toml",
        }

    def test_node_build_requires_its_whole_tree(self, tmp_path):
        files = {
            "web/package.json": "",
            "web/src/a.ts": "",
            WF: _workflow(
                [WF, "web/package.json"],
                "      - run: npm ci\n        working-directory: web\n",
            ),
        }
        assert _missing(_gaps(tmp_path, files)) == {"web/src/a.ts"}

    def test_root_npm_requires_root_manifests(self, tmp_path):
        files = {
            "package.json": "",
            "package-lock.json": "",
            WF: _workflow([WF], "      - run: npm ci\n"),
        }
        assert _missing(_gaps(tmp_path, files)) == {
            "package.json",
            "package-lock.json",
        }

    def test_global_npm_install_reads_no_manifest(self, tmp_path):
        files = {
            "package.json": "",
            "package-lock.json": "",
            WF: _workflow([WF], "      - run: npm install -g mintlify\n"),
        }
        assert _missing(_gaps(tmp_path, files)) == set()

    def test_named_scripts_count_but_comments_and_messages_do_not(self, tmp_path):
        files = {
            "installer/scripts/start.ps1": "",
            "installer/scripts/other.ps1": "",
            WF: _workflow(
                [WF],
                "      - run: |\n"
                "          # see installer/scripts/other.ps1\n"
                "          echo installer/scripts/other.ps1\n"
                "          powershell -File installer\\scripts\\start.ps1\n",
            ),
        }
        assert _missing(_gaps(tmp_path, files)) == {"installer/scripts/start.ps1"}


# ---------------------------------------------------------------------------
# Orphans and the allowlist
# ---------------------------------------------------------------------------


class TestOrphans:
    def test_file_no_scoped_workflow_covers_is_an_orphan(self, tmp_path):
        files = {
            "src/gaia/a.py": "",
            "src/gaia/b.py": "",
            WF: _workflow([WF, "src/gaia/a.py"], "      - run: 'true'\n"),
            ".github/workflows/all.yml": "on: pull_request\njobs: {}\n",
        }
        assert _gaps(tmp_path, files).orphans == ["src/gaia/b.py"]

    def test_allowlisted_orphan_is_accepted(self, tmp_path):
        files = {
            "src/gaia/b.py": "",
            WF: _workflow([WF], "      - run: 'true'\n"),
        }
        allow = cpc.Allowlist({}, {"src/gaia/*.py": "reason"})
        assert _gaps(tmp_path, files, allow).orphans == []


class TestAllowlist:
    def test_workflow_and_wildcard_entries_apply(self, tmp_path):
        files = {
            "tests/test_a.py": "import gaia\nX = ROOT / 'docs' / 'a.mdx'\n",
            "src/gaia/__init__.py": "",
            "docs/a.mdx": "",
            WF: _workflow(
                [WF, "tests/test_a.py"], "      - run: python tests/test_a.py\n"
            ),
        }
        allow = cpc.Allowlist(
            {"*": {"src/gaia/__init__.py": "r"}, "t.yml": {"docs/**": "r"}}, {}
        )
        assert _missing(_gaps(tmp_path, files, allow)) == set()

    def test_entry_that_excuses_nothing_is_stale(self, tmp_path):
        files = {
            "src/gaia/a.py": "",
            WF: _workflow([WF, "src/**"], "      - run: 'true'\n"),
        }
        allow = cpc.Allowlist(
            {"t.yml": {"docs/**": "r"}, "*": {"x.py": "r"}}, {"src/old.py": "r"}
        )
        assert _gaps(tmp_path, files, allow).stale == [
            "t.yml: `docs/**`",
            "*: `x.py`",
            "orphans: `src/old.py`",
        ]

    def test_entry_without_reason_is_rejected(self, tmp_path):
        path = tmp_path / "allow.yml"
        path.write_text("dependencies:\n  t.yml:\n    docs/**: ''\n")
        with pytest.raises(ValueError, match="no reason"):
            cpc.load_allowlist(path)

    def test_missing_allowlist_is_an_error(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            cpc.load_allowlist(tmp_path / "absent.yml")


class TestChangedMode:
    def test_lists_triggered_workflows(self):
        filters = {
            "docs.yml": cpc.PathFilter("paths", ("docs/**",)),
            "unit.yml": cpc.PathFilter("paths", ("src/**",)),
            "audit.yml": cpc.PathFilter("all"),
        }
        assert cpc.triggered_workflows(filters, ["docs/a.mdx"]) == [
            "audit.yml",
            "docs.yml",
        ]


# ---------------------------------------------------------------------------
# The real filters: the trigger policy this guard exists to hold
# ---------------------------------------------------------------------------

# Workflows that run on every PR by design: the skill audit is a no-op gate
# when no skill changed.
CATCH_ALL = {"skill_audit.yml"}


@pytest.fixture(scope="module")
def real_filters():
    return cpc.load_filters(REPO_ROOT / ".github" / "workflows")


def _fired(filters, *paths):
    return set(cpc.triggered_workflows(filters, list(paths))) - CATCH_ALL


class TestRealTriggerPolicy:
    def test_docs_only_change_runs_only_docs_lanes(self, real_filters):
        assert _fired(real_filters, "docs/guides/_probe.mdx") == {
            "docs.yml",
            "check_doc_links.yml",
        }

    def test_website_only_change_runs_only_the_website_lane(self, real_filters):
        assert _fired(real_filters, "website/src/pages/_probe.astro") == {
            "website-ci.yml"
        }

    def test_tui_change_runs_the_go_lane(self, real_filters):
        assert "build_tui.yml" in _fired(real_filters, "tui/internal/ui/_probe.go")

    def test_agent_ui_frontend_change_runs_no_hardware_lane(self, real_filters):
        hardware = {
            "test_agent_sdk.yml",
            "test_api.yml",
            "test_embeddings.yml",
            "test_gaia_cli_windows.yml",
            "test_lemonade_server.yml",
            "test_npu_embedder.yml",
            "test_sd.yml",
            "test_gaia_init_hw.yml",
        }
        fired = _fired(real_filters, "src/gaia/apps/webui/src/components/_Probe.tsx")
        assert not fired & hardware

    @pytest.mark.parametrize(
        "workflow",
        [
            "test_agent_sdk.yml",
            "test_api.yml",
            "test_embeddings.yml",
            "test_gaia_cli_windows.yml",
            "test_npu_embedder.yml",
            "test_sd.yml",
            "test_rag.yml",
            "test_examples.yml",
        ],
    )
    def test_hardware_lanes_run_when_the_lemonade_pin_moves(
        self, real_filters, workflow
    ):
        flt = real_filters[workflow]
        assert flt.fires_on("src/gaia/version.py")
        assert flt.fires_on(".github/actions/install-lemonade/action.yml")
        assert flt.fires_on(f".github/workflows/{workflow}")
