# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Asking once per folder before a repository's own guidance reaches the prompt.

Covers the trust store (``gaia.project_trust``) and the check in
``ProjectMapMixin`` that consults it and asks a person when it can.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from gaia import project_trust
from gaia.agents.base.agent import Agent
from gaia.agents.base.project_map import (
    PROJECT_ROOT_ENV,
    ProjectMapMixin,
    clear_project_map_cache,
    guidance_files,
)
from gaia.agents.base.tool_grants import PROJECT_TRUST_PROMPT_TOOL
from gaia.project_trust import (
    DECLINED,
    TRUSTED,
    ProjectTrustError,
    folder_key,
    folder_trust,
    remember_folder_trust,
    trust_store_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MODEL = "anthropic/claude-opus-5"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    monkeypatch.delenv("GAIA_PROJECT_CONTEXT", raising=False)
    clear_project_map_cache()
    yield
    clear_project_map_cache()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
    return root


# ── the store ─────────────────────────────────────────────────────────────


def test_store_sits_beside_config_json(tmp_path):
    assert trust_store_path() == tmp_path / "project_trust.json"


def test_unknown_folder_has_no_answer(repo):
    assert folder_trust(repo) is None


@pytest.mark.parametrize("decision", [TRUSTED, DECLINED])
def test_answer_is_remembered_with_a_timestamp(repo, decision):
    remember_folder_trust(repo, decision)
    assert folder_trust(repo) == decision
    saved = json.loads(trust_store_path().read_text(encoding="utf-8"))
    assert saved[folder_key(repo)]["decision"] == decision
    assert saved[folder_key(repo)]["at"]


def test_a_new_answer_keeps_the_other_folders(tmp_path, repo):
    other = tmp_path / "other"
    other.mkdir()
    remember_folder_trust(repo, TRUSTED)
    remember_folder_trust(other, DECLINED)
    remember_folder_trust(repo, DECLINED)
    assert folder_trust(repo) == DECLINED
    assert folder_trust(other) == DECLINED


def test_an_unknown_decision_is_refused(repo):
    with pytest.raises(ValueError, match="decision"):
        remember_folder_trust(repo, "maybe")


def test_trailing_slash_and_dot_segments_are_the_same_folder(tmp_path, repo):
    remember_folder_trust(f"{repo}{os.sep}", TRUSTED)
    assert folder_trust(repo) == TRUSTED
    assert folder_trust(repo / "sub" / "..") == TRUSTED


@pytest.mark.skipif(os.name != "nt", reason="Windows paths ignore case")
def test_windows_paths_ignore_case_and_slash_direction(repo):
    remember_folder_trust(str(repo).upper(), TRUSTED)
    assert folder_trust(str(repo).lower().replace("\\", "/")) == TRUSTED


@pytest.mark.skipif(os.name == "nt", reason="POSIX paths are case-sensitive")
def test_posix_paths_keep_their_case(tmp_path):
    (tmp_path / "Repo").mkdir()
    remember_folder_trust(tmp_path / "Repo", TRUSTED)
    assert folder_trust(tmp_path / "repo") is None


def test_the_write_holds_the_lock_and_replaces_atomically(repo, monkeypatch):
    held = []
    real_lock = project_trust.file_lock
    real_write = project_trust.write_text_atomic

    class Recording:
        def __init__(self, target):
            self.cm = real_lock(target)

        def __enter__(self):
            held.append("locked")
            return self.cm.__enter__()

        def __exit__(self, *exc):
            held.append("released")
            return self.cm.__exit__(*exc)

    def write(target, text, *args, **kwargs):
        assert held == ["locked"], "written outside the lock"
        assert Path(target) == trust_store_path()
        real_write(target, text, *args, **kwargs)
        held.append("written")

    monkeypatch.setattr(project_trust, "file_lock", Recording)
    monkeypatch.setattr(project_trust, "write_text_atomic", write)
    remember_folder_trust(repo, TRUSTED)
    assert held == ["locked", "written", "released"]
    assert not list(trust_store_path().parent.glob(".project_trust.json.*"))


def test_concurrent_threads_keep_every_answer(tmp_path):
    folders = []
    for i in range(8):
        folder = tmp_path / f"repo{i}"
        folder.mkdir()
        folders.append(folder)
    errors = []

    def answer(folder):
        try:
            remember_folder_trust(folder, TRUSTED)
        except Exception as e:  # noqa: BLE001 - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=answer, args=(f,)) for f in folders]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert all(folder_trust(f) == TRUSTED for f in folders)


def test_another_process_writing_at_the_same_time_loses_nothing(tmp_path):
    env = {
        **os.environ,
        "GAIA_CONFIG_FILE": str(tmp_path / "config.json"),
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }
    script = (
        "import sys; from gaia.project_trust import remember_folder_trust; "
        "remember_folder_trust(sys.argv[1], 'declined')"
    )
    others = []
    for i in range(3):
        folder = tmp_path / f"proc{i}"
        folder.mkdir()
        others.append(folder)
    procs = [
        subprocess.Popen([sys.executable, "-c", script, str(f)], env=env)
        for f in others
    ]
    mine = tmp_path / "mine"
    mine.mkdir()
    for _ in range(5):
        remember_folder_trust(mine, TRUSTED)
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0]
    assert folder_trust(mine) == TRUSTED
    assert all(folder_trust(f) == DECLINED for f in others)


