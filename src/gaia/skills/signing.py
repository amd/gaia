# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Ed25519 bundle signing and the trust store that turns a signature into a tier.

A published skill bundle carries a detached signature *inside* the bundle, at
``SIGNATURE.json``. Signing covers a canonical digest manifest — every file in
the bundle, its SHA-256, plus the skill's name and version — so neither a
tampered ``SKILL.md`` nor an added, removed, or swapped file survives
verification. The signature travels with the artifact, so the same check works
whether the bundle came from the hub, a mirror, or a USB stick.

**Why inside the bundle and not a separate R2 object?** The hub's publish
contract (``POST /publish/skill``, PR #2668) takes ``skill`` / ``artifact`` /
``changelog`` / ``audit`` parts and nothing else. Embedding keeps the marketplace
lane's contract untouched while still giving install-time provenance.

**What a signature earns.** :func:`attested_tier` maps a verified signature onto
the highest tier it may claim:

* signed by a key the trust store marks ``role: "amd"`` → ``verified``
* signed by any other trusted key → ``community``
* unsigned, unverifiable, or signed by an unknown key → ``experimental``

There is deliberately **no bundled AMD root key**. The trust store is empty until
an operator adds one, so out of the box no skill can reach ``verified`` — an
honest floor rather than a placeholder key that would make the top tier look
enforced when nothing anchors it. :mod:`gaia.skills.tiers` then collapses a
skill's *claimed* tier down to what its signature earned.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from gaia.logger import get_logger
from gaia.skills.errors import SkillError, SkillValidationError
from gaia.skills.tiers import LOWEST_TIER

log = get_logger(__name__)

#: Detached signature file, stored at the root of a skill bundle.
SIGNATURE_FILENAME = "SIGNATURE.json"

#: Trust store filename, in the skills root beside the installed skills.
TRUST_STORE_FILENAME = "trusted-keys.json"

#: Directory (under the skills root) holding this machine's publisher keypairs.
KEYS_DIRNAME = "keys"

#: Signature-format version. Bumped only for a breaking payload change.
SIGNATURE_VERSION = 1

_ALGORITHM = "ed25519"

#: Trust-store roles, and the tier each one attests to.
ROLE_AMD = "amd"
ROLE_PUBLISHER = "publisher"
_ROLE_TIERS = {ROLE_AMD: "verified", ROLE_PUBLISHER: "community"}

_DOCS = "https://amd-gaia.ai/docs/plans/skill-format#security-tiers"


class SkillSignatureError(SkillError):
    """A bundle signature is malformed, does not verify, or cannot be produced.

    Never raised to mean "unsigned" — an absent signature is a valid state that
    caps the skill at ``experimental``. This is for a signature that is *present
    and wrong*, which is a tampering signal, not a trust level.
    """


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


def _ed25519():
    """Return the Ed25519 primitives, or fail loudly if unavailable.

    Signature verification is a security boundary: if the crypto backend is
    missing we refuse rather than skip the check.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise SkillSignatureError(
            "Skill signature verification needs the 'cryptography' package, which "
            "is not installed, so a signed skill cannot be verified. Install it "
            "with: uv pip install 'cryptography>=42.0.0' (or reinstall GAIA: "
            "uv pip install -e '.'). GAIA refuses to install a signed skill "
            f"without checking its signature. See {_DOCS}"
        ) from exc
    return ed25519


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(value: Any, *, what: str) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise SkillSignatureError(
            f"{what} is missing or not a string. Re-sign the bundle with "
            "'gaia skill publish' (or delete the signature to publish unsigned, "
            f"which caps the skill at '{LOWEST_TIER}'). See {_DOCS}"
        )
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise SkillSignatureError(
            f"{what} is not valid base64: {exc}. The bundle's signature is "
            f"corrupt — re-download it or re-sign the source. See {_DOCS}"
        ) from exc


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def key_id(public_key_bytes: bytes) -> str:
    """Stable short id for a public key: the first 16 bytes of its SHA-256, hex."""
    return hashlib.sha256(public_key_bytes).hexdigest()[:32]


@dataclass(frozen=True)
class SigningKey:
    """An Ed25519 keypair on this machine, used to sign skills for publish."""

    private_bytes: bytes = field(repr=False)
    public_bytes: bytes

    @property
    def key_id(self) -> str:
        return key_id(self.public_bytes)


def keys_dir(skills_root: Path) -> Path:
    """Directory holding this machine's publisher keypairs."""
    return Path(skills_root) / KEYS_DIRNAME


def generate_key(skills_root: Path, *, name: str = "publisher", force: bool = False) -> SigningKey:
    """Create an Ed25519 publisher keypair under ``<skills_root>/keys/``.

    The private key is written owner-read/write only. An existing key is never
    silently replaced — overwriting it would orphan every signature already made
    with it.

    Raises:
        SkillSignatureError: if a key of that name exists and *force* is False.
    """
    ed25519 = _ed25519()
    from cryptography.hazmat.primitives import serialization

    directory = keys_dir(skills_root)
    directory.mkdir(parents=True, exist_ok=True)
    private_path = directory / f"{name}.key"
    public_path = directory / f"{name}.pub"

    if private_path.exists() and not force:
        raise SkillSignatureError(
            f"A signing key already exists at {private_path}. Signatures already "
            "published were made with it, so it is not replaced automatically. "
            "Pass --force to overwrite it (old signatures stop verifying), or "
            "choose another --key-name."
        )

    private = ed25519.Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    private_path.write_text(_b64(private_raw) + "\n", encoding="utf-8")
    os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)
    public_path.write_text(_b64(public_raw) + "\n", encoding="utf-8")

    log.info("Generated skill signing key %s (%s)", key_id(public_raw), private_path)
    return SigningKey(private_bytes=private_raw, public_bytes=public_raw)


