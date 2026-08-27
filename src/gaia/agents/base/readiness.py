# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Inherited agent init: readiness probing and provisioning, declared not coded.

An agent states what it needs — a model id, a minimum backend version — and gets
``GET``/``POST /v1/<id>/init`` from :class:`~gaia.agents.base.server.AgentServer`
for free. Before this module every agent that wanted a readiness contract had to
hand-write both verbs; the email sidecar did, and was the only one that had them.

Two verbs, deliberately asymmetric:

* **GET** — read-only. Probes the backend and reports a structured status with an
  actionable ``hint`` per failure. Never pulls, never installs, never mutates.
* **POST** — provisioning. Tells an *already-running* backend to download the
  declared model, streaming newline-terminated progress so a consumer can render
  it terminal-style.

**Where the boundary sits.** Inherited init makes *this agent's* requirements
ready: its model, against a backend that is already up. Installing the backend
itself stays with ``gaia init`` — an agent process cannot bootstrap the server it
depends on, and pretending otherwise would turn a clear error into a hang. When
the backend is unreachable both verbs fail loudly and name whose job the fix is;
:func:`provision_progress` is never even reached. Do not grow an installer here.

**Why the final line decides a pull, not the status code.** Once a streamed 200
is committed the status can no longer change, so a pull that fails half-way still
arrives as ``200 OK``. The last line carries the verdict: ``✓`` success, ``✗``
failure, ``⚠`` succeeded-but-unverified. Consumers must read it rather than the
status. The TUI's preflight gate already does (``tui/internal/ui/preflight``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, List, Optional, Tuple

from gaia.logger import get_logger

logger = get_logger(__name__)

# Fast pre-flight timeouts for the "is the backend even up?" probe. The real
# chat path uses a long scalar timeout — correct for generation, but it also
# governs the TCP connect, so an unreachable server would block on the OS SYN
# timeout before erroring. A short connect leg turns "server down" into a prompt
# answer instead of a 30s hang.
PROBE_CONNECT_TIMEOUT = 2.0
PROBE_READ_TIMEOUT = 5.0

# A model pull is a first-download of multi-GB weights — minutes, not seconds.
# Generous read ceiling so a slow link does not abort a real download; the
# connect leg stays short so an unreachable server still fails fast.
PULL_TIMEOUT = (5.0, 1800.0)


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentRequirements:
    """What an agent needs before it can serve — the whole declaration.

    Args:
        model_id: The backend model id the agent loads. ``None`` means the agent
            has no model requirement, and the model half of the check is skipped
            rather than reported as missing.
        min_backend_version: Minimum Lemonade Server version. ``None`` means the
            agent did not declare one, which is reported as an *indeterminate*
            version check (``compatible: null``) — never as a pass.
        base_url: Backend base URL to probe. ``None`` resolves from the
            environment (``LEMONADE_BASE_URL``), which is what almost every
            agent wants.
    """

    model_id: Optional[str] = None
    min_backend_version: Optional[str] = None
    base_url: Optional[str] = None

    def declares_anything(self) -> bool:
        """Whether this states a requirement worth serving an ``/init`` for.

        A declaration of nothing must not produce a readiness endpoint. It would
        answer ``ready: true`` the moment the backend is up, having verified
        nothing about the agent — and a consumer would believe it. ``base_url``
        alone does not count: it says where to look, not what is needed.
        """
        return bool(self.model_id or self.min_backend_version)

    @classmethod
    def from_manifest(cls, manifest: Any) -> "AgentRequirements":
        """Derive requirements from a parsed ``gaia-agent.yaml``.

        Reads the first entry of the top-level ``models:`` list and
        ``requirements.min_lemonade_version``.

        NOTE: ``gaia.hub.manifest.Requirements`` does not currently parse
        ``min_lemonade_version`` — the email manifest declares it and the field
        is dropped on the floor. Until the parser carries it, this returns
        ``min_backend_version=None``, which surfaces as ``compatible: null``
        (indeterminate) rather than a fabricated pass. Agents needing the gate
        enforced today should construct :class:`AgentRequirements` explicitly.
        """
        models = getattr(manifest, "models", None) or []
        requirements = getattr(manifest, "requirements", None)
        return cls(
            model_id=models[0] if models else None,
            min_backend_version=getattr(requirements, "min_lemonade_version", None),
        )