def test_a_corrupt_store_reads_as_unknown_and_is_not_overwritten(repo):
    store = trust_store_path()
    store.write_text("{not json", encoding="utf-8")
    assert folder_trust(repo) is None
    with pytest.raises(ProjectTrustError, match="project_trust.json"):
        remember_folder_trust(repo, TRUSTED)
    assert store.read_text(encoding="utf-8") == "{not json"


def test_entries_with_a_bad_shape_are_unknown(repo):
    trust_store_path().write_text(
        json.dumps({folder_key(repo): {"decision": "yes please"}}), encoding="utf-8"
    )
    assert folder_trust(repo) is None


# ── what would be loaded ──────────────────────────────────────────────────


def test_guidance_files_name_the_instruction_files_and_count_skills(repo):
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    for name in ("one", "two"):
        (repo / ".claude" / "skills" / name).mkdir(parents=True)
        (repo / ".claude" / "skills" / name / "SKILL.md").write_text("x", "utf-8")
    (repo / ".claude" / "skills" / "empty").mkdir()
    assert guidance_files(repo) == [
        "AGENTS.md",
        "CLAUDE.md",
        ".claude/skills (2 skills)",
    ]


def test_a_folder_without_guidance_lists_nothing(tmp_path):
    assert guidance_files(tmp_path) == []


# ── the check ─────────────────────────────────────────────────────────────


class _Console:
    """A console that answers the trust prompt with *answer*, or cannot ask."""

    def __init__(self, answer=None, **flags):
        self.answer = answer
        self.asked = []
        self.full_access = False
        self.cancelled = threading.Event()
        self.timed_out = False
        self.background_mode = False
        self.nobody_answers = False
        self.blocking_confirmation = answer is not None
        self.__dict__.update(flags)

    def auto_approve_confirmations_enabled(self):
        return False

    def confirm_tool_execution(self, tool, args):
        self.asked.append((tool, args))
        return bool(self.answer)

    def confirmation_timed_out(self, tool):
        return self.timed_out


class _Base:
    def _on_task_start(self, user_input: str) -> None:
        pass


class _FakeAgent(ProjectMapMixin, _Base):
    _path_access_grants_unasked = Agent._path_access_grants_unasked

    def __init__(self, root, console, project_context="auto", model=CLAUDE_MODEL):
        self.config = type(
            "C",
            (),
            {
                "project_root": str(root),
                "auto_index": False,
                "project_context": project_context,
            },
        )()
        self.model_id = model
        self.console = console
        self._tools_registry = {}


def test_unknown_folder_is_not_loaded_and_nobody_is_asked(repo):
    console = _Console()
    agent = _FakeAgent(repo, console)
    agent._on_task_start("hi")
    assert console.asked == []
    assert agent._full_project_context() is False
    assert folder_trust(repo) is None


def test_declined_folder_is_off_and_trusted_folder_is_unchanged(repo):
    remember_folder_trust(repo, DECLINED)
    assert _FakeAgent(repo, _Console())._full_project_context() is False
    remember_folder_trust(repo, TRUSTED)
    assert _FakeAgent(repo, _Console())._full_project_context() is True


def test_a_folder_with_nothing_to_load_is_not_gated(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / ".git").mkdir()
    agent = _FakeAgent(root, _Console())
    assert agent._full_project_context() is True


def test_off_bypasses_the_store(repo):
    remember_folder_trust(repo, TRUSTED)
    agent = _FakeAgent(repo, _Console(), project_context="off")
    assert agent._full_project_context() is False


def test_full_is_the_users_own_choice_and_skips_the_store(repo):
    console = _Console(answer=True)
    agent = _FakeAgent(repo, console, project_context="full")
    agent._on_task_start("hi")
    assert console.asked == []
    assert agent._full_project_context() is True
    remember_folder_trust(repo, DECLINED)
    assert _FakeAgent(repo, _Console(), project_context="full")._full_project_context()


