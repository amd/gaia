# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dual-publish an agent wheel to R2 (Hub) and PyPI (pip).

Phase 3 of the Agent Hub restructure (``docs/spec/agent-hub-restructure.mdx``,
step 3F — dual distribution):

* **R2** is the canonical source for the Hub UI/website. The wheel + its
  ``gaia-agent.yaml`` are POSTed to the Cloudflare Worker's ``/publish``
  endpoint (``workers/agent-hub/``) with a Bearer token. The Worker validates
  the manifest, enforces publisher scope + version immutability, computes a
  server-side SHA-256, and rebuilds ``index.json``.
* **PyPI** is the canonical source for ``pip install gaia-agent-<id>``. The
  wheel is uploaded with ``twine``; PyPI enforces version immutability natively.

Tokens resolve from the environment first (``GAIA_HUB_TOKEN`` / ``PYPI_TOKEN``)
then the OS keyring (``gaia agent login`` stores them there). Per ``CLAUDE.md``
(No Silent Fallbacks): a missing token, a network failure, or a rejected upload
raises a :class:`PublisherError` naming *what* failed, *what* to do, and *where*
to look — there is no "publish to whichever one happens to work" degradation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

# Keyring service + usernames for stored publisher tokens. Distinct from the
# connectors' "gaia.connections" service so a Hub token can't be confused with
# an OAuth refresh token.
KEYRING_SERVICE = "gaia.hub"
HUB_TOKEN_KEY = "hub-token"
PYPI_TOKEN_KEY = "pypi-token"

# Environment overrides (checked before the keyring) — handy for CI, where
# secrets arrive as env vars, not an interactive keyring.
HUB_TOKEN_ENV = "GAIA_HUB_TOKEN"
PYPI_TOKEN_ENV = "PYPI_TOKEN"

# PyPI token uploads always use the literal "__token__" username.
PYPI_TOKEN_USERNAME = "__token__"

_PUBLISH_TIMEOUT = 120  # seconds for the R2 multipart upload


class PublisherError(Exception):
    """Raised when a publish to R2 or PyPI cannot proceed or is rejected.

    The message always names *what* failed, *what* to do, and *where* to look,
    per the project's fail-loudly rule.
    """


@dataclass
class TargetResult:
    """Outcome of publishing to a single target (R2 or PyPI)."""

    target: str  # "r2" | "pypi"
    skipped: bool = False
    detail: str = ""


@dataclass
class PublishResult:
    """Combined outcome of a dual-publish."""

    agent_id: str
    version: str
    r2: TargetResult
    pypi: TargetResult


# ---------------------------------------------------------------------------
# Token storage (keyring + env)
# ---------------------------------------------------------------------------


def _keyring():
    try:
        import keyring  # local import: keyring is in [ui]/[dev], not core
    except ImportError as exc:
        raise PublisherError(
            "the 'keyring' package is required to store/read publisher tokens. "
            "Install it with 'uv pip install \"amd-gaia[dev]\"', or pass tokens via "
            f"the {HUB_TOKEN_ENV} / {PYPI_TOKEN_ENV} environment variables."
        ) from exc
    return keyring


def store_token(kind: str, token: str) -> None:
    """Persist a publisher token in the OS keyring.

    Args:
        kind: ``"hub"`` (R2 Worker) or ``"pypi"``.
        token: The secret to store.

    Raises:
        PublisherError: For an unknown *kind*, an empty *token*, or a keyring
            backend failure.
    """
    if not token or not token.strip():
        raise PublisherError(
            f"refusing to store an empty {kind} token. Pass a non-empty token."
        )
    username = _username_for(kind)
    try:
        _keyring().set_password(KEYRING_SERVICE, username, token.strip())
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise PublisherError(
            f"could not store the {kind} token in the OS keyring: {exc}. On a "
            f"headless machine, set the {_env_for(kind)} environment variable "
            f"instead."
        ) from exc


def _username_for(kind: str) -> str:
    if kind == "hub":
        return HUB_TOKEN_KEY
    if kind == "pypi":
        return PYPI_TOKEN_KEY
    raise PublisherError(
        f"unknown token kind {kind!r}. Use 'hub' (R2 Worker) or 'pypi'."
    )


def _env_for(kind: str) -> str:
    return HUB_TOKEN_ENV if kind == "hub" else PYPI_TOKEN_ENV


