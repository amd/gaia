# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A path that does not exist is reported missing, not offered for approval.

Issue #5299: tools opened a 10-minute access prompt for paths that were never
there, then said "not found" only after it. Paths no answer could grant
(secrets, protected folders) must keep their existing refusal either way, so
they stay no more of an existence oracle than they were.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.security import PathValidator


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _Prompt:
    """Host access prompt that counts every path it is asked about."""

    def __init__(self, answer=False):
        self.answer = answer
        self.asked = []

    def __call__(self, path):
        self.asked.append(path)
        return self.answer


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """A chat scope, and an out-of-scope Documents folder the user could grant.

    pytest's tmp_path sits in the system temp dir, which is never askable, so the
    temp rule is pointed at a dedicated folder instead.
    """
    fake_temp = tmp_path / "systemp"
    fake_temp.mkdir()
    monkeypatch.setattr("gaia.security._system_temp_roots", lambda: {str(fake_temp)})
    scope = tmp_path / "scope"
    scope.mkdir()
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "report.csv").write_text("region,sales\nWest,10\n", encoding="utf-8")
    return SimpleNamespace(scope=scope, docs=docs, tmp=tmp_path)


def _validator(layout, answer=False):
    validator = PathValidator(allowed_paths=[str(layout.scope)])
    prompt = _Prompt(answer)
    validator.set_access_prompt(prompt)
    return validator, prompt


def _is_not_found(reason: str) -> bool:
    return "not found" in reason.lower()


# ── PathValidator: the shared choke point ─────────────────────────────────


class TestValidator:
    def test_a_missing_file_is_not_found_without_asking(self, layout):
        validator, prompt = _validator(layout, answer=True)
        missing = layout.docs / "quarterly_review_2024.pdf"

        allowed, reason = validator.validate_read(str(missing))

        assert not allowed
        assert _is_not_found(reason), reason
        assert prompt.asked == []
        # Nothing was granted on the way.
        assert not validator.is_path_allowed(str(layout.docs), prompt_user=False)

    def test_a_file_in_a_missing_folder_is_not_found_without_asking(self, layout):
        validator, prompt = _validator(layout)
        missing = layout.tmp / "projects" / "reports" / "q.pdf"

        allowed, reason = validator.validate_read(str(missing))

        assert not allowed and _is_not_found(reason), reason
        assert prompt.asked == []

    def test_a_missing_folder_is_not_found_without_asking(self, layout):
        validator, prompt = _validator(layout)

        allowed, reason = validator.validate_read(str(layout.tmp / "NoSuchFolder"))

        assert not allowed and _is_not_found(reason), reason
        assert prompt.asked == []

    def test_an_existing_out_of_scope_file_is_still_asked(self, layout):
        validator, prompt = _validator(layout, answer=False)
        target = layout.docs / "report.csv"

        allowed, reason = validator.validate_read(str(target))

        assert not allowed
        assert "Access denied" in reason
        assert prompt.asked == [target.resolve()]

    def test_an_existing_out_of_scope_folder_is_still_asked(self, layout):
        validator, prompt = _validator(layout, answer=True)

        assert validator.validate_read(str(layout.docs)) == (True, "")
        assert prompt.asked == [layout.docs.resolve()]

    def test_a_file_that_appears_later_is_asked_about_then(self, layout):
        validator, prompt = _validator(layout, answer=True)
        late = layout.docs / "late.csv"

        allowed, reason = validator.validate_read(str(late))
        assert not allowed and _is_not_found(reason)
        assert prompt.asked == []

        late.write_text("a,b\n1,2\n", encoding="utf-8")

        assert validator.validate_read(str(late)) == (True, "")
        assert prompt.asked == [late.resolve()]

    def test_a_missing_path_in_scope_is_still_allowed(self, layout):
        """In scope, the tool reports the missing file itself, as before."""
        validator, prompt = _validator(layout)

        assert validator.validate_read(str(layout.scope / "nope.txt")) == (True, "")
        assert prompt.asked == []

    def test_a_missing_path_is_never_granted(self, layout):
        """Not asking also means no grant: the folder stays out of scope."""
        validator, _ = _validator(layout, answer=True)
        before = set(validator.allowed_paths)

        validator.validate_read(str(layout.docs / "ghost.txt"))

        assert validator.allowed_paths == before

    def test_the_not_found_error_names_an_attached_file_of_that_name(self, layout):
        attached = layout.scope / "sales_data_2025.csv"
        attached.write_text("a\n1\n", encoding="utf-8")
        validator, prompt = _validator(layout)

        allowed, reason = validator.validate_read(
            str(layout.tmp / "repo" / "sales_data_2025.csv")
        )

        assert not allowed and _is_not_found(reason)
        assert str(attached.resolve()) in reason
        assert prompt.asked == []

    def test_a_secret_candidate_is_never_suggested(self, layout):
        (layout.scope / ".env").write_text("TOKEN=1", encoding="utf-8")
        validator, _ = _validator(layout)

        _, reason = validator.validate_read(str(layout.tmp / "repo" / ".env"))

        assert str(layout.scope) not in reason

    def test_without_any_prompt_a_missing_path_is_still_access_denied(
        self, layout, monkeypatch
    ):
        """No prompt would open, so nothing changes: no new existence oracle."""
        monkeypatch.setattr("gaia.security._is_interactive", lambda: False)
        validator = PathValidator(allowed_paths=[str(layout.scope)])

        _, missing_reason = validator.validate_read(str(layout.docs / "ghost.csv"))
        _, present_reason = validator.validate_read(str(layout.docs / "report.csv"))

        assert "Access denied" in missing_reason
        assert not _is_not_found(missing_reason)
        assert missing_reason.replace("ghost", "report") == present_reason


