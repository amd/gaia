# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The code-retrieval benchmark's pieces that need no embedder: labels, metrics,
the grep baseline, the gate, and how robustness outcomes are classified."""

import shutil
import subprocess

import pytest

from gaia.eval.code_retrieval import gold, lexical, metrics, report, robustness
from gaia.eval.code_retrieval.runner import suite_parts
from gaia.eval.code_retrieval.suites import SUITES

BASE = """import os


class Session:
    def request(self, method):
        timeout = 10
        return method


def merge_setting(a, b):
    return a or b


def other():
    pass
"""

# Edits Session.request (line 6), inserts inside merge_setting, and adds a new
# top-level function after other() — which must not be credited to other().
PATCH = """diff --git a/pkg/sessions.py b/pkg/sessions.py
--- a/pkg/sessions.py
+++ b/pkg/sessions.py
@@ -5,3 +5,3 @@ class Session:
     def request(self, method):
-        timeout = 10
+        timeout = 30
         return method
@@ -10,2 +10,3 @@ def request(self, method):
 def merge_setting(a, b):
+    b = b or {}
     return a or b
@@ -14,2 +15,6 @@ def merge_setting(a, b):
 def other():
     pass
+
+
+def added():
+    pass
diff --git a/pkg/new.py b/pkg/new.py
new file mode 100644
--- /dev/null
+++ b/pkg/new.py
@@ -0,0 +1,2 @@
+def brand_new():
+    pass
diff --git a/docs/notes.md b/docs/notes.md
--- a/docs/notes.md
+++ b/docs/notes.md
@@ -1 +1 @@
--- old heading
+-- new heading
"""


def test_parse_patch_reads_base_lines_and_ignores_header_lookalikes():
    changes = {c.path: c for c in gold.parse_patch(PATCH)}
    assert set(changes) == {"pkg/sessions.py", "pkg/new.py", "docs/notes.md"}
    assert changes["pkg/sessions.py"].changed_lines == {6}
    assert (10, "    b = b or {}") in changes["pkg/sessions.py"].insertions
    assert changes["pkg/new.py"].created
    # "--- old heading" is a removed line inside a hunk, not a file header.
    assert changes["docs/notes.md"].changed_lines == {1}


def test_gold_credits_innermost_symbols_and_skips_created_files():
    sources = {"pkg/sessions.py": BASE, "docs/notes.md": "-- old heading\n"}
    g = gold.gold_from_patch(PATCH, sources.get)
    assert g.files == ["pkg/sessions.py", "docs/notes.md"]
    assert g.created == ["pkg/new.py"]
    assert g.symbols == [
        ("pkg/sessions.py", "Session.request"),
        ("pkg/sessions.py", "merge_setting"),
    ]


CLASS_SRC = """class C:
    def a(self):
        x = 1
        return x

    def b(self):
        return 2


@decorated
def last():
    return 3