def get_hub_token() -> Optional[str]:
    """Return the R2 Worker token from env or keyring (``None`` if unset)."""
    return _resolve_token("hub")


def get_pypi_token() -> Optional[str]:
    """Return the PyPI API token from env or keyring (``None`` if unset)."""
    return _resolve_token("pypi")


def _resolve_token(kind: str) -> Optional[str]:
    env_val = os.environ.get(_env_for(kind))
    if env_val and env_val.strip():
        return env_val.strip()
    try:
        stored = _keyring().get_password(KEYRING_SERVICE, _username_for(kind))
    except PublisherError:
        # keyring not installed — env was the only option and it's unset.
        return None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        log.warning("publisher: keyring read failed for %s token: %s", kind, exc)
        return None
    return stored.strip() if stored and stored.strip() else None


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


def publish(
    pack_result,
    manifest_path,
    *,
    hub_url: Optional[str] = None,
    skip_r2: bool = False,
    skip_pypi: bool = False,
    twine_runner=None,
) -> PublishResult:
    """Dual-publish a built wheel to R2 and PyPI.

    Args:
        pack_result: A :class:`gaia.hub.packager.PackResult` (the wheel to ship).
        manifest_path: Path to the agent's ``gaia-agent.yaml`` (uploaded to R2
            so the Worker can validate + index the version).
        hub_url: R2 Worker origin. Defaults to the configured hub base URL
            (``GAIA_HUB_URL`` / ``https://hub.amd-gaia.ai``).
        skip_r2: Publish to PyPI only.
        skip_pypi: Publish to R2 only.
        twine_runner: Optional ``(cmd, env) -> (rc, output)`` callable used to
            run twine (injected by tests).

    Returns:
        A :class:`PublishResult` describing each target's outcome.

    Raises:
        PublisherError: For a missing token, a network/HTTP failure, or a
            rejected upload (e.g. a version that already exists).
    """
    if skip_r2 and skip_pypi:
        raise PublisherError(
            "nothing to publish: both --skip-r2 and --skip-pypi were given. "
            "Drop one so the wheel goes somewhere."
        )

    wheel = Path(pack_result.wheel_path)
    if not wheel.exists():
        raise PublisherError(
            f"wheel not found: {wheel}. Run 'gaia agent pack' first (or the "
            f"publish step rebuilds it)."
        )
    manifest = Path(manifest_path)
    if not manifest.exists():
        raise PublisherError(
            f"gaia-agent.yaml not found: {manifest}. R2 needs the manifest to "
            f"validate and index the published version."
        )

    r2 = (
        TargetResult("r2", skipped=True, detail="skipped (--skip-r2)")
        if skip_r2
        else _publish_r2(wheel, manifest, hub_url)
    )
    pypi = (
        TargetResult("pypi", skipped=True, detail="skipped (--skip-pypi)")
        if skip_pypi
        else _publish_pypi(wheel, twine_runner)
    )
    return PublishResult(
        agent_id=pack_result.agent_id,
        version=pack_result.version,
        r2=r2,
        pypi=pypi,
    )


def _hub_base_url(hub_url: Optional[str]) -> str:
    if hub_url:
        return hub_url.rstrip("/")
    # Reuse the catalog's resolution so publish and install agree on the origin.
    from gaia.hub.catalog import get_hub_base_url

    return get_hub_base_url()


