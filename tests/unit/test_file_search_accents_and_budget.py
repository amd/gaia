# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Name search ignores accents; a wide search stops on time; links are not walked.

Asked for "the resume file", the agent never found ``résumé 📄 draft.txt``
sitting in its own documents folder: every name match compared ``.lower()``
strings, so "resume" was not in "résumé". It then widened the search to ``C:\\``
and one ``find_files`` call ran past the 180 s tool timeout with no result.

No LLM or external service required.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unicodedata
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import search_scope
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.filesystem.index import FileSystemIndexService
from gaia.filesystem.name_match import (
    fold_glob_match,
    fold_name,
    fold_words_match,
    glob_matches_mid_word,
)

RESUME_NFC = unicodedata.normalize("NFC", "résumé 📄 draft.txt")
RESUME_NFD = unicodedata.normalize("NFD", "Résumé final.txt")


class _Sandbox:
    """PathValidator stand-in: everything under *paths* is readable."""

    def __init__(self, *paths):
        self.allowed_paths = {Path(p).resolve() for p in paths}

    def is_path_allowed(self, path, prompt_user=True):
        resolved = Path(path).resolve()
        return any(
            resolved == root or root in resolved.parents for root in self.allowed_paths
        )

    def validate_read(self, path, prompt_user=True):
        if not self.is_path_allowed(path, prompt_user=prompt_user):
            return False, f"Access denied: '{path}' is not in allowed paths"
        return True, ""


