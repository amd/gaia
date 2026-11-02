# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The sales CSV is committed ground truth; --generate-corpus only validates (#4014)."""

import csv
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict

import pytest

from gaia.eval import runner

CSV = runner.CORPUS_DIR / "documents" / "sales_data_2025.csv"
HEADER = [
    "month",
    "salesperson",
    "product",
    "units_sold",
    "unit_price",
    "revenue",
    "region",
]


def _rows():
    with CSV.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _facts():
    manifest = json.loads(runner.MANIFEST.read_text(encoding="utf-8"))
    doc = next(d for d in manifest["documents"] if d["id"] == "sales_data")
    return {f["id"]: f["answer"] for f in doc["facts"]}


# --- the committed CSV agrees with the facts scored against it ---------------


def test_sales_csv_header_and_size():
    with CSV.open(encoding="utf-8", newline="") as f:
        assert next(csv.reader(f)) == HEADER
    assert len(_rows()) == 25


def test_revenue_column_is_units_times_price():
    for row in _rows():
        assert int(row["revenue"]) == int(row["units_sold"]) * int(row["unit_price"])


def test_q1_total_revenue_matches_manifest():
    total = sum(int(r["revenue"]) for r in _rows())
    assert total == 340000
    assert "$340,000" in _facts()["q1_total_revenue"]


def test_top_salesperson_matches_manifest():
    by_person = defaultdict(int)
    for r in _rows():
        by_person[r["salesperson"]] += int(r["revenue"])
    top, revenue = max(by_person.items(), key=lambda kv: kv[1])
    assert (top, revenue) == ("Sarah Chen", 70000)
    # Not a tie: the scored answer names one person.
    assert sorted(by_person.values())[-2] < revenue
    assert _facts()["top_salesperson"] == "Sarah Chen with $70,000"


def test_march_best_seller_matches_manifest():
    units = defaultdict(int)
    revenue = defaultdict(int)
    for r in _rows():
        if r["month"] == "March 2025":
            units[r["product"]] += int(r["units_sold"])
            revenue[r["product"]] += int(r["revenue"])
    product = max(units, key=units.get)
    assert (product, units[product], revenue[product]) == ("Widget Pro X", 150, 30000)
    assert (
        _facts()["top_product_march"]
        == "Widget Pro X with 150 units and $30,000 revenue"
    )


def test_no_generator_can_overwrite_the_csv():
    # Both shipped generators wrote a different 500-row dataset over this file.
    assert not list(runner.CORPUS_DIR.glob("gen_sales_csv*.py"))


# --- --generate-corpus validates, never rewrites ------------------------------


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    (root / "documents").mkdir(parents=True)
    (root / "adversarial").mkdir()
    (root / "documents" / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (root / "adversarial" / "b.txt").write_text("trap", encoding="utf-8")
    manifest = {
        "documents": [{"id": "a", "filename": "a.csv", "facts": [{"id": "f"}]}],
        "adversarial_documents": [{"id": "b", "filename": "b.txt"}],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(runner, "CORPUS_DIR", root)
    monkeypatch.setattr(runner, "MANIFEST", root / "manifest.json")

    def no_subprocess(*_a, **_k):
        raise AssertionError("--generate-corpus must not run a generator")

    monkeypatch.setattr(runner.subprocess, "run", no_subprocess)
    return root


def _write_manifest(root, manifest):
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_complete_corpus_validates_without_touching_files(corpus, capsys):
    before = hashlib.sha256((corpus / "documents" / "a.csv").read_bytes()).hexdigest()
    runner.generate_corpus()
    after = hashlib.sha256((corpus / "documents" / "a.csv").read_bytes()).hexdigest()
    assert before == after
    out = capsys.readouterr().out
    assert "All manifest files present" in out
    assert "1 documents, 1 adversarial, 1 facts" in out


def test_missing_document_fails(corpus):
    (corpus / "documents" / "a.csv").unlink()
    with pytest.raises(RuntimeError, match=r"1 file\(s\).*a\.csv"):
        runner.generate_corpus()


def test_missing_adversarial_document_fails(corpus):
    (corpus / "adversarial" / "b.txt").unlink()
    with pytest.raises(RuntimeError, match="b.txt"):
        runner.generate_corpus()


def test_document_listed_under_the_wrong_folder_fails(corpus):
    _write_manifest(
        corpus,
        {"documents": [{"id": "b", "filename": "b.txt"}], "adversarial_documents": []},
    )
    with pytest.raises(RuntimeError, match="b.txt"):
        runner.generate_corpus()


@pytest.mark.parametrize("entry", [{"id": "z"}, {"id": "z", "filename": ""}])
def test_entry_without_a_filename_fails(corpus, entry):
    # An empty name resolves to the folder itself, which used to "exist".
    _write_manifest(corpus, {"documents": [entry], "adversarial_documents": []})
    with pytest.raises(RuntimeError, match="'z' has no filename"):
        runner.generate_corpus()


def test_directory_in_place_of_a_file_fails(corpus):
    (corpus / "documents" / "a.csv").unlink()
    (corpus / "documents" / "a.csv").mkdir()
    with pytest.raises(RuntimeError, match="a.csv"):
        runner.generate_corpus()


def test_missing_manifest_fails(corpus):
    (corpus / "manifest.json").unlink()
    with pytest.raises(RuntimeError, match="manifest not found"):
        runner.generate_corpus()


def test_cli_validates_the_real_corpus():
    csv_before = CSV.read_bytes()
    # Pin the child to this checkout's source, not whichever one is installed.
    env = {**os.environ, "PYTHONPATH": str(runner.REPO_ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "gaia.cli", "eval", "agent", "--generate-corpus"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
        cwd=str(runner.REPO_ROOT),
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "All manifest files present" in proc.stdout
    assert CSV.read_bytes() == csv_before
