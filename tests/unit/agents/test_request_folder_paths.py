# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Relative outputs the request names live in the folder it named, not GAIA's cwd.

The turn shapes are from the long-horizon ladder's pipeline task (#8094): the
user names ``<work>\\project\\`` and then writes ``project\\data\\stats.csv``,
while the agent's working directory is GAIA's own ``.gaia\\host`` folder.
"""

import os
import subprocess
import sys

import pytest

from gaia.agents.base.agent import _claims_file_write
from gaia.agents.base.completion import CompletionEvidence

_SEP = os.sep


def _pipeline_request(work):
    project, incoming = work / "project", work / "incoming"
    return (
        f"Set up a small analysis project for me in {project}{_SEP}: copy the raw "
        f"sensor files from {incoming}{_SEP} into project{_SEP}data{_SEP}raw{_SEP}, "
        "then for each sensor compute the number of readings, the mean (2 decimals) "
        f"and the max, write them to project{_SEP}data{_SEP}stats.csv (header "
        "sensor,n,mean,max, one row per sensor, sorted by sensor), write "
        f"project{_SEP}report.md that names the hottest sensor by mean, and finally "
        "verify stats.csv against the raw files before telling me you're done."
    )


_ANSWER = (
    "Done. What's there (confirmed on disk): `data\\raw\\` has 2 sensor files, "
    "`data\\stats.csv` has one row per sensor, and `report.md` names the hottest "
    "sensor."
)


@pytest.fixture
def work(tmp_path):
    incoming = tmp_path / "work" / "L4-f-r1" / "incoming"
    incoming.mkdir(parents=True)
    for name, rows in (("s01", "t,25.0\nt,27.9\n"), ("s02", "t,21.0\nt,22.0\n")):
        (incoming / f"{name}.csv").write_text("timestamp,celsius\n" + rows)
    (tmp_path / ".gaia" / "host").mkdir(parents=True)
    return tmp_path / "work" / "L4-f-r1"


def _ledger(work):
    """GAIA's cwd is its own folder; the user granted the work folder for the chat."""
    from gaia.security import PathValidator

    host = work.parent.parent / ".gaia" / "host"
    validator = PathValidator(allowed_paths=[str(work)])
    return CompletionEvidence(_pipeline_request(work), str(host), validator=validator)


def _run_python(ledger, code):
    args = {"code": code}
    before = ledger.snapshot("run_python", args)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    result = {"status": "success", "stdout": done.stdout, "return_code": 0}
    ledger.record("run_python", args, result, True, before=before)


def _pipeline_code(work):
    """The recorded shape: the project folder in a variable, outputs joined to it."""
    return (
        "import os, shutil\n"
        f"src = r'{work / 'incoming'}'\n"
        f"proj = r'{work / 'project'}'\n"
        "raw = os.path.join(proj, 'data', 'raw')\n"
        "os.makedirs(raw, exist_ok=True)\n"
        "for name in sorted(os.listdir(src)):\n"
        "    shutil.copy2(os.path.join(src, name), os.path.join(raw, name))\n"
        "with open(os.path.join(proj, 'data', 'stats.csv'), 'w') as f:\n"
        "    f.write('sensor,n,mean,max\\ns01,2,26.45,27.9\\ns02,2,21.50,22.0\\n')\n"
        "with open(os.path.join(proj, 'report.md'), 'w') as f:\n"
        "    f.write('Hottest sensor: s01\\n')\n"
        "print('done')\n"
    )


def _no_write_gaps(found):
    return [gap for gap in found if "No successful write" in gap]


def test_a_script_write_to_the_named_project_folder_is_credited(work):
    ledger = _ledger(work)
    _run_python(ledger, _pipeline_code(work))
    assert (work / "project" / "data" / "stats.csv").is_file()
    found = ledger.gaps(_ANSWER, _claims_file_write)
    assert _no_write_gaps(found) == [], found
    assert not any(".gaia" in gap for gap in found), found


def test_file_tool_writes_to_the_named_project_folder_are_credited(work):
    """The deny-scripts shape: each copy and the stats file through write_file."""
    ledger = _ledger(work)
    raw = work / "project" / "data" / "raw"
    raw.mkdir(parents=True)
    outputs = [
        raw / "s01.csv",
        raw / "s02.csv",
        work / "project" / "data" / "stats.csv",
    ]
    for path in outputs:
        path.write_text("sensor,n,mean,max\n")
        ledger.record(
            "write_file", {"file_path": str(path)}, {"file_path": str(path)}, True
        )
    found = ledger.gaps(_ANSWER, _claims_file_write)
    assert _no_write_gaps(found) == [], found
    assert not any(".gaia" in gap for gap in found), found