# ---------------------------------------------------------------------------
# Response models — the wire contract
# ---------------------------------------------------------------------------
#
# Shape is byte-compatible with the email sidecar's existing /v1/email/init so
# the TUI preflight gate parses both without a branch. The `lemonade` key is
# named for the backend rather than generically because that is what shipped and
# what every consumer already reads; renaming it would break them for cosmetics.


def _build_models():
    """Build the pydantic response models lazily.

    Module import must stay cheap: ``gaia.agents.base.server`` imports this for
    the dataclass alone in pipe/CLI/MCP modes, which do not pay for pydantic.
    """
    from pydantic import BaseModel, Field

    class BackendStatus(BaseModel):
        """Reachability AND version-compatibility of the local model server."""

        reachable: bool = Field(
            ..., description="True when the backend answered the /health probe."
        )
        base_url: str = Field(..., description="The /api/v1 base URL that was probed.")
        version: Optional[str] = Field(
            default=None,
            description=(
                "The backend's self-reported version (from /health). Null when "
                "it does not advertise one."
            ),
        )
        min_version: str = Field(
            default="",
            description=(
                "Minimum backend version this agent declared. Empty when the "
                "agent declared none, in which case `compatible` is null."
            ),
        )
        compatible: Optional[bool] = Field(
            default=None,
            description=(
                "True when version >= min_version. Null when it could not be "
                "determined — an indeterminate check, NOT a pass."
            ),
        )

    class ModelStatus(BaseModel):
        """Presence (and, when cheap, loadability) of the declared model."""

        id: str = Field(..., description="The model id the agent declared.")
        present: bool = Field(
            ..., description="True when the model is downloaded on the backend."
        )
        loadable: Optional[bool] = Field(
            default=None,
            description=(
                "Whether the model actually loads. Not probed — forcing a load "
                "is heavy — so this is null; `present` is the readiness signal."
            ),
        )
        ctx_size: Optional[int] = Field(
            default=None,
            description=(
                "The context window the model is CURRENTLY loaded at, as "
                "reported by the backend. Null when it is not loaded right now "
                "or the backend does not report one — never a config echo."
            ),
        )

    class InitResponse(BaseModel):
        """Readiness preflight for one agent's requirements.

        ``ready`` is True only when the backend is reachable, at a compatible
        version, and the declared model is present. Served as HTTP 200 when
        ready and 503 when not, with the SAME body either way, so a consumer can
        parse one shape and read ``hint`` for the next step.
        """

        ready: bool = Field(
            ..., description="True when every declared requirement is satisfied."
        )
        lemonade: BackendStatus = Field(..., description="Backend server status.")
        model: ModelStatus = Field(..., description="Declared model status.")
        hint: Optional[str] = Field(
            default=None,
            description=(
                "Actionable next step when not ready (what failed / what to "
                "do). Null when ready."
            ),
        )

    return BackendStatus, ModelStatus, InitResponse


_MODELS: Optional[Tuple[Any, Any, Any]] = None


def init_models() -> Tuple[Any, Any, Any]:
    """Return ``(BackendStatus, ModelStatus, InitResponse)``, building once."""
    global _MODELS
    if _MODELS is None:
        _MODELS = _build_models()
    return _MODELS


# ---------------------------------------------------------------------------
# Probes — every one read-only
# ---------------------------------------------------------------------------


def resolve_probe_base(base_url: Optional[str]) -> str:
    """Resolve the backend's ``/api/v1`` base URL for a probe.

    An explicit ``base_url`` is normalised to end in ``/api/v1`` (callers often
    omit it); ``None`` falls back to the env-derived default, so every probe in
    a process targets the same server.
    """
    from gaia.llm.lemonade_client import _get_lemonade_config

    if base_url:
        probe_base = base_url.rstrip("/")
        if not probe_base.endswith("/api/v1"):
            probe_base = f"{probe_base}/api/v1"
        return probe_base
    _, _, probe_base = _get_lemonade_config()
    return probe_base


