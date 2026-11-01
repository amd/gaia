# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The AMD skill key GAIA trusts out of the box.

First-party skills are published signed by this key, so a cold install — empty
``trusted-keys.json`` — must install them with no flag and no trust setup, while
every other key still lands at ``experimental``.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import pytest

from gaia.skills import cli as skills_cli
from gaia.skills import signing
from gaia.skills.install import SkillInstallError, install_skill
from gaia.skills.publish import publish_skill
from gaia.skills.signing import (
    BUILTIN_TRUSTED_KEYS,
    ROLE_AMD,
    ROLE_PUBLISHER,
    TrustStore,
    key_id,
)
from tests.unit.skills_helpers import (
    fake_hub,
    isolated_manager,
    make_key,
    write_audit_report,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
AMD_KEY_ID = "32197c4382b36336fa69e041750dd0d9"


def test_builtin_key_id_is_the_hash_of_its_own_public_key():
    """A typo in the constant would trust a key nobody holds — and nothing else."""
    assert set(BUILTIN_TRUSTED_KEYS) == {AMD_KEY_ID}
    for identifier, entry in BUILTIN_TRUSTED_KEYS.items():
        raw = base64.b64decode(entry["public_key"], validate=True)
        assert len(raw) == 32
        assert key_id(raw) == identifier == entry["key_id"]
        assert entry["role"] == ROLE_AMD


def test_cold_trust_store_trusts_the_builtin_key_and_nothing_else(tmp_path):
    store = TrustStore.load(tmp_path)
    assert store.entries == {}
    assert store.role_for(AMD_KEY_ID) == ROLE_AMD
    assert store.role_for("0" * 32) == ""
    assert store.role_for("") == ""


def test_builtin_key_is_never_written_to_the_trust_store(tmp_path):
    store = TrustStore.load(tmp_path)
    publisher = make_key(tmp_path / "keys-root")
    store.add(public_key_b64=base64.b64encode(publisher.public_bytes).decode())
    store.save()

    saved = json.loads((tmp_path / signing.TRUST_STORE_FILENAME).read_text())
    assert [k["key_id"] for k in saved["keys"]] == [publisher.key_id]


def test_a_user_entry_for_the_builtin_key_takes_precedence(tmp_path):
    """An operator can demote the AMD key on their machine; never promote others."""
    store = TrustStore.load(tmp_path)
    store.add(
        public_key_b64=BUILTIN_TRUSTED_KEYS[AMD_KEY_ID]["public_key"],
        role=ROLE_PUBLISHER,
    )
    assert store.role_for(AMD_KEY_ID) == ROLE_PUBLISHER


# ---------------------------------------------------------------------------
# Publish → install from a cold machine
# ---------------------------------------------------------------------------


@pytest.fixture
def market(tmp_path):
    hub = fake_hub(tmp_path)
    publisher_root = tmp_path / "publisher-home" / "skills"
    signing_key = make_key(publisher_root, name="amd-skills")
    user_root = tmp_path / "user-home" / "skills"
    audit = write_audit_report(tmp_path)

    def publish(source):
        return publish_skill(
            source,
            token="test-token",
            hub_url=hub.BASE_URL,
            uploader=hub.accept_publish,
            keys_root=publisher_root,
            key_name="amd-skills",
            publisher="AMD",
            audit_report=audit,
        )

    def install(name, **kwargs):
        return install_skill(
            name,
            manager=isolated_manager(tmp_path, user_skills_root=user_root),
            base_url=hub.BASE_URL,
            fetcher=hub.fetcher,
            **kwargs,
        )

    return signing_key, publish, install, user_root


def _as_builtin(monkeypatch, key):
    monkeypatch.setitem(
        BUILTIN_TRUSTED_KEYS,
        key.key_id,
        {
            "key_id": key.key_id,
            "public_key": base64.b64encode(key.public_bytes).decode(),
            "publisher": "AMD",
            "role": ROLE_AMD,
        },
    )


@pytest.mark.parametrize("skill", ["summarize", "rss-digest"])
def test_a_first_party_skill_installs_on_a_cold_machine_without_flags(
    market, monkeypatch, skill
):
    """The real hub/skills folders, signed by the built-in key, install as-is."""
    signing_key, publish, install, user_root = market
    _as_builtin(monkeypatch, signing_key)

    publish(REPO_ROOT / "hub" / "skills" / skill)
    result = install(skill)

    assert not (user_root / signing.TRUST_STORE_FILENAME).exists()
    assert result.installed_tier == "community"
    assert not result.downgraded
    assert (user_root / skill / "SKILL.md").is_file()


def test_the_same_bundle_signed_by_a_non_builtin_key_is_refused(market):
    """Without the built-in entry the key is unknown, so the floor still holds."""
    _signing_key, publish, install, _user_root = market

    publish(REPO_ROOT / "hub" / "skills" / "summarize")
    with pytest.raises(SkillInstallError, match="experimental"):
        install("summarize")


# ---------------------------------------------------------------------------
# gaia skill trust
# ---------------------------------------------------------------------------


@pytest.fixture
def run_trust(tmp_path, monkeypatch, capsys):
    root = tmp_path / "gaia-home" / "skills"
    root.mkdir(parents=True)
    monkeypatch.setattr(
        skills_cli,
        "_manager",
        lambda: isolated_manager(tmp_path, user_skills_root=root),
    )

    def _run(*args):
        parser = argparse.ArgumentParser(prog="gaia")
        skills_cli.add_subparser(parser.add_subparsers(dest="action"))
        rc = skills_cli.handle(parser.parse_args(["skill", "trust", *args]))
        captured = capsys.readouterr()
        return rc, captured.out, captured.err

    return _run, root


def test_trust_list_on_a_cold_machine_shows_the_builtin_key(run_trust):
    run, _root = run_trust
    rc, out, _ = run("list")
    assert rc == 0
    line = next(row for row in out.splitlines() if AMD_KEY_ID in row)
    assert "verified" in line and "AMD (built-in)" in line


def test_trust_remove_refuses_the_builtin_key_and_leaves_no_file(run_trust):
    run, root = run_trust
    rc, _, err = run("remove", AMD_KEY_ID)
    assert rc == skills_cli.EXIT_USAGE
    assert "built into GAIA" in err
    assert not (root / signing.TRUST_STORE_FILENAME).exists()


def test_trust_remove_of_a_user_override_of_the_builtin_key_succeeds(run_trust):
    run, _root = run_trust
    rc, _, _ = run(
        "add", BUILTIN_TRUSTED_KEYS[AMD_KEY_ID]["public_key"], "--role", "publisher"
    )
    assert rc == 0
    rc, out, _ = run("list")
    line = next(row for row in out.splitlines() if AMD_KEY_ID in row)
    assert "community" in line and "built-in" not in line

    rc, _, _ = run("remove", AMD_KEY_ID)
    assert rc == 0
    _, out, _ = run("list")
    assert "AMD (built-in)" in out