def load_key(skills_root: Path, *, name: str = "publisher") -> SigningKey:
    """Load a publisher keypair created by :func:`generate_key`.

    Raises:
        SkillSignatureError: if the key does not exist or cannot be parsed.
    """
    ed25519 = _ed25519()
    from cryptography.hazmat.primitives import serialization

    private_path = keys_dir(skills_root) / f"{name}.key"
    if not private_path.is_file():
        raise SkillSignatureError(
            f"No signing key named {name!r} at {private_path}. Create one with "
            "'gaia skill keygen', or publish unsigned with --unsigned (which caps "
            f"the skill at '{LOWEST_TIER}' for everyone who installs it). See {_DOCS}"
        )

    raw = _unb64(private_path.read_text(encoding="utf-8").strip(), what=f"{private_path}")
    try:
        private = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as exc:
        raise SkillSignatureError(
            f"{private_path} does not hold a valid Ed25519 private key: {exc}. "
            "Regenerate it with 'gaia skill keygen --force'."
        ) from exc

    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return SigningKey(private_bytes=raw, public_bytes=public_raw)


# ---------------------------------------------------------------------------
# Digest manifest
# ---------------------------------------------------------------------------


def file_digests(directory: Path) -> dict[str, str]:
    """``{posix-relative-path: sha256-hex}`` for every file under *directory*.

    ``SIGNATURE.json`` is excluded — it holds the signature over this very map,
    so including it would be circular.
    """
    directory = Path(directory)
    digests: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative == SIGNATURE_FILENAME:
            continue
        digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def signing_payload(*, name: str, version: str, digests: dict[str, str]) -> bytes:
    """The exact bytes a signature covers.

    Canonical JSON (sorted keys, no incidental whitespace) so signer and verifier
    reconstruct byte-identical input from the same bundle on any platform.
    """
    document = {
        "algorithm": _ALGORITHM,
        "files": dict(sorted(digests.items())),
        "name": name,
        "version": version,
        "signature_version": SIGNATURE_VERSION,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Sign / verify
# ---------------------------------------------------------------------------


def sign_bundle(
    directory: Path, *, name: str, version: str, key: SigningKey, publisher: str = ""
) -> dict[str, Any]:
    """Sign every file in *directory* and write ``SIGNATURE.json`` into it.

    Args:
        directory: Bundle root (the skill directory that will be zipped).
        name: Skill name, bound into the signed payload.
        version: Skill version, bound into the signed payload.
        key: The publisher keypair to sign with.
        publisher: Optional publisher identity recorded for display.

    Returns:
        The signature record that was written.
    """
    ed25519 = _ed25519()
    directory = Path(directory)
    digests = file_digests(directory)
    payload = signing_payload(name=name, version=version, digests=digests)

    private = ed25519.Ed25519PrivateKey.from_private_bytes(key.private_bytes)
    record = {
        "signature_version": SIGNATURE_VERSION,
        "algorithm": _ALGORITHM,
        "name": name,
        "version": version,
        "publisher": publisher,
        "key_id": key.key_id,
        "public_key": _b64(key.public_bytes),
        "files": digests,
        "signature": _b64(private.sign(payload)),
    }
    (directory / SIGNATURE_FILENAME).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    log.info(
        "Signed skill bundle '%s' %s with key %s (%d file(s))",
        name,
        version,
        key.key_id,
        len(digests),
    )
    return record


@dataclass(frozen=True)
class VerifiedSignature:
    """The outcome of verifying a bundle's ``SIGNATURE.json``."""

    #: False when the bundle carries no signature at all (a valid, capped state).
    signed: bool
    #: Signing key id, or "" when unsigned.
    key_id: str = ""
    #: Publisher identity the signature records, or "" when unsigned/unset.
    publisher: str = ""
    #: Trust-store role of the signing key: ``amd``, ``publisher``, or "" when
    #: the key is not in the trust store (or the bundle is unsigned).
    role: str = ""

    @property
    def trusted(self) -> bool:
        """True when the signature verified AND its key is in the trust store."""
        return self.signed and bool(self.role)

    def describe(self) -> str:
        """One-line human summary for CLI output."""
        if not self.signed:
            return "unsigned"
        if not self.role:
            return f"signed by untrusted key {self.key_id}"
        who = self.publisher or self.key_id
        return f"signed by {who} (role={self.role}, key={self.key_id})"


def verify_bundle(
    directory: Path,
    *,
    name: str,
    version: str,
    trust_store: "TrustStore",
) -> VerifiedSignature:
    """Verify a bundle's signature and resolve the signing key's trust role.

    An absent ``SIGNATURE.json`` returns ``signed=False`` — unsigned is allowed
    and simply caps the tier. Everything else that can go wrong (bad base64, a
    signature that does not verify, a digest that no longer matches, a
    name/version that does not match the bundle) raises: a present-but-wrong
    signature means the bundle was tampered with, and must not be installed.

    Raises:
        SkillSignatureError: on any signature or content mismatch.
    """
    directory = Path(directory)
    signature_path = directory / SIGNATURE_FILENAME
    if not signature_path.is_file():
        log.info(
            "Skill bundle at %s carries no %s — treating it as unsigned",
            directory,
            SIGNATURE_FILENAME,
        )
        return VerifiedSignature(signed=False)

    try:
        record = json.loads(signature_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillSignatureError(
            f"{signature_path} is not readable JSON: {exc}. The bundle's signature "
            f"is corrupt — re-download the skill. See {_DOCS}"
        ) from exc
    if not isinstance(record, dict):
        raise SkillSignatureError(
            f"{signature_path} must contain a JSON object. The bundle's signature "
            f"is malformed — re-download the skill. See {_DOCS}"
        )

    if record.get("algorithm") != _ALGORITHM:
        raise SkillSignatureError(
            f"{signature_path} declares algorithm {record.get('algorithm')!r}; this "
            f"GAIA build verifies {_ALGORITHM!r} only. Upgrade GAIA, or ask the "
            f"publisher to re-sign. See {_DOCS}"
        )
    if record.get("signature_version") != SIGNATURE_VERSION:
        raise SkillSignatureError(
            f"{signature_path} is signature format v{record.get('signature_version')}; "
            f"this GAIA build reads v{SIGNATURE_VERSION}. Upgrade GAIA to install "
            f"this skill. See {_DOCS}"
        )
    if record.get("name") != name or record.get("version") != version:
        raise SkillSignatureError(
            f"Signature in {signature_path} is for "
            f"'{record.get('name')}' {record.get('version')}, but the bundle is "
            f"'{name}' {version}. A signature does not transfer between skills or "
            f"versions — refusing to install. See {_DOCS}"
        )

    signed_digests = record.get("files")
    if not isinstance(signed_digests, dict):
        raise SkillSignatureError(
            f"{signature_path} is missing its 'files' digest map, so it attests to "
            f"nothing. Refusing to install. See {_DOCS}"
        )

    public_bytes = _unb64(record.get("public_key"), what=f"{signature_path}: public_key")
    signature = _unb64(record.get("signature"), what=f"{signature_path}: signature")

    declared_id = record.get("key_id")
    actual_id = key_id(public_bytes)
    if declared_id != actual_id:
        raise SkillSignatureError(
            f"{signature_path} claims key id {declared_id!r} but its public key "
            f"hashes to {actual_id!r}. Refusing to install. See {_DOCS}"
        )

    ed25519 = _ed25519()
    payload = signing_payload(
        name=name, version=version, digests={str(k): str(v) for k, v in signed_digests.items()}
    )
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, payload)
    except Exception as exc:  # cryptography raises InvalidSignature
        raise SkillSignatureError(
            f"The signature in {signature_path} does not verify against its own "
            f"public key ({exc.__class__.__name__}). The bundle was modified after "
            f"signing, or the signature is forged — refusing to install. See {_DOCS}"
        ) from exc

    # The signature covers the digest map; now confirm the files on disk still
    # match it. Without this, a verified signature would attest to a manifest
    # that no longer describes the bundle.
    actual = file_digests(directory)
    mismatched = sorted(
        set(actual) ^ set(signed_digests)
        | {f for f in set(actual) & set(signed_digests) if actual[f] != signed_digests[f]}
    )
    if mismatched:
        raise SkillSignatureError(
            f"Bundle contents do not match the signed digest list in {signature_path}: "
            f"{', '.join(mismatched[:8])}"
            f"{f' (+{len(mismatched) - 8} more)' if len(mismatched) > 8 else ''}. "
            f"The bundle was modified after signing — refusing to install. See {_DOCS}"
        )

    role = trust_store.role_for(actual_id)
    if not role:
        log.warning(
            "Skill '%s' %s is signed by key %s, which is not in the trust store — "
            "the signature verifies but attests to no trusted publisher",
            name,
            version,
            actual_id,
        )
    return VerifiedSignature(
        signed=True,
        key_id=actual_id,
        publisher=str(record.get("publisher") or ""),
        role=role,
    )


def attested_tier(signature: VerifiedSignature) -> str:
    """The highest tier *signature* entitles a skill to claim.

    Unsigned or untrusted-key → ``experimental``; a trusted publisher key →
    ``community``; a trust-store key with ``role: "amd"`` → ``verified``.
    """
    if not signature.trusted:
        return LOWEST_TIER
    return _ROLE_TIERS.get(signature.role, LOWEST_TIER)


# ---------------------------------------------------------------------------
# Trust store
# ---------------------------------------------------------------------------


@dataclass
class TrustStore:
    """Public keys this machine trusts, and the role each one carries.

    Persisted as ``<skills_root>/trusted-keys.json``::

        {
          "schema_version": 1,
          "keys": [
            {"key_id": "…", "public_key": "<base64>", "publisher": "acme",
             "role": "publisher"}
          ]
        }

    An absent file is an **empty** store, not an error: a machine that trusts
    nobody yet installs everything at ``experimental``, which is the correct
    conservative floor. There is no bundled AMD key — see the module docstring.
    """

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Optional[Path] = None

    @classmethod
    def load(cls, skills_root: Path) -> "TrustStore":
        """Read the trust store under *skills_root* (empty if absent)."""
        path = Path(skills_root) / TRUST_STORE_FILENAME
        if not path.is_file():
            return cls(entries={}, path=path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillValidationError(
                f"Trust store {path} is not readable JSON: {exc}. Fix or delete it — "
                "GAIA will not fall back to trusting nothing silently, because that "
                "would quietly downgrade every skill you already trusted."
            ) from exc

        entries: dict[str, dict[str, Any]] = {}
        for raw in data.get("keys") or []:
            if not isinstance(raw, dict):
                raise SkillValidationError(
                    f"Trust store {path}: every entry of 'keys' must be an object "
                    "with 'key_id', 'public_key', and 'role'."
                )
            identifier = str(raw.get("key_id") or "")
            role = str(raw.get("role") or "")
            if not identifier:
                raise SkillValidationError(
                    f"Trust store {path}: an entry is missing 'key_id'. Re-add the "
                    "key with 'gaia skill trust add'."
                )
            if role not in _ROLE_TIERS:
                raise SkillValidationError(
                    f"Trust store {path}: key {identifier} has role {role!r}; valid "
                    f"roles are {', '.join(sorted(_ROLE_TIERS))}."
                )
            entries[identifier] = {
                "key_id": identifier,
                "public_key": str(raw.get("public_key") or ""),
                "publisher": str(raw.get("publisher") or ""),
                "role": role,
            }
        return cls(entries=entries, path=path)

    def role_for(self, identifier: str) -> str:
        """Trust role of *identifier*, or "" when the key is unknown."""
        entry = self.entries.get(identifier)
        return str(entry["role"]) if entry else ""

    def add(
        self, *, public_key_b64: str, publisher: str = "", role: str = ROLE_PUBLISHER
    ) -> str:
        """Trust a public key. Returns its key id.

        Raises:
            SkillValidationError: for an unknown *role* or unusable key material.
        """
        if role not in _ROLE_TIERS:
            raise SkillValidationError(
                f"Unknown trust role {role!r}. Use 'publisher' (attests "
                f"'community') or 'amd' (attests 'verified')."
            )
        public_bytes = _unb64(public_key_b64, what="public key")
        _ed25519().Ed25519PublicKey.from_public_bytes(public_bytes)  # validate length
        identifier = key_id(public_bytes)
        self.entries[identifier] = {
            "key_id": identifier,
            "public_key": _b64(public_bytes),
            "publisher": publisher,
            "role": role,
        }
        return identifier

    def remove(self, identifier: str) -> bool:
        """Stop trusting *identifier*. Returns True if it was present."""
        return self.entries.pop(identifier, None) is not None

    def save(self, path: Optional[Path] = None) -> Path:
        """Persist the store. Returns the path written."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise SkillValidationError(
                "TrustStore.save() needs a path: this store was built in memory "
                "rather than loaded from disk."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": 1,
            "keys": [self.entries[k] for k in sorted(self.entries)],
        }
        target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        self.path = target
        return target
