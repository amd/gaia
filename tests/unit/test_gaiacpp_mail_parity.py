# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""gaiacpp's mail fixtures still say what the Python mail code does today.

The C++ mail tests replay files written from the Python email tools, backends
and screens. A change to that Python code must regenerate them, or gaiacpp keeps
passing against behaviour Python no longer has. Each generator reruns here into
a temp folder and its output must equal the committed file.
"""

import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TABLES = _ROOT / "cpp" / "src" / "mail_text_tables.inc"
_FIXTURES = _ROOT / "cpp" / "tests" / "fixtures"

_GENERATORS = [
    (
        "util/gen_cpp_mail_screens.py",
        [_TABLES, _FIXTURES / "mail_screens_parity.json"],
    ),
    (
        "cpp/tests/fixtures/mail/make_email_tools_parity.py",
        [_FIXTURES / "mail" / "email_tools_parity.json"],
    ),
    (
        "cpp/tests/fixtures/mail/make_python_parity.py",
        [_FIXTURES / "mail" / "python_parity.json"],
    ),
]


def _recorded_runtime():
    header = _TABLES.read_text(encoding="utf-8").splitlines()[0]
    found = re.search(r"from Python (\d+\.\d+) \(Unicode ([\d.]+)\)", header)
    assert found, f"no Python/Unicode version in the first line of {_TABLES}"
    return found.group(1), found.group(2)


@pytest.mark.parametrize(
    "script, outputs", _GENERATORS, ids=[Path(s).stem for s, _ in _GENERATORS]
)
def test_the_committed_mail_fixtures_match_a_fresh_run(script, outputs, tmp_path):
    python, unicode = _recorded_runtime()
    here = ".".join(str(part) for part in sys.version_info[:2])
    if (python, unicode) != (here, unicodedata.unidata_version):
        pytest.skip(
            f"the fixtures were written by Python {python} (Unicode {unicode}); this is "
            f"Python {here} (Unicode {unicodedata.unidata_version}), whose answers can differ"
        )
    # A subprocess, since the generators patch module globals while they run.
    run = subprocess.run(
        [sys.executable, str(_ROOT / script), str(tmp_path)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    for committed in outputs:
        fresh = (tmp_path / committed.name).read_bytes()
        assert fresh == committed.read_bytes(), (
            f"{committed.relative_to(_ROOT)} is stale: the Python it mirrors changed. "
            f"Run `python {script}` from the repo root and commit the result, then make "
            "the C++ mail code pass against it."
        )
