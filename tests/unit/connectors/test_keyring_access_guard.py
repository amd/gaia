# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""No code reads, writes or deletes a keyring entry without the backend check.

``gaia.connectors.keyring_access`` is the only module allowed to call keyring's
``get_password`` / ``set_password`` / ``delete_password`` / ``get_credential``.
Anything else must go through it, so a weak backend is refused for every
feature, not just the ones someone remembered to check.

``keyring_access.read_for_delete`` reads on a weak backend, so only the modules
in ``READ_FOR_DELETE_ALLOWED`` may use it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HELPER = REPO / "src" / "gaia" / "connectors" / "keyring_access.py"
KEYRING_CALLS = frozenset({"get_password", "set_password", "delete_password"})
# GAIA's connector handlers also define ``get_credential(spec)``, so this one is
# flagged only on a keyring receiver or with keyring's (service, username) shape.
KEYRING_RECEIVER_CALLS = frozenset({"get_credential"})
KEYRING_IMPORTS = KEYRING_CALLS | KEYRING_RECEIVER_CALLS
READ_FOR_DELETE_ALLOWED = frozenset(
    {
        "src/gaia/connectors/keyring_access.py",
        "src/gaia/connectors/store.py",
    }
)


def _scanned_files():
    yield from (REPO / "src" / "gaia").rglob("*.py")
    yield from (REPO / "hub" / "agents").glob("*/python/**/*.py")


def _keyring_names(tree: ast.AST) -> set:
    """Local names bound to the keyring package or one of its modules."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "keyring":
                    names.add(alias.asname or "keyring")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "keyring" or module.split(".")[0] == "keyring":
                    names.add(alias.asname or alias.name)
    return names


def _is_keyring_receiver(node: ast.AST, names: set) -> bool:
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Attribute):
        return node.attr == "get_keyring" or _is_keyring_receiver(node.value, names)
    if isinstance(node, ast.Call):
        return _is_keyring_receiver(node.func, names)
    return False


def _keyring_shaped(call: ast.Call) -> bool:
    keywords = {k.arg for k in call.keywords}
    return len(call.args) >= 2 or bool(keywords & {"service", "username"})


def _violations(path: Path, tree: ast.AST):
    rel = path.relative_to(REPO).as_posix()
    names = _keyring_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in KEYRING_CALLS or (
                attr in KEYRING_RECEIVER_CALLS
                and (
                    _is_keyring_receiver(node.func.value, names)
                    or _keyring_shaped(node)
                )
            ):
                yield f"{rel}:{node.lineno} calls .{attr}()"
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "keyring"
        ):
            for alias in node.names:
                if alias.name in KEYRING_IMPORTS:
                    yield f"{rel}:{node.lineno} imports {alias.name} from {node.module}"


def _read_for_delete_uses(path: Path, tree: ast.AST):
    rel = path.relative_to(REPO).as_posix()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "read_for_delete":
            yield f"{rel}:{node.lineno} uses .read_for_delete"
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "read_for_delete":
                    yield f"{rel}:{node.lineno} imports read_for_delete"


def _parsed():
    for path in _scanned_files():
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_only_the_helper_touches_keyring_entries():
    found = []
    for path, tree in _parsed():
        if path.resolve() == HELPER.resolve():
            continue
        found.extend(_violations(path, tree))
    assert not found, (
        "Read, write and delete keyring entries through "
        "gaia.connectors.keyring_access (read/write/delete), which refuses a "
        "weak keyring backend first:\n  " + "\n  ".join(found)
    )


def test_only_allowlisted_modules_read_for_delete():
    found, used_by = [], set()
    for path, tree in _parsed():
        rel = path.relative_to(REPO).as_posix()
        uses = list(_read_for_delete_uses(path, tree))
        if uses:
            used_by.add(rel)
        if rel not in READ_FOR_DELETE_ALLOWED:
            found.extend(uses)
    assert not found, (
        "keyring_access.read_for_delete reads on a weak keyring, so only delete "
        "paths may use it. Read through keyring_access.read, or add the module "
        "to READ_FOR_DELETE_ALLOWED after review:\n  " + "\n  ".join(found)
    )
    helper = HELPER.relative_to(REPO).as_posix()
    stale = READ_FOR_DELETE_ALLOWED - used_by - {helper}
    assert not stale, f"allowlisted but no longer used: {sorted(stale)}"


def test_the_read_for_delete_scan_catches_every_spelling():
    samples = [
        "from gaia.connectors import keyring_access\n"
        "keyring_access.read_for_delete('s', 'u')",
        "from gaia.connectors.keyring_access import read_for_delete",
        "from gaia.connectors.keyring_access import read_for_delete as r",
        "from gaia.connectors import keyring_access as ka\nf = ka.read_for_delete",
        "import gaia.connectors.keyring_access\n"
        "gaia.connectors.keyring_access.read_for_delete('s', 'u')",
        "getattr(x, 'y').read_for_delete",
    ]
    for sample in samples:
        assert list(_read_for_delete_uses(REPO / "x.py", ast.parse(sample))), sample


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


def test_the_scan_catches_get_credential():
    samples = [
        "import keyring\nkeyring.get_credential('s', 'u')",
        "import keyring\nkeyring.get_credential('s', None)",
        "import keyring as kr\nkr.get_credential('s')",
        "from gaia.connectors._keyring import keyring\nkeyring.get_credential('s')",
        "import keyring.core\nkeyring.core.get_credential('s')",
        "from keyring import core\ncore.get_credential('s')",
        "import keyring\nkeyring.get_keyring().get_credential('s')",
        "from keyring import get_keyring\nget_keyring().get_credential('s')",
        "import keyring\nkeyring.get_credential(service='s', username=None)",
        "def k():\n    import keyring\n    return keyring\nk().get_credential('s', 'u')",
        "x.get_credential(service='s')",
        "from keyring import get_credential",
        "from keyring.core import get_credential as gc",
    ]
    for sample in samples:
        assert list(_violations(REPO / "x.py", ast.parse(sample))), sample


def test_the_scan_ignores_gaia_connector_get_credential():
    clean = (
        "from gaia.connectors.handler import get_credential\n"
        "async def f(handler, spec):\n"
        "    await handler.get_credential(spec, required_scopes=['openid'])\n"
        "    await handler.get_credential(spec, account_id='a')\n"
        "    await get_credential('google', agent_id='a')\n"
    )
    assert not list(_violations(REPO / "x.py", ast.parse(clean)))


def test_the_scan_ignores_helper_calls_and_backend_definitions():
    clean = (
        "from gaia.connectors import keyring_access\n"
        "keyring_access.read('s', 'u')\n"
        "class B:\n"
        "    def get_password(self, service, username):\n"
        "        return None\n"
        "    def get_credential(self, service, username):\n"
        "        return None\n"
    )
    assert not list(_violations(REPO / "x.py", ast.parse(clean)))
