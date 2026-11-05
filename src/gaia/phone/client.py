# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Raise or clear a phone alert from another GAIA process, through the daemon."""

from __future__ import annotations

from typing import Optional

import requests

from gaia.logger import get_logger

log = get_logger(__name__)

TIMEOUT = 5


def _post(path: str, body: dict) -> Optional[dict]:
    from gaia.daemon.client import attach

    inst = attach(timeout=1.5, patient=False)
    if inst is None:
        log.warning("phone: no GAIA daemon is running, so %s could not reach it", path)
        return None
    url = f"http://{inst.host}:{inst.port}/daemon/v1/phone{path}"
    response = requests.post(
        url,
        json=body,
        headers={"Authorization": f"Bearer {inst.token}"},
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        log.warning(
            "phone: %s refused with %s: %s",
            path,
            response.status_code,
            response.text[:300],
        )
        return None
    return response.json()


def get_settings() -> Optional[dict]:
    """The daemon's phone settings, or None when no daemon answers."""
    from gaia.daemon.client import attach

    try:
        inst = attach(timeout=1.5, patient=False)
        if inst is None:
            return None
        response = requests.get(
            f"http://{inst.host}:{inst.port}/daemon/v1/phone/settings",
            headers={"Authorization": f"Bearer {inst.token}"},
            timeout=TIMEOUT,
        )
    except (requests.RequestException, OSError, RuntimeError) as e:
        log.warning("phone: could not read settings: %s", e)
        return None
    if response.status_code != 200:
        log.warning("phone: settings refused with %s", response.status_code)
        return None
    return response.json()


def raise_alert(
    source: str, *, reason: str = "", rule_key: Optional[str] = None
) -> None:
    """Tell the daemon something needs the user. Never raises: the caller is
    already handling a failure of its own, and this only adds a way to hear
    about it."""
    try:
        _post(
            "/alerts", {"source": source, "reason": reason[:500], "rule_key": rule_key}
        )
    except (requests.RequestException, OSError, RuntimeError) as e:
        log.warning("phone: could not raise a %s alert: %s", source, e)


def clear_rule(rule_key: str) -> None:
    try:
        _post("/alerts/clear", {"rule_key": rule_key})
    except (requests.RequestException, OSError, RuntimeError) as e:
        log.warning("phone: could not clear alert %s: %s", rule_key, e)