def test_the_named_project_folder_is_where_a_missing_output_is_looked_for(work):
    ledger = _ledger(work)
    found = _no_write_gaps(ledger.gaps(_ANSWER, _claims_file_write))
    assert found, "nothing was written, so the request is still open"
    assert not any(".gaia" in gap for gap in found), found
    assert any(
        str(work / "project" / "data" / "stats.csv").lower() in gap.lower()
        for gap in found
    ), found


def test_a_relative_output_written_elsewhere_under_the_same_name_is_credited(tmp_path):
    """No folder ties ``out/res.csv`` down, so the agent's own resolution stands."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "in.csv").write_text("a\n1\n")
    elsewhere = tmp_path / "cli-cwd" / "out" / "res.csv"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("a\n1\n")
    ledger = CompletionEvidence(
        f"Read {data / 'in.csv'} and save the totals to out{_SEP}res.csv",
        str(tmp_path / "server"),
    )
    ledger.record(
        "write_file", {"file_path": str(elsewhere)}, {"file_path": str(elsewhere)}, True
    )
    assert _no_write_gaps(ledger.gaps("Saved.", _claims_file_write)) == []


def test_a_write_in_the_working_folder_still_counts_when_an_input_shares_the_name(
    tmp_path,
):
    """``docs/report.md`` beside an input under ``upstream/docs``: either place counts."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    upstream = tmp_path / "upstream" / "docs" / "y.md"
    request = (
        f"Compare docs{_SEP}x.md with {upstream} and save the comparison to "
        f"docs{_SEP}report.md"
    )
    ledger = CompletionEvidence(request, str(repo))
    report = repo / "docs" / "report.md"
    report.write_text("same\n")
    ledger.record(
        "write_file", {"file_path": str(report)}, {"file_path": str(report)}, True
    )
    assert _no_write_gaps(ledger.gaps("Saved.", _claims_file_write)) == []


def test_a_relative_output_nobody_wrote_is_flagged_as_the_user_typed_it(tmp_path):
    """GAIA's own folder is not somewhere the user or the model can act on."""
    host = tmp_path / ".gaia" / "host"
    ledger = CompletionEvidence(f"Save the totals to out{_SEP}res.csv", str(host))
    found = _no_write_gaps(ledger.gaps("Saved.", _claims_file_write))
    assert found == [
        f"No successful write to `out{_SEP}res.csv` is recorded for this turn."
    ]


_INVENTORY = (
    "I need an inventory of every file under {root}{sep} (including subfolders). "
    "Write {root}{sep}inventory.csv with the header path,size_bytes,line_count,ref_code "
    "and one row per file (path relative to that folder, forward slashes). Then write "
    "{root}{sep}summary.txt with three lines: total_files=<n>, total_bytes=<sum>, "
    "largest_file=<path>. Don't change or delete the original files."
)


def _inventory_ledger(tmp_path):
    root = tmp_path / "work" / "L3-a-r2"
    root.mkdir(parents=True)
    (root / "a.txt").write_text("AB-1234-CD\n")
    request = _INVENTORY.format(root=root, sep=_SEP)
    return CompletionEvidence(request, str(tmp_path / ".gaia" / "host")), root


def _inventory_code(root):
    return (
        "import os\n"
        f"root = r'{root}'\n"
        "with open(os.path.join(root, 'inventory.csv'), 'w') as f:\n"
        "    f.write('path,size_bytes,line_count,ref_code\\na.txt,11,1,AB-1234-CD\\n')\n"
        "with open(os.path.join(root, 'summary.txt'), 'w') as f:\n"
        "    f.write('total_files=1\\ntotal_bytes=11\\nlargest_file=a.txt\\n')\n"
        "print('ok')\n"
    )


def _read_whole(ledger, path):
    content = path.read_text()
    result = {"file_path": str(path), "content": content}
    ledger.delivered("read_file", {"file_path": str(path)}, result, result)


_PATHLESS = "The requested output file has no recorded successful write"


def test_a_script_written_inventory_the_request_named_fulfils_the_save(tmp_path):
    ledger, root = _inventory_ledger(tmp_path)
    _run_python(ledger, _inventory_code(root))
    for name in ("inventory.csv", "summary.txt"):
        _read_whole(ledger, root / name)
    found = ledger.gaps(
        "Both files are written. Every file is covered.", _claims_file_write
    )
    assert not any(_PATHLESS in gap for gap in found), found


def test_a_script_that_wrote_no_inventory_still_leaves_the_save_open(tmp_path):
    ledger, _ = _inventory_ledger(tmp_path)
    _run_python(ledger, "print('ok')")
    found = ledger.gaps("Both files are written.", _claims_file_write)
    assert any(_PATHLESS in gap for gap in found), found