def _publish_r2(wheel: Path, manifest: Path, hub_url: Optional[str]) -> TargetResult:
    """POST the wheel + manifest to the Worker's ``/publish`` (Bearer auth)."""
    token = get_hub_token()
    if not token:
        raise PublisherError(
            "no Hub publish token found. Run 'gaia agent login --hub-token "
            f"<token>' to store one, or set {HUB_TOKEN_ENV}. The Worker rejects "
            "anonymous publishes with 401."
        )

    import requests  # local import: requests is a core dep

    url = f"{_hub_base_url(hub_url)}/publish"
    manifest_text = manifest.read_text(encoding="utf-8")
    files = {
        "manifest": ("gaia-agent.yaml", manifest_text, "text/yaml"),
    }
    # Workers that predate these fields ignore unknown multipart parts. README
    # and CHANGELOG are read from beside the manifest and become the catalog
    # entry's `readme` / `changelog` (rendered on the hub agent page).
    for doc, field in (("README.md", "readme"), ("CHANGELOG.md", "changelog")):
        path = manifest.parent / doc
        if path.exists():
            files[field] = (doc, path.read_text(encoding="utf-8"), "text/markdown")
        else:
            # Not fatal (the field defaults to "" in the catalog), but the hub
            # page renders it — log so the omission isn't silent.
            log.info(
                "publisher: no %s next to the manifest; hub page %s will be empty",
                doc,
                field,
            )
    log.debug("publisher: POST %s (%s)", url, wheel.name)
    try:
        with wheel.open("rb") as fh:
            files["artifact"] = (wheel.name, fh, "application/octet-stream")
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                files=files,
                timeout=_PUBLISH_TIMEOUT,
            )
    except requests.RequestException as exc:
        raise PublisherError(
            f"could not reach the Hub publish endpoint at {url}: {exc}. Check "
            f"your network and that GAIA_HUB_URL points at a running Worker."
        ) from exc

    if resp.status_code in (200, 201):
        return TargetResult("r2", detail=f"published to {url}")

    body = _short_body(resp)
    if resp.status_code == 401:
        raise PublisherError(
            f"Hub rejected the publish token (401): {body}. Re-run 'gaia agent "
            f"login --hub-token <token>' with a valid token."
        )
    if resp.status_code == 403:
        raise PublisherError(
            f"Hub rejected the publish: not authorized for this agent's author "
            f"(403): {body}. The token's publisher scope must include the "
            f"manifest 'author'."
        )
    if resp.status_code == 409:
        raise PublisherError(
            f"this version already exists on the Hub (409): {body}. Published "
            f"versions are immutable — bump the version with 'gaia agent version "
            f"<patch|minor|major>' and re-pack."
        )
    raise PublisherError(f"Hub publish failed ({resp.status_code}) at {url}: {body}.")


def _short_body(resp, limit: int = 500) -> str:
    try:
        text = resp.text or ""
    except Exception:  # pylint: disable=broad-exception-caught
        return "<unreadable response body>"
    text = text.strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _publish_pypi(wheel: Path, twine_runner) -> TargetResult:
    """Upload the wheel to PyPI via twine (``__token__`` auth)."""
    token = get_pypi_token()
    if not token:
        raise PublisherError(
            "no PyPI token found. Run 'gaia agent login --pypi-token <token>' "
            f"to store one, or set {PYPI_TOKEN_ENV}. Create a token at "
            "https://pypi.org/manage/account/token/."
        )

    cmd = [
        sys.executable,
        "-m",
        "twine",
        "upload",
        "--non-interactive",
        str(wheel),
    ]
    env = dict(os.environ)
    env["TWINE_USERNAME"] = PYPI_TOKEN_USERNAME
    env["TWINE_PASSWORD"] = token

    run = twine_runner or _default_twine_runner
    log.debug("publisher: twine upload %s", wheel.name)
    rc, output = run(cmd, env)
    if rc == 0:
        return TargetResult("pypi", detail=f"uploaded {wheel.name} to PyPI")

    lowered = (output or "").lower()
    # PyPI rejects a re-upload of an existing file with 400 "File already
    # exists" — surface that as the immutability message, not a generic failure.
    if "already exists" in lowered or "this filename has already been used" in lowered:
        raise PublisherError(
            f"PyPI already has this version of {wheel.name}: published versions "
            f"are immutable. Bump the version with 'gaia agent version "
            f"<patch|minor|major>', re-pack, and publish again.\n{output.strip()}"
        )
    if "403" in lowered or "invalid or non-existent authentication" in lowered:
        raise PublisherError(
            f"PyPI rejected the upload token: {output.strip()}. Re-run 'gaia "
            f"agent login --pypi-token <token>' with a valid API token."
        )
    raise PublisherError(
        f"twine upload failed (exit {rc}) for {wheel.name}:\n{output.strip()}\n"
        f"Ensure twine is installed ('uv pip install \"amd-gaia[publish]\"') and "
        f"the token is valid."
    )


def _default_twine_runner(cmd: List[str], env: dict):
    """Run twine; return ``(returncode, combined stdout+stderr)``."""
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return 1, (
            f"could not run twine: {exc}. Install it with 'pip install "
            f'"amd-gaia[publish]"\'.'
        )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