class TestNoNewOracleForProtectedPaths:
    """Paths no approval can grant are refused the same, present or not."""

    @pytest.mark.parametrize("name", ["id_rsa", "server.pem", "credentials.json"])
    def test_a_secret_file_is_refused_the_same_present_or_missing(self, layout, name):
        validator, prompt = _validator(layout, answer=True)
        outside = layout.tmp / "keys"
        outside.mkdir()
        present = outside / name
        present.write_text("secret", encoding="utf-8")
        missing_dir = layout.tmp / "keys2"
        missing_dir.mkdir()
        missing = missing_dir / name

        ok_present, present_reason = validator.validate_read(str(present))
        ok_missing, missing_reason = validator.validate_read(str(missing))

        assert not ok_present and not ok_missing
        assert not _is_not_found(missing_reason), missing_reason
        assert missing_reason.replace("keys2", "keys") == present_reason
        assert prompt.asked == []

    def test_a_missing_path_in_a_secret_folder_is_refused_as_secret(
        self, layout, monkeypatch
    ):
        secret_dir = layout.tmp / "dotssh"
        secret_dir.mkdir()
        monkeypatch.setattr("gaia.security.SECRET_DIRECTORIES", {str(secret_dir)})
        validator, prompt = _validator(layout, answer=True)

        allowed, reason = validator.validate_read(str(secret_dir / "id_nope"))

        assert not allowed
        assert not _is_not_found(reason), reason
        assert "Read blocked" in reason
        assert prompt.asked == []

    def test_a_missing_path_in_a_protected_folder_is_refused_as_protected(
        self, layout, monkeypatch
    ):
        protected = layout.tmp / "Windows"
        protected.mkdir()
        monkeypatch.setattr("gaia.security.BLOCKED_DIRECTORIES", {str(protected)})
        validator, prompt = _validator(layout, answer=True)

        allowed, reason = validator.validate_read(str(protected / "ghost.ini"))

        assert not allowed
        assert not _is_not_found(reason), reason
        assert "No approval can grant it" in reason
        assert prompt.asked == []


class TestSymlinks:
    @pytest.fixture
    def link(self, layout):
        def _make(name, target, is_dir=False):
            path = layout.scope / name
            try:
                os.symlink(target, path, target_is_directory=is_dir)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlinks unavailable here: {exc}")
            return path

        return _make

    def test_a_link_to_an_existing_outside_file_is_still_asked(self, layout, link):
        target = layout.docs / "report.csv"
        validator, prompt = _validator(layout)

        allowed, _ = validator.validate_read(str(link("r.csv", target)))

        assert not allowed
        assert prompt.asked == [target.resolve()]

    def test_a_file_reached_through_a_linked_folder_is_still_asked(self, layout, link):
        folder = link("docs_link", layout.docs, is_dir=True)
        validator, prompt = _validator(layout)

        validator.validate_read(str(folder / "report.csv"))

        assert prompt.asked == [(layout.docs / "report.csv").resolve()]

    def test_a_dangling_link_out_of_scope_is_not_found_without_asking(
        self, layout, link
    ):
        dangling = link("gone.csv", layout.docs / "gone.csv")
        validator, prompt = _validator(layout)

        allowed, reason = validator.validate_read(str(dangling))

        assert not allowed and _is_not_found(reason), reason
        assert prompt.asked == []


# ── The tools that hit this in the eval ───────────────────────────────────


class _SearchHost(FileSearchToolsMixin):
    pass


def _search_tools(validator, indexed=()):
    host = _SearchHost()
    host.path_validator = validator
    host.rag = SimpleNamespace(indexed_files=[str(p) for p in indexed])
    host.register_file_search_tools()
    return {
        n: _TOOL_REGISTRY[n]["function"] for n in ("read_file", "analyze_data_file")
    }


