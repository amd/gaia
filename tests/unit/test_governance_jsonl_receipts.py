# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for JsonlReceiptService."""

from __future__ import annotations

import json
from dataclasses import replace
from math import nan

import pytest

from gaia.governance import GaiaGovernanceError
from gaia.governance.receipt_service import JsonlReceiptService
from gaia.governance.schemas import ReceiptRecord


def _record(rid: str = "rcpt_test_1") -> ReceiptRecord:
    return ReceiptRecord(
        receipt_id=rid,
        workflow_id="wf_1",
        checkpoint_id=None,
        decision="BLOCK",
        policy_version="v0",
        actor_id="alice",
        validator_set_id=None,
        created_at="2026-04-19T00:00:00+00:00",
        payload_hash="deadbeef",
        metadata={"constitution_hash": "c1"},
    )


def test_issue_writes_one_line_per_receipt(tmp_path):
    path = tmp_path / "receipts.jsonl"
    svc = JsonlReceiptService(path)
    svc.issue_receipt(_record("rcpt_a"))
    svc.issue_receipt(_record("rcpt_b"))
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert {p["receipt_id"] for p in parsed} == {"rcpt_a", "rcpt_b"}


def test_get_receipt_reads_from_cache_and_disk(tmp_path):
    path = tmp_path / "audit.jsonl"
    svc = JsonlReceiptService(path)
    svc.issue_receipt(_record("rcpt_cached"))

    # Fresh service on same file must still find the receipt via cold read.
    svc2 = JsonlReceiptService(path)
    got = svc2.get_receipt("rcpt_cached")
    assert got.receipt_id == "rcpt_cached"
    assert got.decision == "BLOCK"


def test_missing_receipt_raises(tmp_path):
    svc = JsonlReceiptService(tmp_path / "none.jsonl")
    with pytest.raises(GaiaGovernanceError):
        svc.get_receipt("rcpt_missing")


def test_iter_yields_all_records(tmp_path):
    svc = JsonlReceiptService(tmp_path / "r.jsonl")
    svc.issue_receipt(_record("rcpt_1"))
    svc.issue_receipt(_record("rcpt_2"))
    svc.issue_receipt(_record("rcpt_3"))
    seen = {r.receipt_id for r in svc}
    assert seen == {"rcpt_1", "rcpt_2", "rcpt_3"}


def test_parent_directory_auto_created(tmp_path):
    path = tmp_path / "nested" / "deeper" / "r.jsonl"
    svc = JsonlReceiptService(path)
    svc.issue_receipt(_record("rcpt_nested"))
    assert path.exists()
    assert path.parent.is_dir()


def test_issue_rejects_non_canonical_metadata(tmp_path):
    svc = JsonlReceiptService(tmp_path / "strict.jsonl")
    record = _record("rcpt_bad")
    record = replace(record, metadata={"bad": object()})

    with pytest.raises(TypeError):
        svc.issue_receipt(record)


def test_issue_rejects_non_finite_numbers(tmp_path):
    svc = JsonlReceiptService(tmp_path / "strict_float.jsonl")
    record = _record("rcpt_nan")
    record = replace(record, metadata={"score": nan})

    with pytest.raises(ValueError):
        svc.issue_receipt(record)
