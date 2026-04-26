# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``repo_binding`` — ``amd/gaia`` bot-identity binding (§5.11, §15.6).

Three responsibilities:

1. :class:`RepoBinding` — Pydantic v2 model mirroring the
   ``~/.gaia/coder/repo_binding.toml`` schema from §15.6. Loaded by
   :func:`load_repo_binding`; validation failures raise
   :class:`RepoBindingError` — never silently default.
2. :func:`doctor` — the §15.6 bootstrap gate. Verifies (a) the GitHub App
   installs, (b) the private key in the keyring decrypts, (c) a webhook
   signature round-trips, and (d) the bound repo has a ``coder`` branch.
   Until :func:`doctor` returns green the agent refuses to take any
   action — matches the "hard bootstrap gate" rule.
3. :func:`agents_md_entry` — the canonical §5.11 discoverability block to
   paste into ``AGENTS.md`` / ``.github/copilot-instructions.md``.

The module never performs real provisioning work. The steps in §15.6
("create GitHub App", "upload private key", "install on repo") are
EM-driven and deliberately outside of ``gaia-coder``'s reach. ``doctor``
*checks* the invariants; it does not *enforce* them by creating anything.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from gaia.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RepoBindingError(RuntimeError):
    """Raised when the ``repo_binding.toml`` cannot be loaded or is invalid."""


class DoctorCheckError(RuntimeError):
    """Raised when a single :func:`doctor` check fails catastrophically.

    Most doctor checks fold their failure into the :class:`DoctorResult` so
    the EM sees every problem in one pass. This exception is reserved for
    errors that prevent *any* checking — e.g. ``repo_binding.toml`` is
    absent.
    """


# ---------------------------------------------------------------------------
# Pydantic model (§15.6)
# ---------------------------------------------------------------------------


class RepoBinding(BaseModel):
    """Schema matching the §15.6 ``repo_binding.toml`` layout.

    Every field is required; missing fields raise at load time. This is
    principle #3 (fail-loudly): the bot identity is load-bearing and
    silent defaults would be dangerous.
    """

    repo: str = Field(..., description="Canonical ``owner/name`` slug")
    github_app_id: int = Field(..., description="GitHub App ID (integer)")
    github_installation_id: int = Field(
        ..., description="Installation ID returned on install-on-repo"
    )
    webhook_secret_keyring_slot: str = Field(
        ..., description="OS keyring slot holding the webhook secret"
    )
    private_key_keyring_slot: str = Field(
        ..., description="OS keyring slot holding the App's private key"
    )
    allowed_branches: List[str] = Field(
        default_factory=list,
        description="Branches the agent may push to (glob-friendly)",
    )
    forbidden_paths: List[str] = Field(
        default_factory=list,
        description=(
            "Repo-relative glob patterns the agent must never modify "
            "(release scripts, signing keys, ...)"
        ),
    )

    @field_validator("repo")
    @classmethod
    def _validate_repo(cls, v: str) -> str:
        if "/" not in v or v.count("/") != 1 or not all(v.split("/")):
            raise ValueError(f"repo must be in 'owner/name' form, got {v!r}")
        return v

    @field_validator("github_app_id", "github_installation_id")
    @classmethod
    def _validate_positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"GitHub IDs must be positive integers, got {v}")
        return v


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_repo_binding(path: str | Path) -> RepoBinding:
    """Load and validate ``repo_binding.toml`` from ``path``.

    Raises :class:`RepoBindingError` — never returns a partially-populated
    object. Matches §15.6 "hard bootstrap gate" semantics.
    """
    # Python 3.11+ has tomllib in the stdlib.
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise RepoBindingError("tomllib not available; need Python 3.11+") from e

    p = Path(path)
    if not p.exists():
        raise RepoBindingError(
            f"repo_binding.toml not found at {p} — run `gaia-coder bind` "
            "to create one per §15.6."
        )
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise RepoBindingError(f"malformed TOML in {p}: {e}") from e
    try:
        return RepoBinding(**data)
    except ValidationError as e:
        raise RepoBindingError(
            f"repo_binding.toml at {p} failed validation: {e}"
        ) from e


# ---------------------------------------------------------------------------
# Doctor (§15.6 step 7 — the bootstrap gate)
# ---------------------------------------------------------------------------