class TestTools:
    def test_read_file_reports_a_missing_path_without_asking(self, layout):
        validator, prompt = _validator(layout, answer=True)
        tools = _search_tools(validator)

        result = tools["read_file"](str(layout.docs / "quarterly_review_2024.pdf"))

        assert result["status"] == "error"
        assert _is_not_found(result["error"]), result
        assert prompt.asked == []

    def test_analyze_data_file_reports_a_missing_path_without_asking(self, layout):
        validator, prompt = _validator(layout, answer=True)
        tools = _search_tools(validator)

        result = tools["analyze_data_file"](str(layout.docs / "sales_2030.csv"))

        assert result["status"] == "error"
        assert _is_not_found(result["error"]), result
        assert prompt.asked == []

    def test_analyze_data_file_uses_the_attached_csv_for_a_bare_name(
        self, layout, monkeypatch
    ):
        """The csv_analysis repro: a bare name resolves to the attachment."""
        attached = layout.scope / "sales_data_2025.csv"
        attached.write_text("region,sales\nWest,74\nEast,12\n", encoding="utf-8")
        workdir = layout.tmp / "repo"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        validator, prompt = _validator(layout)
        tools = _search_tools(validator, indexed=[attached])

        result = tools["analyze_data_file"]("sales_data_2025.csv")

        assert result.get("status") != "error", result
        assert prompt.asked == []

    def test_analyze_data_file_prefers_the_attachment_over_a_same_named_outside_file(
        self, layout, monkeypatch
    ):
        attached = layout.scope / "report.csv"
        attached.write_text("region,sales\nWest,74\n", encoding="utf-8")
        monkeypatch.chdir(layout.docs)  # holds an out-of-scope report.csv
        validator, prompt = _validator(layout)
        tools = _search_tools(validator, indexed=[attached])

        result = tools["analyze_data_file"]("report.csv")

        assert result.get("status") != "error", result
        assert prompt.asked == []

    def test_a_bare_name_means_the_attachment_even_over_an_in_scope_cwd_file(
        self, layout, monkeypatch
    ):
        """Attachment-first is intended: a same-named working-folder file loses."""
        workdir = layout.scope / "work"
        workdir.mkdir()
        (workdir / "data.csv").write_text("region,sales\nNorth,1\n", encoding="utf-8")
        attached_dir = layout.scope / "attached"
        attached_dir.mkdir()
        attached = attached_dir / "data.csv"
        attached.write_text("region,sales\nWest,74\nEast,12\n", encoding="utf-8")
        monkeypatch.chdir(workdir)
        validator, prompt = _validator(layout)
        tools = _search_tools(validator, indexed=[attached])

        result = tools["analyze_data_file"]("data.csv")

        assert result.get("status") != "error", result
        assert os.path.samefile(result["file_path"], attached), result
        assert result["row_count"] == 2, result
        assert prompt.asked == []

    def test_analyze_data_file_still_asks_for_an_explicit_outside_path(self, layout):
        """Only a bare name is matched to an attachment; a full path is asked."""
        attached = layout.scope / "report.csv"
        attached.write_text("region,sales\nWest,74\n", encoding="utf-8")
        validator, prompt = _validator(layout, answer=False)
        tools = _search_tools(validator)

        result = tools["analyze_data_file"](str(layout.docs / "report.csv"))

        assert result["status"] == "error"
        assert prompt.asked == [(layout.docs / "report.csv").resolve()]


class _RagHost(RAGToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator
        self.indexed_files = set()
        self.current_session = None
        self.rag = MagicMock()
        self.rag.indexed_files = set()

    def rebuild_system_prompt(self):
        pass


class TestRagTools:
    def test_index_document_reports_a_missing_path_without_asking(self, layout):
        validator, prompt = _validator(layout, answer=True)
        host = _RagHost(validator)
        host.register_rag_tools()

        result = _TOOL_REGISTRY["index_document"]["function"](
            str(layout.tmp / "eval" / "benefits_addendum_2026.pdf")
        )

        assert result["status"] == "error"
        assert _is_not_found(result["error"]), result
        assert prompt.asked == []
        host.rag.index_document.assert_not_called()

    def test_index_document_asked_twice_for_a_missing_path_never_prompts(self, layout):
        """file_not_found: the model retried the same path and was asked twice."""
        validator, prompt = _validator(layout)
        host = _RagHost(validator)
        host.register_rag_tools()
        missing = str(layout.tmp / "projects" / "quarterly_review_2024.pdf")

        _TOOL_REGISTRY["index_document"]["function"](missing)
        _TOOL_REGISTRY["index_document"]["function"](missing)

        assert prompt.asked == []

    def test_index_directory_reports_a_missing_folder_without_asking(self, layout):
        validator, prompt = _validator(layout)
        host = _RagHost(validator)
        host.register_rag_tools()

        result = _TOOL_REGISTRY["index_directory"]["function"](
            str(layout.tmp / "NoSuchFolder")
        )

        assert result["status"] == "error"
        assert _is_not_found(result["error"]), result
        assert prompt.asked == []
