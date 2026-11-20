# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""No code reads, writes or deletes a keyring entry without the backend check.

``gaia.connectors.keyring_access`` is the only module allowed to call keyring's
``get_password`` / ``set_password`` / ``delete_password``.
Anything else must go through it, so a weak backend is refused for every
feature, not just the ones someone remembered to check.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HELPER = REPO / "src" / "gaia" / "connectors" / "keyring_access.py"
KEYRING_CALLS = frozenset({"get_password", "set_password", "delete_password"})


def _scanned_files():
    yield from (REPO / "src" / "gaia").rglob("*.py")
    yield from (REPO / "hub" / "agents").glob("*/python/**/*.py")


def _violations(path: Path, tree: ast.AST):
    rel = path.relative_to(REPO).as_posix()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in KEYRING_CALLS
        ):
            yield f"{rel}:{node.lineno} calls .{node.func.attr}()"
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "keyring"
        ):
            for alias in node.names:
                if alias.name in KEYRING_CALLS:
                    yield f"{rel}:{node.lineno} imports {alias.name} from {node.module}"


def test_only_the_helper_touches_keyring_entries():
    found = []
    for path in _scanned_files():
        if path.resolve() == HELPER.resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.extend(_violations(path, tree))
    assert not found, (
        "Read, write and delete keyring entries through "
        "gaia.connectors.keyring_access (read/write/delete), which refuses a "
        "weak keyring backend first:\n  " + "\n  ".join(found)
    )


def test_the_scan_catches_a_direct_call():
    samples = [
        "import keyring\nkeyring.get_password('s', 'u')",
        "from gaia.connectors._keyring import keyring\nkeyring.set_password('s', 'u', 'v')",
        "def k():\n    import keyring\n    return keyring\nk().delete_password('s', 'u')",
        "from keyring import get_password",
        "from keyring.core import set_password as sp",
        "import keyring as kr\nkr.delete_password('s', 'u')",
    ]
    for sample in samples:
        assert list(_violations(REPO / "x.py", ast.parse(sample))), sample


def test_the_scan_ignores_helper_calls_and_backend_definitions():
    clean = (
        "from gaia.connectors import keyring_access\n"
        "keyring_access.read('s', 'u')\n"
        "class B:\n"
        "    def get_password(self, service, username):\n"
        "        return None\n"
    )
    assert not list(_violations(REPO / "x.py", ast.parse(clean)))
