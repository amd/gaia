"""Per-agent grants persisted at ~/.gaia/connections/grants.json

This file implements a tiny JSON-backed grants store with file mode 0600.
"""
import json
import os
from typing import Dict, List

ROOT = os.path.expanduser("~/.gaia/connections")
GRANTS_PATH = os.path.join(ROOT, "grants.json")


def _ensure_dir() -> None:
    os.makedirs(ROOT, exist_ok=True)


def _read() -> Dict[str, Dict[str, List[str]]]:
    if not os.path.exists(GRANTS_PATH):
        return {}
    with open(GRANTS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write(data: Dict[str, Dict[str, List[str]]]) -> None:
    _ensure_dir()
    with open(GRANTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(GRANTS_PATH, 0o600)
    except Exception:
        pass


def grant_agent(provider: str, agent_id: str, scopes: List[str]) -> None:
    data = _read()
    prov = data.setdefault(provider, {})
    prov[agent_id] = scopes
    _write(data)


def revoke_agent_grant(provider: str, agent_id: str) -> None:
    data = _read()
    prov = data.get(provider, {})
    if agent_id in prov:
        del prov[agent_id]
        _write(data)


def list_agent_grants(provider: str) -> Dict[str, List[str]]:
    data = _read()
    return data.get(provider, {})


__all__ = ["grant_agent", "revoke_agent_grant", "list_agent_grants"]
