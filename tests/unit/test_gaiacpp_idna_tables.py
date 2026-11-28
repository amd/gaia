# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""gaiacpp checks an address's domain with the same Unicode data as Python.

``cpp/src/idna_tables.inc`` is written by ``util/gen_cpp_idna_tables.py`` from
the ``idna`` package and ``unicodedata``. When this Python has the versions the
file records, regenerating it must give the committed file byte for byte.
"""

import importlib.util
import re
import sys
import unicodedata
from pathlib import Path

import idna
import pytest
from idna import idnadata

_ROOT = Path(__file__).resolve().parents[2]
_TABLES = _ROOT / "cpp" / "src" / "idna_tables.inc"


def _generator():
    path = _ROOT / "util" / "gen_cpp_idna_tables.py"
    spec = importlib.util.spec_from_file_location("gen_cpp_idna_tables", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recorded():
    first = _TABLES.read_text(encoding="utf-8").splitlines()[0]
    match = re.search(
        r"from Python (\S+) \(unicodedata (\S+)\) and idna (\S+) \(idnadata (\S+)\)",
        first,
    )
    assert match, f"no versions on the first line of {_TABLES}"
    return match.groups()


def test_the_tables_are_what_this_python_and_idna_give():
    _, unidata, idna_version, idnadata_version = _recorded()
    here = (unicodedata.unidata_version, idna.__version__, idnadata.__version__)
    if here != (unidata, idna_version, idnadata_version):
        pytest.skip(
            f"tables come from unicodedata {unidata} and idna {idna_version}; "
            f"this Python has {here[0]} and idna {here[1]}"
        )
    generated = _generator().tables()
    committed = _TABLES.read_text(encoding="utf-8")
    # The header names the exact Python patch release, which may differ.
    assert (
        generated.split("\n", 1)[1] == committed.split("\n", 1)[1]
    ), f"{_TABLES} is stale: run `{sys.executable} util/gen_cpp_idna_tables.py`"
