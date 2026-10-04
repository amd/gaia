# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""HTTP plumbing shared by GAIA's Lemonade speech clients (ASR and TTS).

Both endpoints share the server, the auth header, the error envelope and the
"pull the model before a live caller needs it" step, so they share this code
rather than each growing their own copy of it.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import requests

from gaia.llm.lemonade_client import (
    lemonade_auth_headers,
    resolve_lemonade_api_key,
    resolve_lemonade_base_url,
)
from gaia.logger import get_logger

log = get_logger(__name__)

DOCS_URL = "https://amd-gaia.ai/docs/guides/install"

# A first pull downloads the weights; Whisper-Large-v3 is ~3 GB.
MODEL_PULL_TIMEOUT = 3600


class LemonadeAudioService:
    """Base for a client of one Lemonade audio endpoint.

    Subclasses set ``error_cls`` to the exception raised for a server-side
    failure; reachability failures are always ``ConnectionError``.
    """

    error_cls: type = RuntimeError

    def __init__(
        self,
        base_url: Optional[str],
        model: str,
        api_key: Optional[str],
        timeout: int,
    ):
        self.base_url = resolve_lemonade_base_url(base_url)
        self.model = model
        self.api_key = resolve_lemonade_api_key(api_key, base_url=self.base_url)
        self.timeout = timeout
        self._session = requests.Session()

    def catalog(self) -> List[Dict[str, Any]]:
        """Every model this server knows, downloaded or not."""
        url = f"{self.base_url}/models?show_all=true"
        payload = self._get_json(url, what="model catalog")
        models = payload.get("data")
        if not isinstance(models, list):
            raise self.error_cls(
                f"Unexpected response from {url}: expected a 'data' list, got "
                f"{type(models).__name__}. Check the Lemonade Server version."
            )
        return [entry for entry in models if isinstance(entry, dict)]

    def ensure_model_pulled(
        self, label: str, say: Optional[Callable[[str], None]] = None
    ) -> None:
        """Pull ``self.model`` if the server has it in its catalog but not on disk.

        Args:
            label: The Lemonade label the model must carry, e.g.
                ``"transcription"`` or ``"tts"``.
            say: Called once with a human-readable line before a download.

        Raises:
            ConnectionError: Lemonade Server is not reachable.
            error_cls: The model is unknown, serves another purpose, or the
                pull failed.
        """
        catalog = self.catalog()
        entry = next((m for m in catalog if m.get("id") == self.model), None)
        if entry is None:
            known = sorted(
                str(m.get("id")) for m in catalog if label in (m.get("labels") or [])
            )
            raise self.error_cls(
                f"Lemonade at {self.base_url} has no model '{self.model}'. "
                f"Its {label} models are: {', '.join(known) or 'none'}. Pick one "
                "of those, or update Lemonade Server if the list is empty. "
                f"See {DOCS_URL}"
            )
        if label not in (entry.get("labels") or []):
            raise self.error_cls(
                f"Lemonade model '{self.model}' is not a {label} model (labels: "
                f"{', '.join(entry.get('labels') or []) or 'none'})."
            )
        if entry.get("downloaded"):
            return
        size = entry.get("size")
        message = f"Downloading {self.model} through Lemonade" + (
            f" (~{float(size):.2f} GB)" if isinstance(size, (int, float)) else ""
        )
        log.info(message)
        if say:
            say(message)
        # Built-in models are pulled by name only; a recipe 400s on them (#1655).
        self._post_json(
            f"{self.base_url}/pull",
            {"model_name": self.model},
            what=f"pull of {self.model}",
            timeout=MODEL_PULL_TIMEOUT,
        )

    def _headers(self) -> Dict[str, str]:
        return lemonade_auth_headers(self.api_key)

    def _get_json(self, url: str, what: str) -> Dict[str, Any]:
        try:
            response = self._session.get(
                url, headers=self._headers(), timeout=self.timeout
            )
        except requests.ConnectionError as e:
            raise self._unreachable(e) from e
        return self._decode(response, url, what)

    def _post_json(
        self, url: str, body: Dict[str, Any], what: str, timeout: int
    ) -> Dict[str, Any]:
        try:
            response = self._session.post(
                url, json=body, headers=self._headers(), timeout=timeout
            )
        except requests.ConnectionError as e:
            raise self._unreachable(e) from e
        return self._decode(response, url, what)

    def _unreachable(self, error: Exception) -> ConnectionError:
        from gaia.llm.lemonade_launcher import describe_start_hint

        # The hint owns start/install advice; repeating it here doubled it.
        return ConnectionError(
            f"Lemonade Server is not reachable at {self.base_url} ({error}). "
            f"{describe_start_hint().instruction} If it runs elsewhere, set "
            f"LEMONADE_BASE_URL to that server. See {DOCS_URL}"
        )

    def _check_status(self, response, url: str, what: str) -> None:
        # 401 is handled before the generic branch so the body — which some
        # proxies echo the Authorization header into — never reaches the user.
        if response.status_code == 401:
            raise self.error_cls(
                f"Lemonade rejected the API key (401 Unauthorized) on {what}. "
                "Verify LEMONADE_API_KEY is correct."
            )
        if response.status_code >= 400:
            raise self.error_cls(
                f"Lemonade returned {response.status_code} for {what} at {url}: "
                f"{server_message(response)}"
            )

    def _decode(self, response, url: str, what: str) -> Dict[str, Any]:
        self._check_status(response, url, what)
        try:
            payload = response.json()
        except ValueError as e:
            raise self.error_cls(
                f"Lemonade returned a non-JSON body for {what} at {url}: "
                f"{response.text[:300]!r}"
            ) from e
        if not isinstance(payload, dict):
            raise self.error_cls(
                f"Lemonade returned {type(payload).__name__} for {what} at "
                f"{url}, expected a JSON object."
            )
        return payload


def server_message(response) -> str:
    """Pull Lemonade's ``{"error": {"message": ...}}`` text out of a failure body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:500]
    return str(body)[:500]