def probe_backend_health(
    base_url: Optional[str] = None,
) -> Tuple[bool, str, Optional[str], List[dict]]:
    """Probe the backend's ``/health``.

    Returns ``(reachable, probe_base, version, loaded_models)``. Any HTTP
    response — even an error status — means the server is up; only a
    connection/timeout failure counts as unreachable, because auth and model
    errors surface later on the real call where their messages are specific.

    Never raises: readiness reports values rather than failing.
    """
    import requests

    probe_base = resolve_probe_base(base_url)
    try:
        resp = requests.get(
            f"{probe_base}/health",
            timeout=(PROBE_CONNECT_TIMEOUT, PROBE_READ_TIMEOUT),
        )
    except requests.exceptions.RequestException:
        return False, probe_base, None, []

    version: Optional[str] = None
    loaded_models: List[dict] = []
    try:
        body = resp.json()
        if isinstance(body, dict):
            version = body.get("version")
            raw_loaded = body.get("all_models_loaded", [])
            if isinstance(raw_loaded, list):
                loaded_models = [m for m in raw_loaded if isinstance(m, dict)]
    except ValueError:
        version = None
    return True, probe_base, version, loaded_models


def probe_model_present(probe_base: str, model_id: str) -> bool:
    """Check whether ``model_id`` is downloaded on the backend.

    Matches tolerantly via the core comparison — a ``user.``-prefixed
    registration is listed under the stripped id, so a strict compare would
    report a downloaded model as missing and send the user to re-pull it.

    Raises ``requests.RequestException`` when the model list cannot be read. The
    caller MUST distinguish that from "absent": they mean different things and
    have different remedies.
    """
    import requests

    from gaia.llm.lemonade_client import (
        _model_ids_match,
        lemonade_auth_headers,
        record_cloud_models,
        resolve_lemonade_api_key,
    )

    resp = requests.get(
        f"{probe_base}/models",
        headers=lemonade_auth_headers(resolve_lemonade_api_key()),
        timeout=(PROBE_CONNECT_TIMEOUT, PROBE_READ_TIMEOUT),
    )
    resp.raise_for_status()
    body = resp.json()
    entries = body.get("data", []) if isinstance(body, dict) else []
    # Keep cloud classification current — this response is the authority, and
    # the readiness gate may be the first thing to read it in this process.
    record_cloud_models(body if isinstance(body, dict) else None)
    for entry in entries:
        if isinstance(entry, dict) and _model_ids_match(entry.get("id"), model_id):
            # A gateway model is "present" the moment it is discovered; there
            # is nothing to download. Reporting it absent sends the user to
            # `gaia init` for a model that lives on the gateway.
            return True
    return False


def extract_loaded_ctx(loaded_models: List[dict], model_id: str) -> Optional[int]:
    """The ctx_size the backend reports ``model_id`` loaded at, or None.

    Reports only what the server says: a missing entry, missing
    ``recipe_options``, or a non-positive value all yield None — never a config
    echo or a guess.
    """
    from gaia.llm.lemonade_client import _model_ids_match

    for entry in loaded_models:
        if _model_ids_match(entry.get("model_name"), model_id) or _model_ids_match(
            entry.get("checkpoint"), model_id
        ):
            recipe_options = entry.get("recipe_options")
            if not isinstance(recipe_options, dict):
                return None
            ctx = recipe_options.get("ctx_size")
            if isinstance(ctx, int) and not isinstance(ctx, bool) and ctx > 0:
                return ctx
            return None
    return None


def parse_version(version: Optional[str]) -> Optional[Tuple[int, ...]]:
    """Parse a dotted version into a comparable int tuple, or None."""
    if not version:
        return None
    try:
        return tuple(int(p) for p in version.lstrip("v").split(".")[:3])
    except (ValueError, IndexError, AttributeError):
        return None


def version_meets_min(found: Optional[str], minimum: Optional[str]) -> Optional[bool]:
    """Whether ``found`` >= ``minimum``, or None when it cannot be told.

    None means indeterminate — the server advertised nothing, the string was
    unparseable, or the agent declared no minimum. Readiness surfaces that as
    ``compatible: null`` so a consumer sees the check did not run, rather than
    a fabricated pass.
    """
    found_t = parse_version(found)
    min_t = parse_version(minimum)
    if found_t is None or min_t is None:
        return None
    return found_t >= min_t