"""


def _symbols(patch):
    return gold.gold_from_patch(patch, {"m.py": CLASS_SRC}.get).symbols


def _patch(hunk):
    return "--- a/m.py\n+++ b/m.py\n" + hunk


def test_replacing_a_methods_last_line_credits_only_the_method():
    hunk = "@@ -3,2 +3,2 @@\n         x = 1\n-        return x\n+        return x + 1\n"
    assert _symbols(_patch(hunk)) == [("m.py", "C.a")]


def test_appended_lines_belong_to_the_block_they_are_indented_into():
    into_method = "@@ -4,1 +4,2 @@\n         return x\n+        print(x)\n"
    assert _symbols(_patch(into_method)) == [("m.py", "C.a")]
    new_method = (
        "@@ -7,1 +7,4 @@\n         return 2\n+\n+    def c(self):\n+        pass\n"
    )
    assert _symbols(_patch(new_method)) == [("m.py", "C")]
    end_of_file = "@@ -12,1 +12,2 @@\n     return 3\n+    # done\n"
    assert _symbols(_patch(end_of_file)) == [("m.py", "last")]
    new_function = "@@ -12,1 +12,4 @@\n     return 3\n+\n+def more():\n+    pass\n"
    assert _symbols(_patch(new_function)) == []


def test_a_decorator_change_credits_the_decorated_function():
    hunk = "@@ -10,1 +10,1 @@\n-@decorated\n+@decorated(True)\n"
    assert _symbols(_patch(hunk)) == [("m.py", "last")]


def test_gold_fails_loudly_when_a_modified_file_is_missing_at_base():
    with pytest.raises(gold.GoldError, match="absent at the base"):
        gold.gold_from_patch(PATCH, lambda _p: None)


def test_parse_patch_rejects_garbage_inside_a_hunk():
    bad = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n x\n*oops\n"
    with pytest.raises(gold.GoldError, match="unexpected line"):
        gold.parse_patch(bad)


def test_metrics():
    ranked = ["a", "b", "c", "d"]
    assert metrics.recall_at(ranked, ["b", "z"], 1) == 0
    assert metrics.recall_at(ranked, ["b", "z"], 2) == 0.5
    assert metrics.reciprocal_rank(ranked, ["c"]) == pytest.approx(1 / 3)
    assert metrics.reciprocal_rank(ranked, ["z"]) == 0
    assert metrics.dedupe(["a", "b", "a"]) == ["a", "b"]
    assert metrics.percentile([5, 1, 3, 2, 4], 50) == 3
    assert metrics.percentile([float(i) for i in range(1, 101)], 95) == 95
    with pytest.raises(ValueError):
        metrics.recall_at(ranked, [], 5)


def test_identifiers_keep_code_words_and_drop_prose():
    text = (
        "Calling `Session.request` breaks merge_hooks in requests/sessions.py; "
        "the QuerySet is fine. This is a bug in the code."
    )
    found = lexical.identifiers(text)
    for word in ("Session", "request", "merge_hooks", "QuerySet"):
        assert word in found
    for word in ("Calling", "bug", "code", "This"):
        assert word not in found
    assert lexical.mentioned_paths(text) == ["requests/sessions.py"]


def test_chunks_resolve_to_qualified_symbols(tmp_path):
    (tmp_path / "m.py").write_text(
        "class A:\n    def __init__(self):\n        pass\n\n\n"
        "class B:\n    def __init__(self):\n        pass\n",
        encoding="utf-8",
    )
    lex = lexical.LexicalSearch(tmp_path, ["m.py"])
    assert lex.chunk_symbol("m.py", "__init__", 7) == ("m.py", "B.__init__")
    assert lex.chunk_symbol("m.py", "__init__ (part 2/2)", 3) == ("m.py", "A.__init__")
    assert lex.chunk_symbol("m.py", None, 1) is None


def test_traceback_paths_earn_the_named_file_bonus(tmp_path):
    text = 'File "/home/u/venv/lib/site-packages/pkg/sessions.py", line 3'
    named = lexical.mentioned_paths(text)
    assert any(n.endswith("/pkg/sessions.py") for n in named)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_lexical_ranks_the_file_holding_rare_identifiers(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "sessions.py").write_text(BASE, encoding="utf-8")
    for i in range(6):
        (repo / "pkg" / f"filler{i}.py").write_text(
            "import os\n\n\ndef helper():\n    return os\n", encoding="utf-8"
        )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    corpus = [f"pkg/filler{i}.py" for i in range(6)] + ["pkg/sessions.py"]
    files, symbols, used = lexical.LexicalSearch(repo, corpus).rank(
        "merge_setting returns the wrong value"
    )
    assert files[0] == "pkg/sessions.py"
    assert symbols[0] == ("pkg/sessions.py", "merge_setting")
    traceback = 'File "/opt/venv/site-packages/pkg/filler3.py", line 2, in helper'
    files, _, _ = lexical.LexicalSearch(repo, corpus).rank(traceback)
    assert files[0] == "pkg/filler3.py"
    assert used == ["merge_setting"]


def _result(suite="pr", r10=0.8, ids=("a", "b"), passed=True, correct=True):
    summary = {
        "queries": len(ids),
        "queries_with_symbols": len(ids),
        "semantic": {m: r10 for m in report.GATED_METRICS} | {"symbol_mrr": r10},
        "lexical": {m: 0.5 for m in report.GATED_METRICS} | {"symbol_mrr": 0.5},
    }
    return {
        "suite": suite,
        "quality": {
            "queries": [{"id": i, "gold": {"not_in_index": []}} for i in ids],
            "summary": {"all": summary},
        },
        "incremental": {"correct": correct},
        "robustness": {
            "scenarios": [{"name": "faiss-empty", "passed": passed, "recovers": True}],
            "tree": [{"name": "huge", "status": "pass", "detail": ""}],
        },
    }


def test_compare_passes_within_tolerance_and_flags_real_drops():
    assert report.compare(_result(), _result(r10=0.76), 0.05)["regressions"] == []
    regs = report.compare(_result(), _result(r10=0.7), 0.05)["regressions"]
    assert any("file_recall@10" in r for r in regs)


def test_compare_flags_robustness_and_incremental_regressions():
    regs = report.compare(_result(), _result(passed=False, correct=False), 0.05)[
        "regressions"
    ]
    assert "incremental re-index drifted from a fresh index" in regs
    assert any("faiss-empty: no longer passed" in r for r in regs)


def test_compare_refuses_runs_that_are_not_comparable():
    with pytest.raises(ValueError, match="same suite"):
        report.compare(_result(suite="pr"), _result(suite="nightly"), 0.05)
    with pytest.raises(ValueError, match="different queries"):
        report.compare(_result(), _result(ids=("a", "c")), 0.05)


def test_robustness_classifies_silence_as_failure():
    baseline = [[["a.py", 1]], [["b.py", 2]]]
    assert robustness._attempt(lambda: baseline, baseline)["outcome"] == "correct"
    assert robustness._attempt(lambda: [[], []], baseline)["outcome"] == "silent-empty"
    wrong = robustness._attempt(lambda: [[["x.py", 1]], [["b.py", 2]]], baseline)
    assert wrong["outcome"] == "silent-wrong"

    def boom():
        raise RuntimeError("cache corrupt; run clear_index()")

    raised = robustness._attempt(boom, baseline)
    assert raised["outcome"] == "raised" and "clear_index" in raised["error"]
    verdict = robustness._verdict(
        {"search": {"outcome": "silent-empty"}, "reindex": {"outcome": "correct"}}
    )
    assert verdict == {"passed": False, "recovers": True}


def test_suites_define_the_parts_ci_runs():
    assert suite_parts(SUITES["pr"]) == ["quality", "incremental", "robustness"]
    assert suite_parts(SUITES["nightly"]) == ["quality", "incremental", "robustness"]
    assert suite_parts(SUITES["scale"]) == ["scale"]
    for pin in SUITES["scale"].scale:
        assert len(pin.commit) == 40


def test_cli_parses_the_code_component(monkeypatch):
    import sys

    from gaia import cli

    seen = {}
    monkeypatch.setattr(
        cli, "_handle_eval_retrieval_code", lambda a: seen.update(vars(a))
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gaia",
            "eval",
            "retrieval",
            "--component",
            "code",
            "--suite",
            "nightly",
            "--gate",
            "base.json",
            "--tolerance",
            "0.1",
            "--parts",
            "quality",
        ],
    )
    cli.main()
    assert seen["suite"] == "nightly" and seen["gate"] == "base.json"
    assert seen["tolerance"] == 0.1 and seen["parts"] == "quality"


def test_report_renders_a_partial_run_and_a_comparison():
    result = _result()
    result["environment"] = {
        "cpu": "CPU",
        "ram_gb": 1.0,
        "os": "OS",
        "embedding_model": "m",
        "lemonade_version": "v",
        "gaia_commit": None,
        "gaia_dirty": None,
    }
    result.pop("quality")
    result.pop("incremental")
    result.pop("robustness")
    result["error"] = "RuntimeError: boom"
    text = report.markdown(result)
    assert "Still running." in text and "stopped early" in text
    result["duration_s"] = 120
    result["comparison"] = report.compare(_result(), _result(r10=0.5), 0.05)
    text = report.markdown(result)
    assert "2 min." in text and "regression" in text


@pytest.mark.parametrize("name", ["dir-link-out-of-repo", "dangling-link"])
def test_links_out_of_the_tree_are_never_indexed(tmp_path, name):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    hazard = next(h for h in robustness.LINK_HAZARDS if h.name == name)
    row = robustness._link_hazard(hazard, repo, tmp_path / "work")
    # None = this machine cannot build the link (e.g. Windows without the
    # symlink privilege); it must say so, and it is never a pass.
    assert row["ok"] is not False, row["detail"]
    if row["ok"] is None:
        assert "junction" in row["detail"] or "Developer Mode" in row["detail"]


def test_file_hazards_cover_what_the_index_must_skip():
    expected = {h.name: h.expect_indexed for h in robustness.FILE_HAZARDS}
    assert expected == {
        "binary-with-code-extension": False,
        "nul-after-sniff-window": False,
        "oversized-file": False,
        "latin-1-file": True,
    }


def test_gate_sees_gold_the_index_stopped_holding():
    current = _result()
    current["quality"]["queries"][0]["gold"]["not_in_index"] = ["pkg/a.py"]
    current["quality"]["queries"][1]["skipped"] = "nothing indexed"
    regs = report.compare(_result(), current, 0.05)["regressions"]
    assert any("no longer indexed" in r and "pkg/a.py" in r for r in regs)
    assert any("now skipped" in r for r in regs)


def test_gate_fails_a_metric_that_lost_its_value():
    current = _result()
    current["quality"]["summary"]["all"]["semantic"]["symbol_recall@5"] = None
    regs = report.compare(_result(), current, 0.05)["regressions"]
    assert any("symbol_recall@5 has no value" in r for r in regs)


def test_gate_treats_every_missing_part_the_same():
    for part in ("quality", "incremental", "robustness"):
        current = _result()
        current.pop(part)
        regs = report.compare(_result(), current, 0.05)["regressions"]
        assert f"{part} did not run, so it was not compared" in regs
