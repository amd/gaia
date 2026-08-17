# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
End-to-end publish → install tests for the skills marketplace (#2467 B/C).

These are the acceptance criteria, exercised against a stand-in hub whose objects
are produced by the **real** publish path: ``publish_skill`` validates, runs the
audit gate, signs, and packages; the fake hub stores what it received and computes
the SHA-256 server-side; then ``install_skill`` resolves, downloads, checksums,
unpacks, and verifies signatures against those bytes. Nothing in the client is
stubbed out, so a checksum or signature regression fails here rather than in
production.

Cold state is the default: every root (skills, keys, trust store, lock, catalog
cache) lives under ``tmp_path``, and no test can see a skill, key, or trusted
publisher left behind by a previous run or by the developer's own machine.
"""

from __future__ import annotations

import base64
import json
import zipfile

import pytest

from gaia.skills.errors import (
    SkillNotFoundError,
    SkillPermissionError,
    SkillValidationError,
)
from gaia.skills.install import SkillInstallError, install_skill, remove_skill
from gaia.skills.lock import SkillLock
from gaia.skills.publish import SkillPublishError, publish_skill
from gaia.skills.signing import SkillSignatureError, TrustStore
from tests.unit.skills_helpers import (
    fake_hub,
    isolated_manager,
    make_key,
    write_audit_report,
)

SKILL_BODY = """# Web Research

1. Call `web-research/search_web` with the user's question.
2. Summarize the top results.
"""


def _write_source(
    tmp_path,
    *,
    name="web-research",
    version="1.0.0",
    tier="community",
    permissions=("network:read:*.brave.com",),
    description="Search the web for current information and summarize it.",
):
    """Author a publishable skill source directory."""
    directory = tmp_path / "src" / name
    directory.mkdir(parents=True)
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        f"version: {version}",
        "license: MIT",
        "metadata:",
        "  gaia:",
        f"    security_tier: {tier}",
    ]
    if permissions:
        lines.append("    permissions:")
        lines.extend(f"      - {p}" for p in permissions)
    lines += ["---", "", SKILL_BODY]
    (directory / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return directory


@pytest.fixture
def marketplace(tmp_path):
    """A cold marketplace: empty hub, empty skills root, empty trust store."""

    class Marketplace:
        def __init__(self):
            self.hub = fake_hub(tmp_path)
            self.skills_root = tmp_path / "gaia-home" / "skills"
            self.manager = isolated_manager(tmp_path, user_skills_root=self.skills_root)
            self.audit = write_audit_report(tmp_path)

        def publish(self, source, **kwargs):
            kwargs.setdefault("keys_root", self.skills_root)
            kwargs.setdefault("audit_report", self.audit)
            return publish_skill(
                source,
                token="test-token",
                hub_url=self.hub.BASE_URL,
                uploader=self.hub.accept_publish,
                **kwargs,
            )

        def install(self, reference, **kwargs):
            return install_skill(
                reference,
                manager=self.manager,
                base_url=self.hub.BASE_URL,
                fetcher=self.hub.fetcher,
                **kwargs,
            )

        def trust(self, key, *, role="publisher", publisher="acme"):
            store = TrustStore.load(self.skills_root)
            store.add(
                public_key_b64=base64.b64encode(key.public_bytes).decode("ascii"),
                publisher=publisher,
                role=role,
            )
            store.save()

        def keygen(self, name="publisher"):
            return make_key(self.skills_root, name=name)

    return Marketplace()


# ---------------------------------------------------------------------------
# Publish uploads a signed skill and writes a catalog entry
# ---------------------------------------------------------------------------


def test_publish_uploads_a_signed_bundle_and_lands_a_catalog_entry(
    marketplace, tmp_path
):
    marketplace.keygen()
    source = _write_source(tmp_path)

    result = marketplace.publish(source, publisher="acme")

    assert result.signed is True
    assert result.artifact_filename == "web-research-1.0.0.zip"
    assert result.audit.verdict == "ALLOW"

    # Assert the SHAPE of what went to the Worker, not merely that it was called.
    sent = marketplace.hub.publishes[-1]
    assert set(sent.parts()) == {"skill", "artifact", "audit"}
    # 'skill' must be the FULL SKILL.md — front matter included. The Worker parses
    # the manifest out of it; a front-matter-stripped body would 400.
    assert sent.skill_markdown.startswith("---\n")
    assert "name: web-research" in sent.skill_markdown
    assert "version: 1.0.0" in sent.skill_markdown
    assert SKILL_BODY.splitlines()[0] in sent.skill_markdown
    # A supplied report is forwarded verbatim: these four keys in, these four
    # out. (The engine adds binding fields on top — see the bound-report test.)
    assert set(json.loads(sent.audit_json)) == {
        "verdict",
        "engine",
        "audited_at",
        "findings",
    }
    # The artifact must be a real zip carrying SKILL.md and the signature.
    with zipfile.ZipFile(sent.artifact_filename and _as_zip(sent, tmp_path)) as bundle:
        names = bundle.namelist()
    assert "web-research/SKILL.md" in names
    assert "web-research/SIGNATURE.json" in names

    # The catalog entry is filterable as a skill.
    index = json.loads(
        marketplace.hub.objects[f"{marketplace.hub.BASE_URL}/index.json"]
    )
    entry = next(e for e in index["agents"] if e["id"] == "web-research")
    assert entry["type"] == "skill"
    assert entry["latest_version"] == "1.0.0"


def _as_zip(request, tmp_path):
    path = tmp_path / "sent.zip"
    path.write_bytes(request.artifact_bytes)
    return path


def test_publish_excludes_build_noise_from_the_signed_bundle(marketplace, tmp_path):
    """__pycache__ churn would make two publishes of identical source differ."""
    marketplace.keygen()
    source = _write_source(tmp_path)
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "tools.cpython-312.pyc").write_bytes(b"\x00")

    marketplace.publish(source)

    sent = marketplace.hub.publishes[-1]
    with zipfile.ZipFile(_as_zip(sent, tmp_path)) as bundle:
        assert not any("__pycache__" in n for n in bundle.namelist())


def test_publish_refuses_a_skill_with_no_version(marketplace, tmp_path):
    marketplace.keygen()
    source = tmp_path / "src" / "no-version"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: no-version\ndescription: A skill with no version at all.\n---\nBody\n",
        "utf-8",
    )
    with pytest.raises(SkillValidationError, match="no 'version:'"):
        marketplace.publish(source)
    assert marketplace.hub.publishes == []


def test_publish_refuses_the_reserved_unversioned_sentinel(marketplace, tmp_path):
    marketplace.keygen()
    source = _write_source(tmp_path, version="0.0.0", tier="experimental")
    with pytest.raises(SkillValidationError, match="0.0.0"):
        marketplace.publish(source)


def test_publish_refuses_unsigned_above_experimental(marketplace, tmp_path):
    """An unsigned 'community' claim is a promise no installer would honor."""
    source = _write_source(tmp_path, tier="community")
    with pytest.raises(SkillPublishError, match="--unsigned"):
        marketplace.publish(source, unsigned=True)
    assert marketplace.hub.publishes == []


def test_publish_allows_unsigned_experimental(marketplace, tmp_path):
    source = _write_source(tmp_path, tier="experimental", permissions=("network:read",))
    result = marketplace.publish(source, unsigned=True)
    assert result.signed is False
    assert marketplace.hub.publishes


def test_publish_refuses_a_local_capability_permission(marketplace, tmp_path):
    """v1 has no sandbox, so publishing this would publish a brick."""
    marketplace.keygen()
    source = _write_source(tmp_path, permissions=("shell:execute",))
    with pytest.raises(SkillPermissionError, match="local-capability"):
        marketplace.publish(source)
    assert marketplace.hub.publishes == []


# ---------------------------------------------------------------------------
# The audit gate
# ---------------------------------------------------------------------------


def test_publish_fails_loudly_when_the_audit_engine_is_absent(
    marketplace, tmp_path, monkeypatch
):
    """No engine and no report => refuse. Never 'proceed un-audited'."""
    from gaia.skills import audit_gate
    from gaia.skills.audit_gate import SkillAuditUnavailableError

    # The engine ships now (#2468), so absence has to be simulated: aim the
    # lookup at a module that does not exist.
    monkeypatch.setattr(
        audit_gate, "AUDIT_MODULE", "gaia.skills.audit_absent_in_this_build"
    )

    marketplace.keygen()
    source = _write_source(tmp_path)

    with pytest.raises(SkillAuditUnavailableError) as excinfo:
        marketplace.publish(source, audit_report=None)

    message = str(excinfo.value)
    assert "gaia.skills.audit" in message
    assert "2468" in message
    assert "--audit-report" in message
    assert marketplace.hub.publishes == []


def test_publish_attaches_a_report_bound_to_what_it_audited(marketplace, tmp_path):
    """The hub refuses a gated tier whose report drops the binding fields.

    ``assertReportBinding`` in ``workers/agent-hub/src/audit.ts`` 428s on a
    missing ``skill`` / ``version`` / ``cleared_tiers`` / ``manifest_digest``,
    so normalizing them away here would break every community publish.
    """
    marketplace.keygen()
    source = _write_source(tmp_path, tier="community", version="1.0.0")

    marketplace.publish(source, audit_report=None)

    sent = json.loads(marketplace.hub.publishes[-1].audit_json)
    assert sent["verdict"] == "ALLOW"
    assert sent["skill"] == "web-research"
    assert sent["version"] == "1.0.0"
    assert "community" in sent["cleared_tiers"]
    assert sent["manifest_digest"].startswith("sha256:")
    # Findings must survive as JSON, not as engine dataclasses.
    assert isinstance(sent["findings"], list)


@pytest.mark.parametrize("verdict", ["BLOCK", "REVIEW"])
def test_publish_refuses_a_blocked_or_held_audit_verdict(
    marketplace, tmp_path, verdict
):
    from gaia.skills.audit_gate import SkillAuditFailedError

    marketplace.keygen()
    source = _write_source(tmp_path)
    report = write_audit_report(
        tmp_path / "held",
        {
            "verdict": verdict,
            "engine": "test-audit/0.1.0",
            "audited_at": "2026-07-30T00:00:00+00:00",
            "findings": ["reads os.environ and posts it"],
        },
    )
    with pytest.raises(SkillAuditFailedError, match=verdict):
        marketplace.publish(source, audit_report=report)
    assert marketplace.hub.publishes == []


def test_publish_refuses_an_audit_report_with_no_engine(marketplace, tmp_path):
    """The hub 400s an unattributed verdict, so catch it before uploading."""
    from gaia.skills.audit_gate import SkillAuditUnavailableError

    marketplace.keygen()
    source = _write_source(tmp_path)
    report = write_audit_report(
        tmp_path / "anon",
        {"verdict": "ALLOW", "audited_at": "2026-07-30T00:00:00+00:00"},
    )
    with pytest.raises(SkillAuditUnavailableError, match="engine"):
        marketplace.publish(source, audit_report=report)


def test_audit_gate_uses_the_engine_when_it_is_installed(
    marketplace, tmp_path, monkeypatch
):
    """When #2468 lands, the gate calls it — this pins the contract it must meet."""
    import sys
    import types

    calls = []
    module = types.ModuleType("gaia.skills.audit")

    def audit_skill(directory):
        calls.append(directory)
        return {
            "verdict": "ALLOW",
            "engine": "gaia-skill-audit/0.1.0",
            "audited_at": "2026-07-30T00:00:00+00:00",
            "findings": [],
        }

    module.audit_skill = audit_skill
    monkeypatch.setitem(sys.modules, "gaia.skills.audit", module)

    marketplace.keygen()
    source = _write_source(tmp_path)
    result = marketplace.publish(source, audit_report=None)

    assert calls == [source]
    assert result.audit.engine == "gaia-skill-audit/0.1.0"
    assert json.loads(marketplace.hub.publishes[-1].audit_json)["engine"] == (
        "gaia-skill-audit/0.1.0"
    )


# ---------------------------------------------------------------------------
# install <name@version> lands it, lock-tracked
# ---------------------------------------------------------------------------


def test_install_lands_the_skill_and_records_the_lock(marketplace, tmp_path):
    key = marketplace.keygen()
    marketplace.trust(key)
    marketplace.publish(_write_source(tmp_path), publisher="acme")

    result = marketplace.install("web-research@^1.0")

    assert result.version == "1.0.0"
    assert result.path == marketplace.skills_root / "web-research"
    assert (result.path / "SKILL.md").is_file()
    assert result.installed_tier == "community"
    assert result.signature.role == "publisher"

    lock = SkillLock.load(marketplace.skills_root)
    entry = lock.get("web-research")
    assert entry.version == "1.0.0"
    assert entry.requested == "^1.0"
    assert entry.installed_tier == "community"
    assert entry.attested_tier == "community"
    assert entry.signature["role"] == "publisher"
    assert entry.artifact_sha256
    assert entry.permissions == ["network:read:*.brave.com"]
    # The origin actually fetched from, not the --hub-url flag: the flag is unset
    # whenever the hub came from GAIA_HUB_URL or the default, and "which hub did
    # this come from" is the provenance the lock exists to answer.
    assert entry.hub_url == marketplace.hub.BASE_URL

    # ...and the installed skill is discoverable and loadable.
    discovered = marketplace.manager.reload()
    assert "web-research" in discovered
    loaded = marketplace.manager.load("web-research")
    assert loaded.security_tier == "community"
    assert "search_web" in loaded.body


def test_an_install_that_honored_the_claim_stays_verifiable_on_disk(
    marketplace, tmp_path
):
    """No re-stamp when the signature backed the claim, so the bundle still verifies.

    The installer rewrites SKILL.md only to *lower* a tier the signature did not
    support. Rewriting invalidates the bundled SIGNATURE.json's digest for that
    file, so skipping it when nothing changed keeps the installed copy auditable.
    """
    from gaia.skills.signing import SIGNATURE_FILENAME, verify_bundle

    key = marketplace.keygen()
    marketplace.trust(key)
    result = marketplace.publish(_write_source(tmp_path), publisher="acme")
    installed = marketplace.install("web-research")

    assert installed.downgraded is False
    assert (installed.path / SIGNATURE_FILENAME).is_file()
    revalidated = verify_bundle(
        installed.path,
        name="web-research",
        version="1.0.0",
        trust_store=TrustStore.load(marketplace.skills_root),
    )
    assert revalidated.trusted is True
    assert revalidated.key_id == result.key_id


def test_a_downgraded_install_rewrites_the_manifest_and_says_so(marketplace, tmp_path):
    """The converse: a lowered tier does rewrite, and the lock keeps the evidence."""
    source = _write_source(tmp_path, tier="community", permissions=("network:read",))
    _seed_hub_directly(marketplace, source, version="1.0.0")

    result = marketplace.install("web-research", allow_experimental=True)

    assert result.downgraded is True
    assert marketplace.manager.reload()["web-research"].security_tier == "experimental"
    entry = SkillLock.load(marketplace.skills_root).get("web-research")
    assert (entry.claimed_tier, entry.installed_tier) == ("community", "experimental")


def test_install_resolves_the_highest_version_satisfying_the_pin(marketplace, tmp_path):
    key = marketplace.keygen()
    marketplace.trust(key)
    for version in ("1.0.0", "1.3.0", "2.0.0"):
        marketplace.publish(_write_source(tmp_path / version, version=version))

    assert marketplace.install("web-research@^1.0").version == "1.3.0"
    remove_skill("web-research", manager=marketplace.manager)
    assert marketplace.install("web-research").version == "2.0.0"


def test_install_refuses_a_pin_no_published_version_satisfies(marketplace, tmp_path):
    key = marketplace.keygen()
    marketplace.trust(key)
    marketplace.publish(_write_source(tmp_path))

    with pytest.raises(SkillNotFoundError, match="satisfies"):
        marketplace.install("web-research@>=9.0.0")
    assert not (marketplace.skills_root / "web-research").exists()


def test_install_refuses_to_clobber_an_existing_install_without_force(
    marketplace, tmp_path
):
    key = marketplace.keygen()
    marketplace.trust(key)
    marketplace.publish(_write_source(tmp_path))
    marketplace.install("web-research")

    with pytest.raises(SkillInstallError, match="already installed"):
        marketplace.install("web-research")

    result = marketplace.install("web-research", force=True)
    assert result.version == "1.0.0"


def test_install_of_an_unknown_skill_names_search(marketplace):
    with pytest.raises(SkillNotFoundError, match="gaia skill search"):
        marketplace.install("no-such-skill")


# ---------------------------------------------------------------------------
# Security tiers at install
# ---------------------------------------------------------------------------


def test_unsigned_skill_cannot_install_as_verified(marketplace, tmp_path):
    """The headline supply-chain property: a tier claim is not a tier."""
    source = _write_source(tmp_path, tier="experimental", permissions=("network:read",))
    marketplace.publish(source, unsigned=True)

    # Republish the same version's SKILL.md claiming 'verified', as a hostile
    # mirror (or a compromised hub) would.
    forged = source / "SKILL.md"
    forged.write_text(
        forged.read_text("utf-8").replace(
            "security_tier: experimental", "security_tier: verified"
        ),
        "utf-8",
    )
    marketplace.hub.put_skill_doc("web-research", "1.0.0", forged.read_text("utf-8"))

    # The claim disagrees with the bundle's own SKILL.md — refused outright.
    with pytest.raises(SkillValidationError, match="disagrees with"):
        marketplace.install("web-research", allow_experimental=True)


def test_a_self_consistent_verified_claim_without_a_trusted_signature_is_downgraded(
    marketplace, tmp_path
):
    """Unsigned + 'verified' everywhere still installs at experimental, not verified."""
    source = _write_source(tmp_path, tier="verified", permissions=("network:read",))
    # GAIA's own publish path refuses 'verified' unsigned, so this stages the
    # bundle directly: a hostile mirror serving a self-consistent unsigned
    # 'verified' skill. The installer must not trust that the server checked.
    _seed_hub_directly(marketplace, source, version="1.0.0")

    with pytest.raises(SkillInstallError, match="--allow-experimental"):
        marketplace.install("web-research")

    result = marketplace.install("web-research", allow_experimental=True)
    assert result.claimed_tier == "verified"
    assert result.attested_tier == "experimental"
    assert result.installed_tier == "experimental"
    assert result.downgraded is True
    # The enforced tier — not the claim — is what lands on disk. Asserted by
    # reparsing rather than string-matching: 'experimental' is the format's
    # default, so re-stamping it correctly *removes* the field.
    installed = (result.path / "SKILL.md").read_text("utf-8")
    assert "verified" not in installed
    assert marketplace.manager.reload()["web-research"].security_tier == "experimental"
    assert SkillLock.load(marketplace.skills_root).get(
        "web-research"
    ).installed_tier == ("experimental")


def _seed_hub_directly(marketplace, source, *, version):
    """Stage a bundle in the hub without going through the publish gates.

    Models a hostile or compromised hub: the client must not assume the server
    enforced anything.
    """
    import hashlib

    name = source.name
    archive = source.parent / f"{name}-{version}.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, arcname=f"{name}/{path.relative_to(source)}")
    payload = archive.read_bytes()
    marketplace.hub.put_artifact(name, version, archive.name, payload)
    marketplace.hub.put_skill_doc(
        name, version, (source / "SKILL.md").read_text("utf-8")
    )
    marketplace.hub.put_manifest(
        name,
        version,
        filename=archive.name,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    marketplace.hub.rebuild_index()


def test_experimental_requires_allow_experimental(marketplace, tmp_path):
    source = _write_source(tmp_path, tier="experimental", permissions=("network:read",))
    marketplace.publish(source, unsigned=True)

    with pytest.raises(SkillInstallError) as excinfo:
        marketplace.install("web-research")
    assert "--allow-experimental" in str(excinfo.value)
    assert not (marketplace.skills_root / "web-research").exists()

    result = marketplace.install("web-research", allow_experimental=True)
    assert result.installed_tier == "experimental"


def test_the_experimental_refusal_names_the_code_the_user_would_be_running(
    marketplace, tmp_path
):
    """ "Unsandboxed" is abstract; naming tools.py is the actual decision."""
    marketplace.keygen()
    source = _write_source(tmp_path, tier="experimental", permissions=("network:read",))
    (source / "tools.py").write_text(
        "from gaia.agents.base.tools import tool\n\n\n"
        "@tool\ndef search_web(query: str) -> dict:\n"
        '    """Search the web."""\n    return {"q": query}\n',
        "utf-8",
    )
    skill_md = source / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text("utf-8").replace(
            "    permissions:",
            "    tools:\n"
            "      - name: search_web\n"
            "        description: Search the web.\n"
            "        parameters:\n"
            "          query:\n"
            "            type: string\n"
            "            required: true\n"
            "    permissions:",
        ),
        "utf-8",
    )
    marketplace.publish(source)

    with pytest.raises(SkillInstallError) as excinfo:
        marketplace.install("web-research")
    message = str(excinfo.value)
    assert "tools.py" in message
    assert "search_web" in message
    assert "your agent's own process" in message


def test_the_experimental_refusal_says_so_when_a_skill_ships_no_code(
    marketplace, tmp_path
):
    marketplace.keygen()
    marketplace.publish(
        _write_source(tmp_path, tier="experimental", permissions=("network:read",))
    )

    with pytest.raises(SkillInstallError, match="instruction-only"):
        marketplace.install("web-research")


def test_untrusted_signature_drops_a_community_skill_to_experimental(
    marketplace, tmp_path
):
    """Signed by a stranger is integrity, not trust — so no community grant."""
    marketplace.keygen()  # signed, but the key is NOT added to the trust store
    marketplace.publish(
        _write_source(tmp_path, tier="community", permissions=("network:read",))
    )

    result = marketplace.install("web-research", allow_experimental=True)
    assert result.signature.signed is True
    assert result.signature.trusted is False
    assert result.attested_tier == "experimental"
    assert result.installed_tier == "experimental"


def test_amd_trusted_key_attests_verified(marketplace, tmp_path):
    key = marketplace.keygen()
    marketplace.trust(key, role="amd", publisher="AMD")
    marketplace.publish(_write_source(tmp_path, tier="verified"), publisher="AMD")

    result = marketplace.install("web-research")
    assert result.attested_tier == "verified"
    assert result.installed_tier == "verified"
    assert result.downgraded is False


def test_permission_ceiling_is_enforced_at_install(marketplace, tmp_path):
    """An experimental skill may not open an MCP connection."""
    key = marketplace.keygen()  # untrusted => attests experimental
    marketplace.publish(
        _write_source(
            tmp_path, tier="experimental", permissions=("mcp:connect:mcp-tavily",)
        )
    )
    del key

    with pytest.raises(SkillPermissionError, match="above the 'experimental' ceiling"):
        marketplace.install("web-research", allow_experimental=True)
    assert not (marketplace.skills_root / "web-research").exists()
    assert SkillLock.load(marketplace.skills_root).entries == {}


def test_ceiling_is_applied_to_the_effective_tier_not_the_claim(marketplace, tmp_path):
    """A 'community' claim cannot buy a community-only permission when unsigned."""
    source = _write_source(tmp_path, tier="community", permissions=("network:write",))
    _seed_hub_directly(marketplace, source, version="1.0.0")

    with pytest.raises(SkillPermissionError, match="above the 'experimental' ceiling"):
        marketplace.install("web-research", allow_experimental=True)


def test_dangerous_grant_prompts_at_community_and_refusal_blocks_the_install(
    marketplace, tmp_path
):
    key = marketplace.keygen()
    marketplace.trust(key)
    marketplace.publish(
        _write_source(tmp_path, tier="community", permissions=("network:write",))
    )

    prompts = []

    def decline(prompt):
        prompts.append(prompt)
        return False

    with pytest.raises(SkillInstallError, match="not granted"):
        marketplace.install("web-research", confirm=decline)
    assert prompts and "network:write" in prompts[0]
    assert not (marketplace.skills_root / "web-research").exists()

    result = marketplace.install("web-research", confirm=lambda _p: True)
    assert result.installed_tier == "community"


def test_verified_skill_does_not_prompt_for_dangerous_grants(marketplace, tmp_path):
    """'Auto-grant, no prompt' is the whole point of the AMD-audited tier."""
    key = marketplace.keygen()
    marketplace.trust(key, role="amd", publisher="AMD")
    marketplace.publish(
        _write_source(tmp_path, tier="verified", permissions=("network:write",))
    )

    def fail(_prompt):
        raise AssertionError("a verified skill must not prompt for a declared grant")

    result = marketplace.install("web-research", confirm=fail)
    assert result.installed_tier == "verified"


def test_assume_yes_grants_dangerous_permissions_non_interactively(
    marketplace, tmp_path
):
    key = marketplace.keygen()
    marketplace.trust(key)
    marketplace.publish(
        _write_source(tmp_path, tier="community", permissions=("network:write",))
    )

    result = marketplace.install("web-research", assume_yes=True)
    assert result.installed_tier == "community"


def test_local_capability_permission_is_refused_even_from_a_trusted_publisher(
    marketplace, tmp_path
):
    """No tier stamp substitutes for the sandbox v1 does not have."""
    key = marketplace.keygen()
    marketplace.trust(key, role="amd")
    source = _write_source(tmp_path, tier="verified", permissions=("filesystem:write",))
    _seed_hub_directly(marketplace, source, version="1.0.0")

    with pytest.raises(SkillPermissionError, match="local-capability"):
        marketplace.install("web-research")


def test_tampered_bundle_is_refused_at_install(marketplace, tmp_path):
    """A repacked artifact must not install even if the manifest hash was updated."""
    import hashlib

    key = marketplace.keygen()
    marketplace.trust(key)
    source = _write_source(tmp_path)
    marketplace.publish(source)

    # Repack the signed bundle with an extra file, and update the hub's checksum
    # so the transport check passes. Only the signature can catch this.
    original = marketplace.hub.objects[
        f"{marketplace.hub.BASE_URL}/skills/web-research/1.0.0/web-research-1.0.0.zip"
    ]
    staged = tmp_path / "repack"
    staged.mkdir()
    (staged / "in.zip").write_bytes(original)
    with zipfile.ZipFile(staged / "in.zip") as bundle:
        bundle.extractall(staged / "out")
    (staged / "out" / "web-research" / "payload.py").write_text("import os\n", "utf-8")
    repacked = staged / "web-research-1.0.0.zip"
    with zipfile.ZipFile(repacked, "w") as bundle:
        for path in sorted((staged / "out").rglob("*")):
            if path.is_file():
                bundle.write(path, arcname=str(path.relative_to(staged / "out")))
    payload = repacked.read_bytes()
    marketplace.hub.put_artifact("web-research", "1.0.0", repacked.name, payload)
    marketplace.hub.put_manifest(
        "web-research",
        "1.0.0",
        filename=repacked.name,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )

    with pytest.raises(SkillSignatureError, match="modified after signing"):
        marketplace.install("web-research")
    assert not (marketplace.skills_root / "web-research").exists()


def test_install_refuses_a_bundle_whose_skill_md_disagrees_with_the_hub_object(
    marketplace, tmp_path
):
    key = marketplace.keygen()
    marketplace.trust(key)
    marketplace.publish(_write_source(tmp_path))

    # Swap only the R2 SKILL.md object, keeping the signed bundle intact.
    doctored = (
        (tmp_path / "src" / "web-research" / "SKILL.md")
        .read_text("utf-8")
        .replace("network:read:*.brave.com", "network:write")
    )
    marketplace.hub.put_skill_doc("web-research", "1.0.0", doctored)

    with pytest.raises(SkillValidationError, match="disagrees with"):
        marketplace.install("web-research")


def test_nothing_is_written_when_a_gate_refuses(marketplace, tmp_path):
    """A refused install must leave no partial state behind."""
    marketplace.keygen()
    marketplace.publish(
        _write_source(tmp_path, tier="experimental", permissions=("network:read",))
    )

    with pytest.raises(SkillInstallError):
        marketplace.install("web-research")

    assert not (marketplace.skills_root / "web-research").exists()
    assert SkillLock.load(marketplace.skills_root).entries == {}
    assert marketplace.manager.reload() == {}


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


def test_remove_deletes_the_skill_and_its_lock_entry(marketplace, tmp_path):
    key = marketplace.keygen()
    marketplace.trust(key)
    marketplace.publish(_write_source(tmp_path))
    marketplace.install("web-research")

    result = remove_skill("web-research", manager=marketplace.manager)

    assert result.version == "1.0.0"
    assert result.was_locked is True
    assert not (marketplace.skills_root / "web-research").exists()
    assert SkillLock.load(marketplace.skills_root).entries == {}
    assert marketplace.manager.reload() == {}


def test_remove_of_an_unknown_skill_raises(marketplace):
    with pytest.raises(SkillNotFoundError, match="No installed skill"):
        remove_skill("no-such-skill", manager=marketplace.manager)


def test_remove_refuses_a_read_only_root(tmp_path):
    """Deleting a skill inside an installed agent package is not remove's business."""
    from tests.unit.skills_helpers import copy_fixture

    bundled = tmp_path / "agent" / "skills"
    bundled.mkdir(parents=True)
    copy_fixture("incident-review", bundled)
    manager = isolated_manager(
        tmp_path,
        user_skills_root=tmp_path / "home" / "skills",
        agent_skill_dirs=[bundled],
    )

    with pytest.raises(SkillInstallError, match="agent-bundled"):
        remove_skill("incident-review", manager=manager)
    assert (bundled / "incident-review").is_dir()


def test_installed_provenance_reads_from_the_lock(marketplace, tmp_path):
    from gaia.skills.install import installed_provenance

    key = marketplace.keygen()
    marketplace.trust(key)
    marketplace.publish(_write_source(tmp_path))
    marketplace.install("web-research@^1.0")

    provenance = installed_provenance(marketplace.manager)
    assert provenance["web-research"]["requested"] == "^1.0"
    assert provenance["web-research"]["installed_tier"] == "community"


# ---------------------------------------------------------------------------
# A hostile bundle: the client unpacks whatever the hub served
# ---------------------------------------------------------------------------


def _seed_raw_bundle(
    marketplace, payload: bytes, *, name="web-research", version="1.0.0"
):
    """Publish arbitrary bytes as *name*'s artifact, with a matching checksum.

    The checksum is computed over the hostile payload, so the download gate
    passes and the unpack gate is the one under test.
    """
    import hashlib

    filename = f"{name}-{version}.zip"
    marketplace.hub.put_artifact(name, version, filename, payload)
    marketplace.hub.put_skill_doc(name, version, f"---\nname: {name}\n---\nbody\n")
    marketplace.hub.put_manifest(
        name,
        version,
        filename=filename,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    marketplace.hub.rebuild_index()


def _zip_bytes(entries: dict) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for arcname, text in entries.items():
            bundle.writestr(arcname, text)
    return buffer.getvalue()


def test_install_refuses_a_bundle_that_escapes_the_destination(marketplace):
    """Zip traversal on the install path.

    ``gaia skill import`` has its own traversal guard and its own test; this is
    the separate implementation in ``install.py``, which a hostile hub reaches
    without any local file being involved.
    """
    payload = _zip_bytes(
        {
            "web-research/SKILL.md": "---\nname: web-research\n---\nbody\n",
            "../escaped.py": "import os\n",
        }
    )
    _seed_raw_bundle(marketplace, payload)

    with pytest.raises(SkillValidationError, match="escapes the destination"):
        marketplace.install("web-research", allow_experimental=True)
    assert not (marketplace.skills_root / "web-research").exists()
    assert not (marketplace.skills_root.parent / "escaped.py").exists()


def test_install_refuses_a_bundle_that_is_not_a_zip(marketplace):
    _seed_raw_bundle(marketplace, b"this is not a zip file")

    with pytest.raises(SkillValidationError, match="not a valid .zip"):
        marketplace.install("web-research", allow_experimental=True)
    assert not (marketplace.skills_root / "web-research").exists()


def test_install_refuses_a_bundle_with_no_skill_md(marketplace):
    _seed_raw_bundle(marketplace, _zip_bytes({"web-research/tools.py": "# tools\n"}))

    with pytest.raises(SkillValidationError, match="No SKILL.md in the bundle"):
        marketplace.install("web-research", allow_experimental=True)
    assert not (marketplace.skills_root / "web-research").exists()


def test_install_refuses_a_bundle_holding_more_than_one_skill(marketplace):
    """Two skills in one bundle means one of them was never named or gated."""
    _seed_raw_bundle(
        marketplace,
        _zip_bytes(
            {
                "web-research/SKILL.md": "---\nname: web-research\n---\nbody\n",
                "stowaway/SKILL.md": "---\nname: stowaway\n---\nbody\n",
            }
        ),
    )

    with pytest.raises(SkillValidationError, match="more than one skill"):
        marketplace.install("web-research", allow_experimental=True)
    assert not (marketplace.skills_root / "stowaway").exists()


def test_the_lock_records_the_default_hub_when_no_base_url_is_given(
    marketplace, tmp_path, monkeypatch
):
    """Provenance must name the hub actually fetched from, not an empty string.

    Every other install test passes ``base_url`` explicitly, so the default-origin
    half of the expression that writes ``hub_url`` is never exercised.
    """
    key = marketplace.keygen()
    marketplace.trust(key)
    marketplace.publish(_write_source(tmp_path))
    # How a user actually retargets the hub, so this exercises the same
    # resolution the lock is supposed to record.
    monkeypatch.setenv("GAIA_HUB_URL", marketplace.hub.BASE_URL)

    install_skill(
        "web-research",
        manager=marketplace.manager,
        base_url=None,
        fetcher=marketplace.hub.fetcher,
    )

    entry = SkillLock.load(marketplace.skills_root).get("web-research")
    assert entry.hub_url == marketplace.hub.BASE_URL