def test_a_model_that_would_not_get_the_context_is_never_asked(repo):
    console = _Console(answer=True)
    agent = _FakeAgent(repo, console, model="Gemma-4-E4B-it-GGUF")
    agent._on_task_start("hi")
    assert console.asked == []
    assert agent._full_project_context() is False


def test_full_access_counts_as_trusted_without_asking(repo):
    console = _Console(answer=True, full_access=True)
    agent = _FakeAgent(repo, console)
    agent._on_task_start("hi")
    assert console.asked == []
    assert agent._full_project_context() is True
    assert folder_trust(repo) is None


def test_full_access_overrides_a_declined_folder(repo):
    remember_folder_trust(repo, DECLINED)
    agent = _FakeAgent(repo, _Console(full_access=True))
    assert agent._full_project_context() is True


def test_an_interactive_console_is_asked_which_files_and_the_yes_is_saved(repo):
    console = _Console(answer=True)
    agent = _FakeAgent(repo, console)
    agent._system_prompt_cache = "composed without the guidance"
    assert agent._full_project_context() is False
    agent._on_task_start("hi")
    assert console.asked == [
        (PROJECT_TRUST_PROMPT_TOOL, {"folder": str(repo), "files": ["CLAUDE.md"]})
    ]
    assert agent._full_project_context() is True
    assert not hasattr(agent, "_system_prompt_cache")
    assert folder_trust(repo) == TRUSTED
    # Once per session.
    agent._on_task_start("again")
    assert len(console.asked) == 1


def test_a_no_is_saved_and_the_folder_stays_off(repo):
    console = _Console(answer=False)
    agent = _FakeAgent(repo, console)
    agent._on_task_start("hi")
    assert agent._full_project_context() is False
    assert folder_trust(repo) == DECLINED


def test_a_saved_answer_means_no_second_question(repo):
    remember_folder_trust(repo, TRUSTED)
    console = _Console(answer=False)
    _FakeAgent(repo, console)._on_task_start("hi")
    assert console.asked == []


@pytest.mark.parametrize("how", ["timed_out", "cancelled"])
def test_an_unanswered_prompt_is_not_saved_as_a_no(repo, how):
    console = _Console(answer=False)
    if how == "timed_out":
        console.timed_out = True
    else:
        console.cancelled.set()
    agent = _FakeAgent(repo, console)
    agent._on_task_start("hi")
    assert agent._full_project_context() is False
    assert folder_trust(repo) is None
    agent._on_task_start("again")
    assert len(console.asked) == 1


@pytest.mark.parametrize(
    "flags",
    [
        {"background_mode": True},
        {"nobody_answers": True},
        {"blocking_confirmation": False},
    ],
)
def test_a_console_nobody_can_answer_is_not_asked(repo, flags):
    console = _Console(answer=True, **flags)
    agent = _FakeAgent(repo, console)
    agent._on_task_start("hi")
    assert console.asked == []
    assert agent._full_project_context() is False


def test_pre_approved_tools_never_answer_for_the_user(repo):
    console = _Console(answer=True)
    console.auto_approve_confirmations_enabled = lambda: True
    agent = _FakeAgent(repo, console)
    agent._on_task_start("hi")
    assert console.asked == []
    assert folder_trust(repo) is None


def test_a_store_that_cannot_be_written_keeps_the_answer_for_the_session(
    repo, monkeypatch, caplog
):
    def refuse(*_args):
        raise ProjectTrustError("Cannot save the answer to x")

    monkeypatch.setattr("gaia.project_trust.remember_folder_trust", refuse)
    agent = _FakeAgent(repo, _Console(answer=True))
    agent._on_task_start("hi")
    assert agent._full_project_context() is True
    assert "ask again next time" in caplog.text


def test_the_prompt_names_the_folder_and_the_files():
    from gaia.ui.sse_translation import CanonicalTranslator

    args = {"folder": "/w/repo", "files": ["CLAUDE.md", ".claude/skills (2 skills)"]}
    (event,) = CanonicalTranslator(run_id=None, agent_id="gaia", debug=False).translate(
        {"type": "permission_request", "tool": PROJECT_TRUST_PROMPT_TOOL, "args": args}
    )
    assert "/w/repo" in event["summary"]
    assert "CLAUDE.md, .claude/skills (2 skills)" in event["summary"]
    assert "always_scope" not in event


def test_the_sidecar_relay_never_offers_always_for_it():
    from gaia.ui.email_sidecar import relay

    assert PROJECT_TRUST_PROMPT_TOOL in relay._FRESH_CONSENT_TOOLS
