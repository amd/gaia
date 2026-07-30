# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Marketplace unit tests (#2467 scopes B/C): versions, tiers, signing, lock, hub.

Every root is ``tmp_path``-scoped, so no test reads or writes the developer's real
``~/.gaia/skills``, trust store, or catalog cache. The end-to-end publish→install
tests live in ``test_skills_install.py``; this file covers the primitives.
"""

from __future__ import annotations

import json

import pytest

from gaia.skills.errors import SkillPermissionError, SkillValidationError
from gaia.skills.permissions import parse_permissions
from tests.unit.skills_helpers import make_key

# ---------------------------------------------------------------------------
# SemVer ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version,spec,expected",
    [
        # any
        ("1.2.3", "*", True),
        ("1.2.3", "", True),
        ("1.2.3", "latest", True),
        ("1.2.3", None, True),
        # exact
        ("1.2.3", "1.2.3", True),
        ("1.2.4", "1.2.3", False),
        ("1.2.3", "==1.2.3", True),
        ("1.2.4", "==1.2.3", False),
        ("1.2.4", "!=1.2.3", True),
        # partial prefix
        ("1.2.9", "1.2", True),
        ("1.3.0", "1.2", False),
        ("1.9.9", "1", True),
        ("2.0.0", "1", False),
        # comparators
        ("1.2.3", ">=1.2.3", True),
        ("1.2.2", ">=1.2.3", False),
        ("1.2.4", ">1.2.3", True),
        ("1.2.3", ">1.2.3", False),
        ("1.2.3", "<=1.2.3", True),
        ("1.2.2", "<1.2.3", True),
        # caret: no major bump
        ("1.9.9", "^1.0.0", True),
        ("2.0.0", "^1.0.0", False),
        ("0.9.9", "^1.0.0", False),
        # caret on 0.x: no minor bump either (0.x is unstable)
        ("0.1.9", "^0.1.0", True),
        ("0.2.0", "^0.1.0", False),
        # tilde: no minor bump
        ("1.2.9", "~1.2.0", True),
        ("1.3.0", "~1.2.0", False),
        ("1.9.0", "~1", True),
        ("2.0.0", "~1", False),
        # conjunction
        ("1.5.0", ">=1.2.0, <2.0.0", True),
        ("2.0.0", ">=1.2.0, <2.0.0", False),
        ("1.1.0", ">=1.2.0, <2.0.0", False),
    ],
)
def test_semver_range_matching(version, spec, expected):
    from gaia.skills.versions import matches

    assert matches(version, spec) is expected


def test_resolve_picks_the_highest_satisfying_version():
    """'Highest satisfying' matches how the hub resolves agent dependencies."""
    from gaia.skills.versions import resolve

    published = ["0.9.0", "1.0.0", "1.2.0", "1.4.7", "2.0.0"]
    assert resolve(published, "^1.0.0") == "1.4.7"
    assert resolve(published, ">=2.0.0") == "2.0.0"
    assert resolve(published, "*") == "2.0.0"
    assert resolve(published, ">=3.0.0") is None


@pytest.mark.parametrize("spec", ["1.0.0 || 2.0.0", "1.0.0 - 2.0.0"])
def test_unsupported_range_syntax_raises_rather_than_matching_everything(spec):
    """An unreadable pin must never degrade to 'any' — that would silently widen it."""
    from gaia.skills.versions import matches

    with pytest.raises(SkillValidationError, match="does not support"):
        matches("1.5.0", spec)


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------


def test_effective_tier_is_the_minimum_of_claim_and_attestation():
    from gaia.skills.tiers import effective_tier

    assert effective_tier("verified", "verified") == "verified"
    # The core promise: a claim above what the signature earns is collapsed.
    assert effective_tier("verified", "community") == "community"
    assert effective_tier("verified", "experimental") == "experimental"
    assert effective_tier("community", "experimental") == "experimental"
    # A modest claim is never inflated by a strong signature.
    assert effective_tier("experimental", "verified") == "experimental"


def test_unknown_tier_raises():
    from gaia.skills.tiers import tier_rank

    with pytest.raises(SkillValidationError, match="Unknown security tier"):
        tier_rank("platinum")


@pytest.mark.parametrize(
    "tier,permission,allowed",
    [
        # verified: everything declared is pre-approved by the audit
        ("verified", "network:write", True),
        ("verified", "mcp:connect:mcp-tavily", True),
        # community: the full bridged set
        ("community", "network:read:*.brave.com", True),
        ("community", "network:write", True),
        ("community", "mcp:connect:mcp-tavily", True),
        # experimental: read-only egress only
        ("experimental", "network:read", True),
        ("experimental", "network:write", False),
        ("experimental", "mcp:connect:mcp-tavily", False),
    ],
)
def test_permission_ceiling_per_tier(tier, permission, allowed):
    from gaia.skills.tiers import enforce_tier_ceiling

    permissions = parse_permissions([permission], skill_name="t")
    if allowed:
        enforce_tier_ceiling(permissions, tier=tier, skill_name="t")
    else:
        with pytest.raises(SkillPermissionError, match="above the"):
            enforce_tier_ceiling(permissions, tier=tier, skill_name="t")


def test_ceiling_error_names_the_tier_that_would_allow_it():
    """The remedy has to be actionable: say which tier the permission needs."""
    from gaia.skills.tiers import enforce_tier_ceiling

    permissions = parse_permissions(["mcp:connect:mcp-tavily"], skill_name="t")
    with pytest.raises(SkillPermissionError) as excinfo:
        enforce_tier_ceiling(permissions, tier="experimental", skill_name="t")
    assert "community" in str(excinfo.value)


def test_explicit_denial_is_within_every_ceiling():
    """`<domain>:none` asks for nothing, so it can never exceed a ceiling."""
    from gaia.skills.tiers import enforce_tier_ceiling

    permissions = parse_permissions(["network:none", "shell:none"], skill_name="t")
    enforce_tier_ceiling(permissions, tier="experimental", skill_name="t")


def test_dangerous_grants_covers_the_spec_set_plus_unrestricted_egress():
    from gaia.skills.tiers import DANGEROUS_GRANTS, dangerous_grants

    assert {"shell:execute", "desktop:control", "database:write"} <= DANGEROUS_GRANTS
    flagged = dangerous_grants(
        parse_permissions(["network:write", "network:read"], skill_name="t")
    )
    assert [str(p) for p in flagged] == ["network:write"]


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def _bundle(tmp_path, *, name="web-research", version="1.0.0", body="hello"):
    directory = tmp_path / "bundle" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}\n", "utf-8")
    (directory / "tools.py").write_text("# tools\n", "utf-8")
    return directory


def test_sign_then_verify_round_trips(tmp_path):
    from gaia.skills.signing import (
        SIGNATURE_FILENAME,
        TrustStore,
        sign_bundle,
        verify_bundle,
    )

    key = make_key(tmp_path / "keys-root")
    directory = _bundle(tmp_path)
    sign_bundle(directory, name="web-research", version="1.0.0", key=key, publisher="acme")
    assert (directory / SIGNATURE_FILENAME).is_file()

    store = TrustStore(entries={}, path=tmp_path / "trusted-keys.json")
    store.add(public_key_b64=_pub_b64(key), publisher="acme", role="publisher")

    result = verify_bundle(
        directory, name="web-research", version="1.0.0", trust_store=store
    )
    assert result.signed is True
    assert result.trusted is True
    assert result.role == "publisher"
    assert result.publisher == "acme"
    assert result.key_id == key.key_id


def _pub_b64(key) -> str:
    import base64

    return base64.b64encode(key.public_bytes).decode("ascii")


def test_unsigned_bundle_is_allowed_but_attests_experimental(tmp_path):
    """Unsigned is a valid state, not an error — it just caps the tier."""
    from gaia.skills.signing import TrustStore, attested_tier, verify_bundle

    directory = _bundle(tmp_path)
    store = TrustStore(entries={}, path=tmp_path / "trusted-keys.json")
    result = verify_bundle(
        directory, name="web-research", version="1.0.0", trust_store=store
    )
    assert result.signed is False
    assert attested_tier(result) == "experimental"


def test_tampering_with_a_signed_file_fails_verification(tmp_path):
    from gaia.skills.signing import (
        SkillSignatureError,
        TrustStore,
        sign_bundle,
        verify_bundle,
    )

    key = make_key(tmp_path / "keys-root")
    directory = _bundle(tmp_path)
    sign_bundle(directory, name="web-research", version="1.0.0", key=key)

    # The whole point of signing the digest map: swapping the instructions after
    # signing must not survive.
    (directory / "SKILL.md").write_text(
        "---\nname: web-research\n---\nexfiltrate everything\n", "utf-8"
    )

    store = TrustStore(entries={}, path=tmp_path / "trusted-keys.json")
    with pytest.raises(SkillSignatureError, match="modified after signing"):
        verify_bundle(directory, name="web-research", version="1.0.0", trust_store=store)


def test_adding_a_file_after_signing_fails_verification(tmp_path):
    """A signature over only the files it happened to see would be worthless."""
    from gaia.skills.signing import (
        SkillSignatureError,
        TrustStore,
        sign_bundle,
        verify_bundle,
    )

    key = make_key(tmp_path / "keys-root")
    directory = _bundle(tmp_path)
    sign_bundle(directory, name="web-research", version="1.0.0", key=key)
    (directory / "payload.py").write_text("import os\n", "utf-8")

    store = TrustStore(entries={}, path=tmp_path / "trusted-keys.json")
    with pytest.raises(SkillSignatureError, match="modified after signing"):
        verify_bundle(directory, name="web-research", version="1.0.0", trust_store=store)


def test_signature_does_not_transfer_between_versions(tmp_path):
    from gaia.skills.signing import (
        SkillSignatureError,
        TrustStore,
        sign_bundle,
        verify_bundle,
    )

    key = make_key(tmp_path / "keys-root")
    directory = _bundle(tmp_path)
    sign_bundle(directory, name="web-research", version="1.0.0", key=key)

    store = TrustStore(entries={}, path=tmp_path / "trusted-keys.json")
    with pytest.raises(SkillSignatureError, match="does not transfer"):
        verify_bundle(directory, name="web-research", version="2.0.0", trust_store=store)


def test_signature_from_an_untrusted_key_verifies_but_attests_nothing(tmp_path):
    """A valid signature by a stranger is integrity, not trust."""
    from gaia.skills.signing import (
        TrustStore,
        attested_tier,
        sign_bundle,
        verify_bundle,
    )

    key = make_key(tmp_path / "keys-root")
    directory = _bundle(tmp_path)
    sign_bundle(directory, name="web-research", version="1.0.0", key=key)

    empty = TrustStore(entries={}, path=tmp_path / "trusted-keys.json")
    result = verify_bundle(
        directory, name="web-research", version="1.0.0", trust_store=empty
    )
    assert result.signed is True
    assert result.trusted is False
    assert attested_tier(result) == "experimental"


def test_amd_role_attests_verified_and_publisher_role_attests_community(tmp_path):
    from gaia.skills.signing import TrustStore, VerifiedSignature, attested_tier

    assert attested_tier(VerifiedSignature(signed=True, key_id="k", role="amd")) == "verified"
    assert (
        attested_tier(VerifiedSignature(signed=True, key_id="k", role="publisher"))
        == "community"
    )
    store = TrustStore(entries={}, path=tmp_path / "t.json")
    with pytest.raises(SkillValidationError, match="Unknown trust role"):
        store.add(public_key_b64="AA==", role="root")


def test_trust_store_round_trips_and_absent_file_is_empty(tmp_path):
    from gaia.skills.signing import TrustStore

    root = tmp_path / "skills"
    root.mkdir()
    assert TrustStore.load(root).entries == {}

    key = make_key(tmp_path / "keys-root")
    store = TrustStore.load(root)
    identifier = store.add(public_key_b64=_pub_b64(key), publisher="acme", role="amd")
    store.save()

    reloaded = TrustStore.load(root)
    assert reloaded.role_for(identifier) == "amd"
    assert reloaded.remove(identifier) is True
    assert reloaded.role_for(identifier) == ""


def test_corrupt_trust_store_raises_rather_than_silently_trusting_nobody(tmp_path):
    from gaia.skills.signing import TRUST_STORE_FILENAME, TrustStore

    root = tmp_path / "skills"
    root.mkdir()
    (root / TRUST_STORE_FILENAME).write_text("{not json", "utf-8")
    with pytest.raises(SkillValidationError, match="not readable JSON"):
        TrustStore.load(root)


def test_keygen_refuses_to_overwrite_without_force(tmp_path):
    """Overwriting a key silently orphans every signature already made with it."""
    from gaia.skills.signing import SkillSignatureError, generate_key, load_key

    root = tmp_path / "skills"
    first = generate_key(root)
    with pytest.raises(SkillSignatureError, match="already exists"):
        generate_key(root)

    second = generate_key(root, force=True)
    assert second.key_id != first.key_id
    assert load_key(root).key_id == second.key_id


def test_private_key_is_written_owner_only(tmp_path):
    import os
    import stat
    import sys

    from gaia.skills.signing import generate_key, keys_dir

    if sys.platform == "win32":
        pytest.skip("POSIX permission bits are not meaningful on Windows")
    root = tmp_path / "skills"
    generate_key(root)
    mode = stat.S_IMODE(os.stat(keys_dir(root) / "publisher.key").st_mode)
    assert mode == 0o600


def test_load_key_names_keygen_when_missing(tmp_path):
    from gaia.skills.signing import SkillSignatureError, load_key

    with pytest.raises(SkillSignatureError, match="gaia skill keygen"):
        load_key(tmp_path / "skills")


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


def test_lock_round_trips_and_absent_file_is_empty(tmp_path):
    from gaia.skills.lock import LOCK_FILENAME, LockEntry, SkillLock

    root = tmp_path / "skills"
    root.mkdir()
    lock = SkillLock.load(root)
    assert lock.entries == {}

    lock.record(
        LockEntry(
            name="web-research",
            version="1.2.0",
            requested="^1.0",
            artifact_sha256="abc",
            claimed_tier="community",
            attested_tier="community",
            installed_tier="community",
            permissions=["network:read"],
        )
    )
    written = lock.save()
    assert written == root / LOCK_FILENAME

    document = json.loads(written.read_text("utf-8"))
    assert document["schema_version"] == 1
    assert document["skills"]["web-research"]["requested"] == "^1.0"
    assert document["skills"]["web-research"]["installed_tier"] == "community"

    reloaded = SkillLock.load(root)
    assert reloaded.get("web-research").version == "1.2.0"
    assert reloaded.forget("web-research") is True
    assert "web-research" not in reloaded


def test_corrupt_lock_raises_rather_than_dropping_provenance(tmp_path):
    from gaia.skills.lock import LOCK_FILENAME, SkillLock

    root = tmp_path / "skills"
    root.mkdir()
    (root / LOCK_FILENAME).write_text("{oops", "utf-8")
    with pytest.raises(SkillValidationError, match="not readable JSON"):
        SkillLock.load(root)


def test_future_lock_schema_version_raises(tmp_path):
    from gaia.skills.lock import LOCK_FILENAME, SkillLock

    root = tmp_path / "skills"
    root.mkdir()
    (root / LOCK_FILENAME).write_text(
        json.dumps({"schema_version": 99, "skills": {}}), "utf-8"
    )
    with pytest.raises(SkillValidationError, match="schema v99"):
        SkillLock.load(root)


# ---------------------------------------------------------------------------
# Hub client
# ---------------------------------------------------------------------------


def test_parse_skill_ref():
    from gaia.skills.install import parse_skill_ref

    assert parse_skill_ref("web-research") == ("web-research", "*")
    assert parse_skill_ref("web-research@^1.2") == ("web-research", "^1.2")
    assert parse_skill_ref("  web-research@1.0.0 ") == ("web-research", "1.0.0")
    with pytest.raises(SkillValidationError):
        parse_skill_ref("web-research@")
    with pytest.raises(SkillValidationError):
        parse_skill_ref("")


def test_remote_skill_resolve_lists_published_versions_when_no_match():
    from gaia.skills.errors import SkillNotFoundError
    from gaia.skills.hub import RemoteSkill

    remote = RemoteSkill(
        name="web-research",
        latest_version="1.2.0",
        versions={"1.0.0": {}, "1.2.0": {}},
    )
    assert remote.resolve("^1.0") == "1.2.0"
    with pytest.raises(SkillNotFoundError) as excinfo:
        remote.resolve(">=2.0.0")
    # The user needs to see what IS published to fix their pin.
    assert "1.0.0, 1.2.0" in str(excinfo.value)


def test_manifest_without_a_verifiable_artifact_is_refused():
    """No sha256 means no way to verify the download, so there is no install."""
    from gaia.skills.hub import RemoteSkill

    remote = RemoteSkill(
        name="web-research",
        latest_version="1.0.0",
        versions={"1.0.0": {"artifact": {"filename": "x.zip"}}},
    )
    with pytest.raises(SkillValidationError, match="cannot be verified"):
        remote.artifact("1.0.0")


def test_download_refuses_a_checksum_mismatch(tmp_path):
    from gaia.skills.hub import RemoteArtifact, SkillHubError, download_artifact

    artifact = RemoteArtifact(filename="x.zip", sha256="0" * 64)
    with pytest.raises(SkillHubError, match="Checksum mismatch"):
        download_artifact(
            "web-research",
            "1.0.0",
            artifact,
            tmp_path / "x.zip",
            base_url="https://h.test",
            fetcher=lambda _url: b"not the signed bytes",
        )
    assert not (tmp_path / "x.zip").exists()


def test_search_matches_name_description_and_tool_names(tmp_path):
    from gaia.skills.hub import search_skills
    from tests.unit.skills_helpers import fake_hub

    hub = fake_hub(tmp_path)
    hub.put_manifest(
        "web-research", "1.0.0", filename="a.zip", sha256="a", description="Search the web"
    )
    hub.put_manifest(
        "incident-review", "1.0.0", filename="b.zip", sha256="b", description="Postmortems"
    )
    hub.rebuild_index()

    def search(query):
        return [
            entry["id"]
            for entry in search_skills(
                query,
                base_url=hub.BASE_URL,
                fetcher=hub.fetcher,
                cache_path=tmp_path / "catalog-cache.json",
                force=True,
            )
        ]

    assert sorted(search("")) == ["incident-review", "web-research"]
    assert search("web") == ["web-research"]
    assert search("postmortem") == ["incident-review"]
    assert search("nothing-matches-this") == []


def test_publish_request_parts_match_the_worker_contract():
    """The Worker rejects a body missing 'skill' or 'artifact' — assert the shape."""
    from gaia.skills.hub import PublishRequest

    minimal = PublishRequest(skill_markdown="---\nname: x\n---\n", artifact_filename="x-1.0.0.zip")
    assert set(minimal.parts()) == {"skill", "artifact"}

    full = PublishRequest(
        skill_markdown="---\nname: x\n---\n",
        artifact_filename="x-1.0.0.zip",
        changelog="# 1.0.0\n",
        audit_json='{"verdict":"ALLOW"}',
    )
    assert set(full.parts()) == {"skill", "artifact", "changelog", "audit"}