CheckStatus = Literal["pass", "fail", "skip"]


@dataclass
class DoctorCheck:
    """One row in :class:`DoctorResult`."""

    name: str
    status: CheckStatus
    detail: str


@dataclass
class DoctorResult:
    """Structured outcome of :func:`doctor`.

    ``green`` is the one boolean the agent's startup code looks at — any
    ``fail`` row anywhere flips it to False and blocks action (§15.6).
    """

    checks: List[DoctorCheck]
    checked_at: str  # ISO-8601 UTC

    @property
    def green(self) -> bool:
        """True iff no check has status ``fail``."""
        return not any(c.status == "fail" for c in self.checks)

    def failed(self) -> List[DoctorCheck]:
        return [c for c in self.checks if c.status == "fail"]

    def to_dict(self) -> dict:
        return {
            "green": self.green,
            "checked_at": self.checked_at,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail}
                for c in self.checks
            ],
        }


def doctor(
    binding: RepoBinding,
    *,
    keyring_getter: Optional[Any] = None,
    gh_runner: Optional[Any] = None,
    sample_payload: bytes = b'{"ping":"doctor"}',
) -> DoctorResult:
    """Run the four §15.6 bootstrap checks and return a structured result.

    Args:
        binding: The validated :class:`RepoBinding` to check against.
        keyring_getter: Callable ``(slot_name) -> bytes`` used to retrieve
            the private key and webhook secret. Defaults to an OS-keyring
            reader; tests pass a stub.
        gh_runner: Callable ``(argv: list[str]) -> str`` that invokes the
            ``gh`` CLI. Defaults to
            :func:`gaia.coder.tools.github._run_gh`. Tests pass a stub.
        sample_payload: Bytes to sign with the webhook secret during the
            signature round-trip check. Default is a tiny fixed payload.

    The function never raises for a failing check — it records the failure
    and keeps going so the EM sees every broken invariant in one pass.
    It *does* raise :class:`DoctorCheckError` if an input is structurally
    broken in a way that prevents *any* checking (e.g. missing keyring
    getter on a system with no keyring backend).
    """
    getter = keyring_getter or _default_keyring_getter
    runner = gh_runner or _default_gh_runner
    checks: List[DoctorCheck] = []

    checks.append(_check_app_install(binding, runner))
    checks.append(_check_private_key_decrypts(binding, getter))
    checks.append(_check_webhook_signature_round_trip(binding, getter, sample_payload))
    checks.append(_check_coder_branch_exists(binding, runner))

    return DoctorResult(
        checks=checks,
        checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Individual checks — each is a pure function for unit-test isolation
# ---------------------------------------------------------------------------


def _check_app_install(binding: RepoBinding, runner: Any) -> DoctorCheck:
    """Verify ``gh api /app`` authenticates as the right App."""
    try:
        raw = runner(["api", "/app", "-H", "Accept: application/vnd.github+json"])
    except Exception as e:  # noqa: BLE001 — doctor aggregates
        return DoctorCheck(
            name="github_app_install",
            status="fail",
            detail=f"gh api /app failed: {e}",
        )
    import json

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        return DoctorCheck(
            name="github_app_install",
            status="fail",
            detail=f"gh api /app returned non-JSON: {e}",
        )
    app_id = payload.get("id")
    if app_id != binding.github_app_id:
        return DoctorCheck(
            name="github_app_install",
            status="fail",
            detail=(
                f"authenticated App ID {app_id!r} does not match binding "
                f"{binding.github_app_id}"
            ),
        )
    return DoctorCheck(
        name="github_app_install",
        status="pass",
        detail=f"authenticated as App ID {app_id}",
    )


def _check_private_key_decrypts(binding: RepoBinding, getter: Any) -> DoctorCheck:
    """Pull the private key from the keyring and verify it looks like a PEM."""
    try:
        material = getter(binding.private_key_keyring_slot)
    except Exception as e:  # noqa: BLE001
        return DoctorCheck(
            name="private_key_decrypts",
            status="fail",
            detail=f"keyring get failed for {binding.private_key_keyring_slot!r}: {e}",
        )
    if not material:
        return DoctorCheck(
            name="private_key_decrypts",
            status="fail",
            detail=(
                f"keyring slot {binding.private_key_keyring_slot!r} "
                "returned an empty value"
            ),
        )
    # Cheap structural check — full RSA decode would need an extra dep.
    text = (
        material.decode("utf-8", errors="replace")
        if isinstance(material, bytes)
        else material
    )
    if "BEGIN" not in text or "PRIVATE KEY" not in text:
        return DoctorCheck(
            name="private_key_decrypts",
            status="fail",
            detail="keyring value does not look like a PEM-encoded private key",
        )
    return DoctorCheck(
        name="private_key_decrypts",
        status="pass",
        detail="private key retrieved and structurally valid",
    )


def _check_webhook_signature_round_trip(
    binding: RepoBinding, getter: Any, payload: bytes
) -> DoctorCheck:
    """Sign a tiny payload with the secret, then verify the signature matches.

    Same algorithm GitHub uses: ``X-Hub-Signature-256`` = HMAC-SHA256 of
    the raw request body, hex-encoded, prefixed with ``sha256=``.
    """
    try:
        secret = getter(binding.webhook_secret_keyring_slot)
    except Exception as e:  # noqa: BLE001
        return DoctorCheck(
            name="webhook_signature_round_trip",
            status="fail",
            detail=(
                f"keyring get failed for "
                f"{binding.webhook_secret_keyring_slot!r}: {e}"
            ),
        )
    if not secret:
        return DoctorCheck(
            name="webhook_signature_round_trip",
            status="fail",
            detail="keyring returned empty webhook secret",
        )
    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    signature = "sha256=" + hmac.new(secret_bytes, payload, hashlib.sha256).hexdigest()
    if not verify_webhook_signature(secret_bytes, payload, signature):
        return DoctorCheck(
            name="webhook_signature_round_trip",
            status="fail",
            detail="self-signed payload failed own verifier — secret round-trip broken",
        )
    # Negative test: verifier must reject a wrong signature. Without this,
    # a verifier that always returned True would still pass the positive
    # check above. Cf. #827 auto-review.
    wrong_signature = "sha256=" + "0" * 64
    if verify_webhook_signature(secret_bytes, payload, wrong_signature):
        return DoctorCheck(
            name="webhook_signature_round_trip",
            status="fail",
            detail="verifier accepted a known-wrong signature — discrimination broken",
        )
    # Discrimination on payload: same secret, different payload must not verify.
    other_sig = (
        "sha256=" + hmac.new(secret_bytes, payload + b"x", hashlib.sha256).hexdigest()
    )
    if verify_webhook_signature(secret_bytes, payload, other_sig):
        return DoctorCheck(
            name="webhook_signature_round_trip",
            status="fail",
            detail="verifier accepted a signature computed over a different payload",
        )
    return DoctorCheck(
        name="webhook_signature_round_trip",
        status="pass",
        detail="HMAC-SHA256 round-trip OK; verifier discriminates on both wrong-sig and wrong-payload",
    )


def _check_coder_branch_exists(binding: RepoBinding, runner: Any) -> DoctorCheck:
    """Verify the bound repo has a ``coder`` branch (§5.7)."""
    try:
        raw = runner(
            [
                "api",
                f"/repos/{binding.repo}/branches/coder",
                "-H",
                "Accept: application/vnd.github+json",
            ]
        )
    except Exception as e:  # noqa: BLE001
        return DoctorCheck(
            name="coder_branch_exists",
            status="fail",
            detail=(
                f"gh api /repos/{binding.repo}/branches/coder failed: {e}. "
                "Create the branch per §5.7 before retrying."
            ),
        )
    import json

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        return DoctorCheck(
            name="coder_branch_exists",
            status="fail",
            detail=f"branch lookup returned non-JSON: {e}",
        )
    if payload.get("name") != "coder":
        return DoctorCheck(
            name="coder_branch_exists",
            status="fail",
            detail=(
                "branch lookup succeeded but payload.name is "
                f"{payload.get('name')!r} (expected 'coder')"
            ),
        )
    return DoctorCheck(
        name="coder_branch_exists",
        status="pass",
        detail=f"{binding.repo}:coder exists at {payload.get('commit', {}).get('sha', '<unknown>')}",
    )


# ---------------------------------------------------------------------------
# Webhook signature verification — used by the daemon (§15.5) and doctor
# ---------------------------------------------------------------------------


def verify_webhook_signature(secret: bytes, body: bytes, header_value: str) -> bool:
    """Constant-time check of a ``X-Hub-Signature-256`` header.

    Returns ``True`` on match. Implementation mirrors GitHub's canonical
    example; kept alongside :func:`doctor` because both sign with the
    same secret.
    """
    if not header_value.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    supplied = header_value.split("=", 1)[1]
    return hmac.compare_digest(expected, supplied)


# ---------------------------------------------------------------------------
# Default callables — real keyring / gh invocation
# ---------------------------------------------------------------------------


def _default_keyring_getter(slot: str) -> bytes:
    """Resolve a keyring slot via the ``keyring`` package if present.

    Falls back to ``GAIA_CODER_KEYRING_<SLOT>`` env var when the ``keyring``
    package is not installed — useful for CI / tests / headless boxes. The
    env var fallback is documented, not a silent degradation, and is the
    only case where a missing dep changes behaviour.
    """
    env_key = "GAIA_CODER_KEYRING_" + slot.replace("/", "_").replace("-", "_").upper()
    if env_key in os.environ:
        return os.environ[env_key].encode("utf-8")
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError as e:
        raise DoctorCheckError(
            "neither the `keyring` package nor a GAIA_CODER_KEYRING_* env "
            f"var provides slot {slot!r}"
        ) from e
    # keyring uses (service, username) — the slot encodes both.
    if "/" not in slot:
        raise DoctorCheckError(
            f"keyring slot {slot!r} must be of the form 'service/username'"
        )
    service, username = slot.split("/", 1)
    value = keyring.get_password(service, username)
    if value is None:
        raise DoctorCheckError(f"keyring has no entry for {slot!r}")
    return value.encode("utf-8")


def _default_gh_runner(argv: List[str]) -> str:
    """Invoke ``gh`` via :func:`gaia.coder.tools.github._run_gh`."""
    # Late import to avoid a hard dep during ``load_repo_binding``.
    from gaia.coder.tools.github import _run_gh

    return _run_gh(argv, timeout_s=60)


# ---------------------------------------------------------------------------
# AGENTS.md discoverability (§5.11)
# ---------------------------------------------------------------------------


AGENTS_MD_ENTRY_TEMPLATE = """\
## `gaia-coder` — autonomous coding agent for `{repo}`

`gaia-coder` is a long-lived agent bound to this repository. She opens
pull requests, triages issues, and self-fixes regressions. All of her
work lands on the `coder` integration branch, never `main` — the EM
reviews and merges from `coder` to `main` at their own cadence.

**How to interact:**

- Mention `@gaia-coder[bot]` in any issue or PR comment. She will read,
  decide whether the ask is actionable, and either reply or open a draft
  PR against `coder`.
- Issues labelled `auto-triage` get classified and suggested labels.
- PRs labelled `auto-review` run her multi-pass review flow.

**What she cannot do:**

- Merge to `main` (branch protection enforces human review).
- Touch forbidden paths listed in `repo_binding.toml`:
{forbidden_paths_list}
- Take any action until `gaia-coder doctor` returns green.

**See:** `docs/plans/coder-agent.mdx` for the full spec.
"""


def agents_md_entry(binding: RepoBinding) -> str:
    """Render the canonical §5.11 AGENTS.md block for ``binding``.

    Pure-template; idempotent; safe to write into ``AGENTS.md`` /
    ``.github/copilot-instructions.md`` as many times as needed (the
    writer is expected to deduplicate by header).
    """
    if binding.forbidden_paths:
        rendered = "\n".join(f"  - `{p}`" for p in binding.forbidden_paths)
    else:
        rendered = "  - (none configured)"
    return AGENTS_MD_ENTRY_TEMPLATE.format(
        repo=binding.repo, forbidden_paths_list=rendered
    )


__all__ = [
    "AGENTS_MD_ENTRY_TEMPLATE",
    "DoctorCheck",
    "DoctorCheckError",
    "DoctorResult",
    "RepoBinding",
    "RepoBindingError",
    "agents_md_entry",
    "doctor",
    "load_repo_binding",
    "verify_webhook_signature",
]