def pull_model(probe_base: str, model_id: str) -> None:
    """Tell a RUNNING backend to download ``model_id``.

    Posts ONLY ``model_name`` — sending ``recipe`` for a built-in Lemonade model
    makes it 400 (#1655), and that failure only reproduces on a cold cache, so
    it survives every warm-machine test. Raises ``requests.RequestException`` on
    failure; the caller surfaces it as a loud ``✗`` line.

    This is the ONLY provisioning an agent process can do. It cannot install the
    backend — see the module docstring.
    """
    import requests

    from gaia.llm.lemonade_client import (
        is_cloud_model,
        lemonade_auth_headers,
        resolve_lemonade_api_key,
    )

    if is_cloud_model(model_id):
        # A gateway model has no weights to fetch. Lemonade 400s on a pull for
        # one, and the message talks about a download the user cannot perform.
        raise RuntimeError(
            f"'{model_id}' is hosted on a gateway, so there is nothing to "
            f"download. If it is unavailable, check the gateway connection "
            f"with `gaia gateway status`."
        )

    resp = requests.post(
        f"{probe_base}/pull",
        json={"model_name": model_id},
        headers=lemonade_auth_headers(resolve_lemonade_api_key()),
        timeout=PULL_TIMEOUT,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# GET — readiness
# ---------------------------------------------------------------------------


def model_list_unreadable(hint: Optional[str]) -> bool:
    """Whether a hint reports the one failure the structured fields cannot.

    The backend answered /health but its model list read failed, so
    ``present:false`` means "could not tell", not "missing". Only the hint
    carries that distinction.
    """
    h = (hint or "").lower()
    return "model list" in h and "could not be read" in h


def compute_init_status(
    requirements: AgentRequirements, agent_name: str = "This agent"
):
    """Probe the declared requirements and return a structured status.

    Read-only — never pulls, never installs. Returns a not-ready response with
    an actionable ``hint`` per failure rather than raising, so one route can
    serialize the same body under both 200 and 503.

    Failure order is dependency order and it matters: a version too old is
    reported before a missing model, because upgrading the backend comes first
    even when both are wrong. Reporting them the other way sends the user to
    download gigabytes that the old server may then refuse to serve.
    """
    import requests

    BackendStatus, ModelStatus, InitResponse = init_models()

    model_id = requirements.model_id
    reachable, probe_base, version, loaded_models = probe_backend_health(
        requirements.base_url
    )
    compatible = (
        version_meets_min(version, requirements.min_backend_version)
        if reachable
        else None
    )
    backend = BackendStatus(
        reachable=reachable,
        base_url=probe_base,
        version=version,
        min_version=requirements.min_backend_version or "",
        compatible=compatible,
    )

    def _model(present: bool, ctx_size: Optional[int] = None):
        # A model-less agent reports present=True so a consumer's "is the model
        # downloaded?" check does not raise a failure over a model that was
        # never required; the id says plainly that there is none.
        return ModelStatus(
            id=model_id or "(none required)",
            present=present,
            loadable=None,
            ctx_size=ctx_size,
        )

    if not reachable:
        return InitResponse(
            ready=False,
            lemonade=backend,
            model=_model(False),
            hint=(
                f"The local Lemonade Server is not reachable at {probe_base} — "
                "start it with `lemonade-server serve` (or run `gaia init`), "
                f"then retry. {agent_name} cannot install it: that is a host "
                "prerequisite, not something an agent can bootstrap."
            ),
        )

    # An agent with no declared model has nothing further to check — a reachable
    # backend at a compatible version is the whole of its readiness.
    if not model_id:
        if compatible is False:
            return InitResponse(
                ready=False,
                lemonade=backend,
                model=_model(False),
                hint=_version_hint(version, requirements, agent_name),
            )
        return InitResponse(ready=True, lemonade=backend, model=_model(True), hint=None)

    loaded_ctx = extract_loaded_ctx(loaded_models, model_id)

    try:
        present = probe_model_present(probe_base, model_id)
    except requests.exceptions.RequestException as exc:
        # Reachable, but the model list could not be read: `present:false` here
        # means "could not tell", not "missing". Reporting it as missing would
        # send the user to re-download something they may already have.
        return InitResponse(
            ready=False,
            lemonade=backend,
            model=_model(False, loaded_ctx),
            hint=(
                f"The backend is reachable but its model list at {probe_base}/models "
                f"could not be read ({type(exc).__name__}: {exc}). Make sure the "
                "server is healthy, then retry."
            ),
        )

    model = _model(present, loaded_ctx)

    if compatible is False:
        return InitResponse(
            ready=False,
            lemonade=backend,
            model=model,
            hint=_version_hint(version, requirements, agent_name),
        )

    if not present:
        return InitResponse(
            ready=False,
            lemonade=backend,
            model=model,
            hint=(
                f"Model `{model_id}` is not downloaded — run `gaia init`, or POST "
                "to this same path to pull it here, then retry."
            ),
        )

    return InitResponse(ready=True, lemonade=backend, model=model, hint=None)


def _version_hint(
    version: Optional[str], requirements: AgentRequirements, agent_name: str
) -> str:
    return (
        f"Lemonade {version} is older than the required "
        f"{requirements.min_backend_version} that {agent_name} declares — "
        "upgrade it (see https://lemonade-server.ai or run `gaia init`), then "
        "retry."
    )


# ---------------------------------------------------------------------------
# POST — provisioning
# ---------------------------------------------------------------------------


def provision_progress(
    probe_base: str, model_id: str, agent_name: str = "This agent"
) -> Iterator[str]:
    """Yield newline-terminated progress while provisioning the declared model.

    The only provisioning an agent process can do: ask an already-running
    backend to download the model it declared, narrating each step. The caller
    checks reachability first so it can return a truthful 503 — this generator
    runs only once the backend is confirmed up.

    The FINAL line is authoritative (``✓``/``✗``/``⚠``); see the module
    docstring for why the status code is not.
    """
    import requests

    def line(text: str) -> str:
        return text + "\n"

    yield line(f"→ {agent_name} requires model: {model_id}")
    yield line(f"→ Backend reachable at {probe_base}")
    yield line(f"→ Checking whether {model_id} is already downloaded…")

    try:
        present = probe_model_present(probe_base, model_id)
    except requests.exceptions.RequestException as exc:
        yield line(
            f"✗ Could not read the backend's model list ({type(exc).__name__}: {exc})."
        )
        yield line(
            "✗ Provisioning aborted — make sure the Lemonade Server is healthy, "
            "then retry."
        )
        return

    if present:
        yield line(f"✓ {model_id} is already downloaded — nothing to pull.")
        yield line("✓ Provisioning complete. Re-run GET on this path to confirm.")
        return

    yield line(f"→ Pulling {model_id} — a first download can take several minutes…")
    try:
        pull_model(probe_base, model_id)
    except requests.exceptions.RequestException as exc:
        yield line(f"✗ Provisioning failed: {type(exc).__name__}: {exc}")
        yield line(
            "✗ The model was not downloaded. Check the Lemonade Server logs, "
            "then retry."
        )
        return

    yield line(f"✓ {model_id} downloaded.")

    # Verify the pull actually registered. A verify hiccup is surfaced, not
    # swallowed, but does not by itself fail a pull the backend reported OK.
    try:
        if probe_model_present(probe_base, model_id):
            yield line(f"✓ Verified {model_id} is registered with the backend.")
        else:
            yield line(
                f"✗ {model_id} is still not listed after the pull — provisioning "
                "incomplete. Check the Lemonade Server logs, then retry."
            )
            return
    except requests.exceptions.RequestException as exc:
        yield line(
            f"⚠ The pull reported success but the model list could not be re-read "
            f"({type(exc).__name__}). Re-run GET on this path to confirm."
        )

    yield line("✓ Provisioning complete. Re-run GET on this path to confirm.")


def unreachable_progress(
    probe_base: str, agent_name: str = "This agent"
) -> Iterator[str]:
    """The lines a provisioning request gets when the backend is down.

    Yielded under a real 503 — sent BEFORE any streaming commits a 200, so the
    status code is truthful here and the consumer does not have to parse lines
    to learn nothing happened.
    """
    yield f"✗ The local Lemonade Server is not reachable at {probe_base}.\n"
    yield (
        "✗ Start it with `lemonade-server serve` (or run `gaia init`), then "
        "POST to this path again.\n"
    )
    yield (
        f"✗ {agent_name} can't install the backend itself — that's a host "
        "prerequisite.\n"
    )
