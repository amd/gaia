# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Consent boundaries and durable context records."""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from gaia.engineering.service import EngineeringService
from gaia.engineering.store import JobStore, clean_context


def test_developer_mode_is_required_before_any_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("GAIA_DEVELOPER_MODE", raising=False)
    path = tmp_path / "private"
    with pytest.raises(PermissionError, match="developer mode"):
        EngineeringService(path)
    assert not path.exists()
    monkeypatch.setenv("GAIA_DEVELOPER_MODE", "1")
    with pytest.raises(PermissionError):
        EngineeringService(path, developer_mode=False)
    assert not path.exists()


def test_grants_recipient_expiry_revocation_and_cursor(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("claude", "Bad citations", "private-otter-729", {})
    assert (
        store.context(job["id"], "claude")["evidence"][0]["text"] == "private-otter-729"
    )
    with pytest.raises(PermissionError):
        store.context(job["id"], "codex")
    store.append(job["id"], "second revision feedback")
    assert [e["seq"] for e in store.context(job["id"], "claude", 1)["evidence"]] == [2]
    store.revoke(job["id"])
    with pytest.raises(PermissionError):
        store.context(job["id"], "claude", 1)
    fresh = store.create("codex", "Case", "x", {})
    store.update(fresh["id"], lambda item: item["grant"].update(expires_at=0))
    with pytest.raises(PermissionError):
        store.context(fresh["id"], "codex")


def test_atomic_concurrent_feedback_preserves_all_updates(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("codex", "Task", "initial", {})
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i: store.append(job["id"], f"feedback {i}"), range(12)))
    record = store.read(job["id"])
    assert len(record["evidence"]) == 13
    assert record["revision"] == 13
    assert [e["seq"] for e in record["evidence"]] == list(range(1, 14))
    assert os.stat(store.path(job["id"])).st_mode & 0o777 == 0o600


def test_revision_conflict_corruption_and_path_refusal(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("claude", "Task", "x", {})
    with pytest.raises(ValueError, match="changed"):
        store.update(job["id"], lambda item: None, expected_revision=0)
    with pytest.raises(ValueError, match="ID"):
        store.read("../../secret")
    store.path(job["id"]).write_text("{broken")
    with pytest.raises(json.JSONDecodeError):
        store.read(job["id"])
    assert store.path(job["id"]).read_text() == "{broken"


def test_context_limits_and_redaction():
    assert (
        clean_context("\x1b[31mhello\x1b[0m\nAPI_KEY=topsecret")
        == "hello\nAPI_KEY=[REDACTED]"
    )
    # JSON- and dict-shaped secrets, the shape most logs and config dumps take.
    assert clean_context('{"api_key": "abc123"}') == '{"api_key": [REDACTED]'
    assert clean_context("{'password': 'hunter2'}") == "{'password': [REDACTED]"
    with pytest.raises(ValueError):
        clean_context("x" * (128 * 1024 + 1))
    with pytest.raises(ValueError):
        clean_context("")


@pytest.mark.parametrize("after_seq", [True, -1, "1", 1.0])
def test_context_cursor_must_be_a_nonnegative_int(tmp_path, after_seq):
    with pytest.raises(ValueError, match="nonnegative integer"):
        JobStore(tmp_path).context("job", "codex", after_seq=after_seq)


def test_pairing_has_no_access_and_no_server_consent_tools(tmp_path):
    service = EngineeringService(tmp_path, developer_mode=True)
    result = service.connection("codex")
    assert service.store.jobs.is_dir()
    assert list(service.store.jobs.iterdir()) == []
    assert "token" not in json.dumps(result)
    with pytest.raises(PermissionError):
        service.authenticate("codex", "not-a-token")
    job = service.share("codex", "Task", "Selected private context")
    with pytest.raises(ValueError, match="diagnosis"):
        service.approve_code(
            job["id"], expected_revision=service.status(job["id"])["revision"]
        )
    service.report_diagnosis(
        job["id"], "codex", "Model configuration is correct; narrow patch proposed"
    )
    service.approve_code(
        job["id"], expected_revision=service.status(job["id"])["revision"]
    )
    assert service.status(job["id"])["code_approved"]
    service.revoke(job["id"])
    with pytest.raises(PermissionError):
        service.report_result(job["id"], "codex", "Cannot report after revocation")


def test_stale_approval_and_separate_profile_roots(tmp_path):
    first = EngineeringService(tmp_path / "first", developer_mode=True)
    second = EngineeringService(tmp_path / "second", developer_mode=True)
    assert first.repository.root != second.repository.root
    job = first.share("claude", "Patch scope", "selected evidence")
    first.report_diagnosis(job["id"], "claude", "original diagnosis")
    displayed = first.status(job["id"])
    first.report_diagnosis(job["id"], "claude", "changed diagnosis")
    with pytest.raises(ValueError, match="changed"):
        first.approve_code(job["id"], expected_revision=displayed["revision"])
    assert not first.status(job["id"])["code_approved"]


def test_frozen_sidecar_never_becomes_python_launcher(tmp_path, monkeypatch):
    import sys

    service = EngineeringService(tmp_path, developer_mode=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(RuntimeError, match="Python installation"):
        service.connection("codex")
    assert not (tmp_path / "clients" / "codex.json").exists()