def _find_files(root, index=None):
    class Agent(FileSystemToolsMixin):
        def __init__(self):
            self._web_client = None
            self._path_validator = _Sandbox(root)
            self._fs_index = index
            self._tools = {}
            self._bookmarks = {}

    registered = {}

    def mock_tool(atomic=True):
        def decorator(func):
            registered[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", mock_tool):
        Agent().register_filesystem_tools()
    return registered["find_files"]


def _file_search_tools(root):
    mixin = FileSearchToolsMixin()
    mixin.path_validator = _Sandbox(root)
    saved = dict(_TOOL_REGISTRY)
    try:
        mixin.register_file_search_tools()
        return (
            _TOOL_REGISTRY["search_file"]["function"],
            _TOOL_REGISTRY["search_file_content"]["function"],
        )
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def docs(tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    (root / RESUME_NFC).write_text("my résumé\n", encoding="utf-8")
    (root / RESUME_NFD).write_text("final\n", encoding="utf-8")
    (root / "Café menu.md").write_text("coffee\n", encoding="utf-8")
    (root / "resume plain.txt").write_text("plain\n", encoding="utf-8")
    (root / "報告書 2026.txt").write_text("report\n", encoding="utf-8")
    (root / "unrelated.txt").write_text("x\n", encoding="utf-8")
    return root


# ============================================================================
# 1. Folding
# ============================================================================


class TestFolding:
    def test_accents_and_case_fold_away(self):
        assert fold_name("Résumé") == "resume"
        assert fold_name("CAFÉ") == "cafe"

    def test_nfc_and_nfd_fold_the_same(self):
        nfc = unicodedata.normalize("NFC", "résumé")
        nfd = unicodedata.normalize("NFD", "résumé")
        assert nfc != nfd
        assert fold_name(nfc) == fold_name(nfd) == "resume"

    def test_emoji_and_cjk_fold_to_themselves(self):
        assert fold_name("📄") == "📄"
        assert fold_name("報告書") == "報告書"

    def test_a_hangul_syllable_does_not_match_half_of_another(self):
        # 하 is the first two jamo of 한; decomposition must not leak through.
        assert not fold_words_match("한국.txt", "하")
        assert fold_words_match("한국.txt", "한")

    def test_marks_that_change_the_letter_are_kept(self):
        # ガ (ga) is カ (ka) plus a voicing mark; they are different letters.
        assert not fold_words_match("ガイド.txt", "カイド")

    def test_both_directions_match(self):
        assert fold_words_match(RESUME_NFC, "resume")
        assert fold_words_match("resume plain.txt", "résumé")

    def test_glob_semantics_survive_folding(self):
        assert fold_glob_match(RESUME_NFD, "résumé*.txt")
        assert fold_glob_match("RESUME.txt", "r?sum?.*")
        assert fold_glob_match("résumé.txt", "[r]esume.txt")
        assert not fold_glob_match("my résumé.txt", "résumé*")


# ============================================================================
# 2. find_files — walk path
# ============================================================================


class TestFindFilesIgnoresAccents:
    def test_resume_finds_the_accented_files(self, docs):
        out = _find_files(docs)("resume", scope=str(docs))
        assert RESUME_NFC in out
        assert Path(RESUME_NFD).name in out or "final.txt" in out
        assert "resume plain.txt" in out

    def test_an_accented_query_finds_a_plain_name(self, docs):
        out = _find_files(docs)("résumé", scope=str(docs))
        assert "resume plain.txt" in out

    def test_cafe_finds_cafe(self, docs):
        assert "Café menu.md" in _find_files(docs)("cafe", scope=str(docs))

    def test_emoji_and_cjk_names_still_match(self, docs):
        find = _find_files(docs)
        assert RESUME_NFC in find("📄", search_type="name", scope=str(docs))
        assert "報告書 2026.txt" in find("報告書", scope=str(docs))

    def test_a_glob_with_an_accent(self, docs):
        out = _find_files(docs)("résumé*.txt", scope=str(docs))
        assert RESUME_NFC in out
        assert "final.txt" in out
        assert "resume plain.txt" in out
        assert "unrelated.txt" not in out


# ============================================================================
# 3. find_files — index path
# ============================================================================


@pytest.fixture
def index(tmp_path, docs):
    service = FileSystemIndexService(db_path=str(tmp_path / "index.db"))
    service.scan_directory(str(docs))
    yield service
    service.close_db()


class TestTheIndexFoldsToo:
    def test_query_files_matches_without_accents(self, index):
        names = {Path(r["path"]).name for r in index.query_files(name="resume")}
        assert RESUME_NFC in names
        assert "resume plain.txt" in names
        assert any("final.txt" in n for n in names)

    def test_query_files_accepts_glob_and_punctuation(self, index):
        names = {Path(r["path"]).name for r in index.query_files(name="résumé*.txt")}
        assert RESUME_NFC in names

    def test_a_query_with_no_words_matches_nothing(self, index):
        assert index.query_files(name="*.*") == []

    def test_find_files_answers_from_the_index(self, docs, index):
        out = _find_files(docs, index)("resume", scope=str(docs))
        assert "from index" in out
        assert RESUME_NFC in out

    def test_an_index_glob_keeps_glob_semantics(self, docs, index):
        out = _find_files(docs, index)("résumé*", scope=str(docs))
        assert RESUME_NFC in out
        assert "unrelated.txt" not in out

    @pytest.mark.parametrize("glob", ["*port*", "?eport.pdf", "re*ort*"])
    def test_a_glob_that_matches_mid_word_is_not_cut_short(self, tmp_path, glob):
        # The index finds word starts, so it would answer with port.txt alone.
        folder = tmp_path / "reports"
        folder.mkdir()
        for name in ("report.pdf", "port.txt", "unrelated.md"):
            (folder / name).write_text("x", encoding="utf-8")
        service = FileSystemIndexService(db_path=str(tmp_path / "glob.db"))
        try:
            service.scan_directory(str(folder))
            out = _find_files(folder, service)(glob, scope=str(folder))
        finally:
            service.close_db()
        assert "report.pdf" in out
        assert "unrelated.md" not in out
        if glob == "*port*":
            assert "port.txt" in out

    @pytest.mark.parametrize(
        "glob, mid_word",
        [
            ("*port*", True),
            ("?esume.txt", True),
            ("res*ume", True),
            ("[rR]*esume", True),
            ("résumé*", False),
            ("*.pdf", False),
            ("*_2026.txt", False),
            ("report", False),
        ],
    )
    def test_which_globs_the_index_cannot_answer(self, glob, mid_word):
        assert glob_matches_mid_word(glob) is mid_word


# ============================================================================
# 4. search_file
# ============================================================================


class TestSearchFileIgnoresAccents:
    def test_resume_finds_the_accented_files(self, docs):
        search_file, _ = _file_search_tools(docs)
        result = search_file("resume", directory=str(docs))
        names = {Path(f).name for f in result["files"]}
        assert RESUME_NFC in names
        assert any(n.endswith("final.txt") for n in names)

    def test_cafe_and_a_glob_with_an_accent(self, docs):
        search_file, _ = _file_search_tools(docs)
        assert [
            Path(f).name for f in search_file("cafe", directory=str(docs))["files"]
        ] == ["Café menu.md"]
        # search_file reads a pattern holding "." as a regex, so the glob has none.
        names = {
            Path(f).name for f in search_file("résumé*", directory=str(docs))["files"]
        }
        assert RESUME_NFC in names
        assert "resume plain.txt" in names
        assert "unrelated.txt" not in names

    def test_a_regex_with_an_accent(self, docs):
        search_file, _ = _file_search_tools(docs)
        names = {
            Path(f).name
            for f in search_file(r"RÉSUMÉ.*\D", directory=str(docs))["files"]
        }
        assert RESUME_NFC in names


# ============================================================================
# 5. The time budget, on a fake clock
# ============================================================================


@pytest.fixture
def wide_tree(tmp_path):
    root = tmp_path / "home"
    for d in range(6):
        folder = root / f"folder{d}"
        folder.mkdir(parents=True)
        for f in range(6):
            (folder / f"notes{f}.txt").write_text("needle\n", encoding="utf-8")
    return root


@pytest.fixture
def ticking_clock(monkeypatch):
    """Each read of the clock is one second later; the budget is ten seconds."""
    now = [0.0]

    def clock():
        now[0] += 1.0
        return now[0]

    monkeypatch.setattr(search_scope, "monotonic", clock)
    monkeypatch.setattr(search_scope, "SEARCH_TIME_BUDGET_S", 10.0)
    return now


class TestTheTimeBudget:
    def test_the_budget_is_well_under_the_tool_timeout(self):
        assert search_scope.SEARCH_TIME_BUDGET_S <= 30

    def test_an_injected_clock_wins(self):
        budget = search_scope.SearchBudget(seconds=2, clock=iter([0, 1, 5]).__next__)
        assert not budget.spend()
        assert budget.spend("here")
        assert budget.reason == "time"
        assert budget.stopped_in == "here"

    def test_find_files_returns_partial_results_with_a_note(
        self, wide_tree, ticking_clock
    ):
        out = _find_files(wide_tree)("notes", scope=str(wide_tree), max_results=100)
        assert "Found " in out
        assert out.count("notes") < 36
        assert "stopped early" in out
        assert f"Not finished: {wide_tree.resolve()}" in out
        assert "scope" in out

    def test_a_partial_miss_does_not_read_as_a_zero(self, wide_tree, ticking_clock):
        out = _find_files(wide_tree)("absent", scope=str(wide_tree))
        assert "No files found" not in out
        assert "in the part searched" in out
        assert "stopped early" in out

    def test_the_content_walk_is_bounded_too(self, wide_tree, ticking_clock):
        out = _find_files(wide_tree)(
            "needle", search_type="content", scope=str(wide_tree), max_results=100
        )
        assert "stopped early" in out

    def test_search_file_lists_what_it_did_not_finish(self, wide_tree, ticking_clock):
        search_file, _ = _file_search_tools(wide_tree)
        result = search_file("absent", directory=str(wide_tree))
        assert result["truncated"] is True
        assert str(wide_tree.resolve()) in result["unfinished_paths"]
        assert "NOT a complete zero" in result["suggestion"]

    def test_search_file_content_is_bounded(self, wide_tree, ticking_clock):
        _, search_file_content = _file_search_tools(wide_tree)
        result = search_file_content("needle", directory=str(wide_tree))
        assert result["truncated"] is True
        assert "stopped early" in result["message"]
        assert len(result["matches"]) < 36

    def test_within_budget_nothing_is_flagged(self, wide_tree):
        out = _find_files(wide_tree)("notes", scope=str(wide_tree), max_results=100)
        assert "Found 36" in out
        assert "stopped early" not in out


# ============================================================================
# 6. Links are not walked
# ============================================================================


@pytest.fixture
def home_with_link(tmp_path):
    """A home holding a link to a big tree elsewhere — a ``.cache`` model store."""
    store = tmp_path / "model-store"
    (store / "blobs").mkdir(parents=True)
    (store / "blobs" / "resume_weights.txt").write_text("w\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    (home / "resume_mine.txt").write_text("mine\n", encoding="utf-8")
    link = home / "models"
    if sys.platform == "win32":
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(store)],
            capture_output=True,
            text=True,
        )
        if made.returncode != 0:
            pytest.skip(f"cannot create a junction here: {made.stderr.strip()}")
    else:
        try:
            os.symlink(store, link, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"cannot create a symlink here: {exc}")
    return home, link


class TestLinksAreNotWalked:
    def test_is_link_sees_the_link_and_not_a_folder(self, home_with_link):
        home, link = home_with_link
        entries = {e.name: e for e in os.scandir(home)}
        if sys.platform == "win32":
            # The trap: a junction reports as a plain directory without following.
            assert entries["models"].is_dir(follow_symlinks=False)
        assert search_scope.is_link(entries["models"])
        assert search_scope.is_link(link)
        assert not search_scope.is_link(home)

    def test_find_files_does_not_follow_it(self, home_with_link):
        home, _ = home_with_link
        out = _find_files(home)("resume", scope=str(home))
        assert "resume_mine.txt" in out
        assert "resume_weights.txt" not in out

    def test_find_files_content_does_not_follow_it(self, home_with_link):
        home, _ = home_with_link
        # The sandbox holds the link target too, so only the walk can keep it out.
        out = _find_files(home.parent)("w", search_type="content", scope=str(home))
        assert "resume_weights.txt" not in out

    def test_search_file_does_not_follow_it(self, home_with_link):
        home, _ = home_with_link
        search_file, search_file_content = _file_search_tools(home)
        names = {
            Path(f).name for f in search_file("resume", directory=str(home))["files"]
        }
        assert names == {"resume_mine.txt"}
        grep = search_file_content("w", directory=str(home))
        assert all("resume_weights" not in m["file"] for m in grep["matches"])
