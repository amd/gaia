# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A new chat's task cards and working folder come from what is set up here."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui import start_screen as ss
from gaia.ui.dependencies import get_db
from gaia.ui.routers import onboarding

STARTER = frozenset(
    {
        "research-report",
        "coding",
        "summarize",
        "check-in",
        "document-brief",
        "inbox-triage",
    }
)


def _ctx(skills=STARTER, docs=0, mail=False):
    return ss.Context(skills, docs, mail, "my-project")


def _skills(cards):
    return [c["skill"] for c in cards]


def test_a_fresh_install_gets_four_cards_that_work_without_setup():
    cards = ss.pick_cards(_ctx())
    assert _skills(cards) == ["research-report", "coding", "summarize", "check-in"]


def test_what_the_user_set_up_comes_first():
    cards = ss.pick_cards(_ctx(docs=3, mail=True))
    assert _skills(cards) == [
        "document-brief",
        "inbox-triage",
        "research-report",
        "coding",
    ]
    assert cards[0]["note"] == "3 indexed documents"


def test_a_card_needs_its_skill_installed():
    cards = ss.pick_cards(_ctx(skills=frozenset({"summarize"}), docs=2, mail=True))
    assert _skills(cards) == ["summarize"]


def test_no_skills_means_no_cards():
    assert ss.pick_cards(_ctx(skills=frozenset())) == []


def test_cards_that_need_a_subject_say_so():
    by_skill = {c["skill"]: c for c in ss.pick_cards(_ctx())}
    assert by_skill["research-report"]["needs_input"] is True
    assert by_skill["check-in"]["needs_input"] is False
    assert "my-project" in by_skill["coding"]["prompt"]


def test_working_folder_is_the_project_directory(tmp_path, monkeypatch):
    from gaia.ui import session_environment

    monkeypatch.setattr(session_environment, "relayed", lambda _session: False)
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    folder = ss.working_folder()
    assert folder == {
        "path": str(project.resolve()),
        "name": "proj",
        "gaia_folder": False,
        "choosable": True,
    }


def test_working_folder_is_not_choosable_for_a_relayed_flagship(tmp_path, monkeypatch):
    from gaia.ui import session_environment

    monkeypatch.setattr(session_environment, "relayed", lambda _session: True)
    monkeypatch.chdir(tmp_path)
    assert ss.working_folder()["choosable"] is False


def test_working_folder_falls_back_to_gaias_folder_when_cwd_is_home(
    monkeypatch, tmp_path
):
    from gaia.ui import _chat_helpers as chat

    managed = tmp_path / "documents"
    monkeypatch.setattr(chat, "_managed_documents_dir", lambda: managed)
    monkeypatch.chdir(Path.home())
    folder = ss.working_folder()
    assert folder["path"] == str(managed) and folder["gaia_folder"] is True


class _Db:
    def __init__(self, docs):
        self.docs = docs

    def list_documents(self):
        return self.docs


def test_start_screen_counts_only_finished_documents(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ss, "_mail_connected", lambda: False)
    db = _Db([{"indexing_status": "complete"}, {"indexing_status": "indexing"}])
    result = ss.start_screen(db, [{"name": "document-brief"}])
    assert result["cards"][0]["note"] == "1 indexed document"
    assert result["working_folder"]["name"] == tmp_path.name


def test_mail_counts_as_connected_when_any_account_is(monkeypatch):
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    import gaia.connectors.store as store

    monkeypatch.setattr(
        store, "peek_connection", lambda ref: {"x": 1} if ref == "microsoft" else None
    )
    assert ss._mail_connected() is True
    monkeypatch.setattr(store, "peek_connection", lambda ref: None)
    assert ss._mail_connected() is False


def _refused_keyring(ref):
    from gaia.connectors.errors import ConnectorsError

    raise ConnectorsError("plaintext keyring backend refused")


def test_an_unreadable_keyring_only_drops_the_inbox_card(monkeypatch, caplog):
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    import gaia.connectors.store as store

    monkeypatch.setattr(store, "peek_connection", _refused_keyring)
    with caplog.at_level("WARNING"):
        assert ss._mail_connected() is False
    assert "plaintext keyring backend refused" in caplog.text


def test_an_unreadable_keyring_still_serves_the_start_screen(monkeypatch, tmp_path):
    import gaia.connectors.store as store

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "gaia.ui.routers.skills.list_skills",
        lambda: {"skills": [{"name": n} for n in STARTER], "invalid": []},
    )
    monkeypatch.setattr(store, "peek_connection", _refused_keyring)
    app = FastAPI()
    app.include_router(onboarding.router)
    app.dependency_overrides[get_db] = lambda: _Db([])
    response = TestClient(app).get("/api/onboarding/start")
    assert response.status_code == 200
    body = response.json()
    assert len(body["cards"]) == ss.MAX_CARDS
    assert body["working_folder"]["name"] == tmp_path.name
    assert not body.get("skills_error")


def test_a_keychain_prompt_does_not_hold_the_start_screen(monkeypatch, caplog):
    import threading
    import time

    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    import gaia.connectors.store as store
    from gaia.connectors import credential_wait

    answered = threading.Event()
    reads = []

    def prompting(ref):
        reads.append(ref)
        answered.wait(timeout=30)
        return None

    monkeypatch.setattr(store, "peek_connection", prompting)
    monkeypatch.setattr(credential_wait, "WAIT_SECONDS", 0.2)
    try:
        with caplog.at_level("WARNING"):
            for _ in range(3):
                started = time.monotonic()
                assert ss._mail_connected() is False
                assert time.monotonic() - started < 2
        assert "Look for a system dialog" in caplog.text
        assert len(reads) == 1  # one read in flight, not one per call
    finally:
        answered.set()
    deadline = time.monotonic() + 5
    while credential_wait.in_flight_count() and time.monotonic() < deadline:
        ss._mail_connected()
        time.sleep(0.01)
    assert credential_wait.in_flight_count() == 0


def test_a_non_keyring_error_still_fails_loudly(monkeypatch):
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    import gaia.connectors.store as store

    def boom(ref):
        raise RuntimeError("bug")

    monkeypatch.setattr(store, "peek_connection", boom)
    with pytest.raises(RuntimeError):
        ss._mail_connected()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ss, "_mail_connected", lambda: False)
    monkeypatch.setattr(
        "gaia.ui.routers.skills.list_skills",
        lambda: {"skills": [{"name": n} for n in STARTER], "invalid": []},
    )
    app = FastAPI()
    app.include_router(onboarding.router)
    app.dependency_overrides[get_db] = lambda: _Db([])
    return TestClient(app)


def test_start_endpoint(client):
    body = client.get("/api/onboarding/start").json()
    assert len(body["cards"]) == ss.MAX_CARDS
    assert body["working_folder"]["gaia_folder"] is False


def test_start_endpoint_says_why_when_the_agent_cannot_list_skills(client, monkeypatch):
    from fastapi import HTTPException

    def fail():
        raise HTTPException(
            status_code=503, detail="The GAIA agent could not be started"
        )

    monkeypatch.setattr("gaia.ui.routers.skills.list_skills", fail)
    body = client.get("/api/onboarding/start").json()
    assert body["cards"] == []
    assert body["skills_error"] == "The GAIA agent could not be started"
    assert body["working_folder"]["name"]
