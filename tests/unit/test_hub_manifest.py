# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the gaia-agent.yaml manifest parser/validator (#1091)."""

import textwrap
from pathlib import Path

import pytest
import yaml

from gaia.hub import AgentManifest, ManifestError, parse
from gaia.hub.installer import VERIFIED_TIER, ensure_trust_ack, requires_trust_ack
from gaia.hub.manifest import DEFAULT_SECURITY_TIER, VALID_SECURITY_TIERS

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

VALID_PYTHON_MANIFEST = {
    "id": "my-agent",
    "name": "My Agent",
    "version": "0.1.0",
    "description": "A test agent",
    "author": "AMD",
    "license": "MIT",
    "category": "productivity",
    "tags": ["test", "demo"],
    "icon": "zap",
    "tools_count": 3,
    "language": "python",
    "min_gaia_version": "0.18.0",
    "models": ["Qwen3.5-35B-A3B-GGUF"],
    "security_tier": "community",
    "requirements": {
        "min_memory_gb": 8,
        "min_disk_gb": 2,
        "min_context_size": 4096,
        "platforms": ["win-x64", "linux-x64", "darwin-arm64"],
        "npu": True,
        "gpu_vram_gb": 6,
    },
    "python": {
        "entry_module": "gaia_agent_my.agent",
        "entry_class": "MyAgent",
        "dependencies": ["amd-gaia>=0.18.0"],
    },
    "permissions": ["filesystem:read", "network:outbound"],
    "required_connections": ["google"],
    "interfaces": {
        "tui": True,
        "cli": True,
        "pipe": True,
        "api_server": True,
        "mcp_server": False,
    },
}

VALID_CPP_MANIFEST = {
    "id": "fast-agent",
    "name": "Fast Agent",
    "version": "1.0.0",
    "description": "A native agent",
    "author": "AMD",
    "license": "MIT",
    "language": "cpp",
    "cpp": {
        "binaries": {
            "win-x64": "bin/fast-agent.exe",
            "linux-x64": "bin/fast-agent",
        },
        "static_linked": True,
    },
}


def write_manifest(tmp_path, data, name="gaia-agent.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_valid_python_manifest_parses(tmp_path):
    p = write_manifest(tmp_path, VALID_PYTHON_MANIFEST)
    m = parse(p)
    assert isinstance(m, AgentManifest)
    assert m.id == "my-agent"
    assert m.name == "My Agent"
    assert m.version == "0.1.0"
    assert m.language == "python"
    assert m.security_tier == "community"
    assert m.tools_count == 3
    assert m.tags == ["test", "demo"]
    assert m.models == ["Qwen3.5-35B-A3B-GGUF"]
    assert m.permissions == ["filesystem:read", "network:outbound"]
    assert m.required_connections == ["google"]
    assert m.source_path == p


def test_valid_python_manifest_blocks(tmp_path):
    m = parse(write_manifest(tmp_path, VALID_PYTHON_MANIFEST))
    assert m.python is not None
    assert m.python.entry_module == "gaia_agent_my.agent"
    assert m.python.entry_class == "MyAgent"
    assert m.python.dependencies == ["amd-gaia>=0.18.0"]
    assert m.requirements.min_memory_gb == 8
    assert m.requirements.min_context_size == 4096
    assert m.requirements.npu is True
    assert m.requirements.platforms == ["win-x64", "linux-x64", "darwin-arm64"]
    assert m.interfaces.tui is True
    assert m.interfaces.mcp_server is False


def test_valid_cpp_manifest_parses(tmp_path):
    m = parse(write_manifest(tmp_path, VALID_CPP_MANIFEST))
    assert m.language == "cpp"
    assert m.cpp is not None
    assert m.cpp.binaries["win-x64"] == "bin/fast-agent.exe"
    assert m.cpp.static_linked is True
    assert m.python is None


def test_minimal_manifest_uses_safe_defaults(tmp_path):
    minimal = {
        "id": "minimal",
        "name": "Minimal",
        "version": "0.1.0",
        "description": "x",
        "author": "AMD",
        "license": "MIT",
        "language": "python",
    }
    m = parse(write_manifest(tmp_path, minimal))
    # Unspecified security tier defaults to least-privileged.
    assert m.security_tier == DEFAULT_SECURITY_TIER == "experimental"
    assert m.category == "general"
    assert m.tags == []
    assert m.permissions == []
    assert m.tools_count == 0
    assert m.requirements.platforms == []
    assert m.interfaces.cli is False


def test_parse_accepts_directory(tmp_path):
    write_manifest(tmp_path, VALID_PYTHON_MANIFEST)
    m = parse(tmp_path)  # directory, not file
    assert m.id == "my-agent"


def test_from_dict_without_source():
    m = AgentManifest.from_dict(dict(VALID_CPP_MANIFEST))
    assert m.id == "fast-agent"
    assert m.source_path is None


def test_semver_prerelease_and_build_metadata():
    data = dict(VALID_PYTHON_MANIFEST, version="1.2.3-rc.1+build.5")
    m = AgentManifest.from_dict(data)
    assert m.version == "1.2.3-rc.1+build.5"


# ---------------------------------------------------------------------------
# File-level failures
# ---------------------------------------------------------------------------


def test_missing_file_raises(tmp_path):
    with pytest.raises(ManifestError, match="Manifest not found"):
        parse(tmp_path / "nope.yaml")


def test_empty_file_raises(tmp_path):
    p = tmp_path / "gaia-agent.yaml"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ManifestError, match="empty"):
        parse(p)


def test_invalid_yaml_raises(tmp_path):
    p = tmp_path / "gaia-agent.yaml"
    p.write_text("id: [unclosed\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="Invalid YAML"):
        parse(p)


def test_non_mapping_yaml_raises(tmp_path):
    p = tmp_path / "gaia-agent.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="must be a YAML mapping"):
        parse(p)


# ---------------------------------------------------------------------------
# Required field validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["id", "name", "version", "description", "author", "license"]
)
def test_missing_required_field_raises(missing):
    data = dict(VALID_PYTHON_MANIFEST)
    del data[missing]
    with pytest.raises(ManifestError, match="missing required field"):
        AgentManifest.from_dict(data)


def test_missing_required_field_names_the_field():
    data = dict(VALID_PYTHON_MANIFEST)
    del data["author"]
    with pytest.raises(ManifestError, match="author"):
        AgentManifest.from_dict(data)


def test_empty_string_required_field_raises():
    data = dict(VALID_PYTHON_MANIFEST, name="   ")
    with pytest.raises(ManifestError, match="missing required field"):
        AgentManifest.from_dict(data)


def test_missing_language_raises():
    data = dict(VALID_PYTHON_MANIFEST)
    del data["language"]
    with pytest.raises(ManifestError, match="language"):
        AgentManifest.from_dict(data)


# ---------------------------------------------------------------------------
# SemVer validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0.0", "1", "abc", "01.2.3"])
def test_invalid_semver_raises(bad):
    data = dict(VALID_PYTHON_MANIFEST, version=bad)
    with pytest.raises(ManifestError, match="SemVer"):
        AgentManifest.from_dict(data)


@pytest.mark.parametrize("good", ["0.0.1", "1.2.3", "10.20.30", "1.2.3-rc.1"])
def test_valid_semver_accepted(good):
    data = dict(VALID_PYTHON_MANIFEST, version=good)
    assert AgentManifest.from_dict(data).version == good


def test_invalid_min_gaia_version_raises():
    data = dict(VALID_PYTHON_MANIFEST, min_gaia_version="not-a-version")
    with pytest.raises(ManifestError, match="min_gaia_version"):
        AgentManifest.from_dict(data)


# ---------------------------------------------------------------------------
# ID validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "My-Agent",  # uppercase
        "-leading",  # leading hyphen
        "trailing-",  # trailing hyphen
        "has_underscore",  # underscore not allowed
        "has space",  # space
        "a" * 60,  # too long
        "",  # empty
        "agent!",  # punctuation
    ],
)
def test_invalid_id_raises(bad_id):
    data = dict(VALID_PYTHON_MANIFEST, id=bad_id)
    with pytest.raises(ManifestError, match="id"):
        AgentManifest.from_dict(data)


@pytest.mark.parametrize("good_id", ["a", "my-agent", "agent2", "a1-b2-c3"])
def test_valid_id_accepted(good_id):
    data = dict(VALID_PYTHON_MANIFEST, id=good_id)
    assert AgentManifest.from_dict(data).id == good_id


# Only ``builder`` remains a framework builtin; chat/doc/data/web/email migrated
# to standalone hub wheels (#1102) and are no longer reserved ids.
@pytest.mark.parametrize("reserved", ["builder"])
def test_reserved_id_rejected(reserved):
    data = dict(VALID_PYTHON_MANIFEST, id=reserved)
    with pytest.raises(ManifestError, match="reserved"):
        AgentManifest.from_dict(data)


# ---------------------------------------------------------------------------
# Enum-ish validation: language, security_tier
# ---------------------------------------------------------------------------


def test_invalid_language_raises():
    data = dict(VALID_PYTHON_MANIFEST, language="rust")
    with pytest.raises(ManifestError, match="language"):
        AgentManifest.from_dict(data)


# cpp is covered by test_valid_cpp_manifest_parses — it needs its own `cpp:`
# section, so it cannot ride the Python fixture here.
@pytest.mark.parametrize("language", ["python", "go", "typescript"])
def test_supported_languages_parse(language):
    """go/typescript admit the terminal hub and the Agent UI as hub packages."""
    data = dict(VALID_PYTHON_MANIFEST, language=language)
    assert AgentManifest.from_dict(data).language == language


def test_type_defaults_to_agent():
    data = dict(VALID_PYTHON_MANIFEST)
    assert "type" not in data
    assert AgentManifest.from_dict(data).type == "agent"


@pytest.mark.parametrize("pkg_type", ["agent", "app", "component"])
def test_valid_type_accepted(pkg_type):
    data = dict(VALID_PYTHON_MANIFEST, type=pkg_type)
    assert AgentManifest.from_dict(data).type == pkg_type


def test_invalid_type_raises():
    data = dict(VALID_PYTHON_MANIFEST, type="plugin")
    with pytest.raises(ManifestError, match="agent, app, component"):
        AgentManifest.from_dict(data)


def test_non_string_type_raises():
    data = dict(VALID_PYTHON_MANIFEST, type=3)
    with pytest.raises(ManifestError, match="type"):
        AgentManifest.from_dict(data)


def test_type_parses_from_yaml_file(tmp_path):
    data = dict(VALID_PYTHON_MANIFEST, type="app")
    assert parse(write_manifest(tmp_path, data)).type == "app"


@pytest.mark.parametrize("tier", ["verified", "community", "experimental"])
def test_valid_security_tier_accepted(tier):
    data = dict(VALID_PYTHON_MANIFEST, security_tier=tier)
    assert AgentManifest.from_dict(data).security_tier == tier


def test_invalid_security_tier_raises():
    data = dict(VALID_PYTHON_MANIFEST, security_tier="trusted")
    with pytest.raises(ManifestError, match="security_tier"):
        AgentManifest.from_dict(data)


# ---------------------------------------------------------------------------
# Permission syntax validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_perm",
    [
        "filesystem",  # no action
        "filesystem:",  # empty action
        ":read",  # empty domain
        "Filesystem:read",  # uppercase
        "filesystem read",  # space, no colon
        "filesystem:read:write",  # too many parts
    ],
)
def test_invalid_permission_syntax_raises(bad_perm):
    data = dict(VALID_PYTHON_MANIFEST, permissions=[bad_perm])
    with pytest.raises(ManifestError, match="permission"):
        AgentManifest.from_dict(data)


def test_unknown_permission_domain_raises():
    data = dict(VALID_PYTHON_MANIFEST, permissions=["filesytem:read"])  # typo
    with pytest.raises(ManifestError, match="unknown domain"):
        AgentManifest.from_dict(data)


def test_permissions_not_a_list_raises():
    data = dict(VALID_PYTHON_MANIFEST, permissions="filesystem:read")
    with pytest.raises(ManifestError, match="permissions must be a list"):
        AgentManifest.from_dict(data)


@pytest.mark.parametrize(
    "good_perm",
    ["filesystem:read", "network:outbound", "shell:exec", "clipboard:write"],
)
def test_valid_permission_accepted(good_perm):
    data = dict(VALID_PYTHON_MANIFEST, permissions=[good_perm])
    assert AgentManifest.from_dict(data).permissions == [good_perm]


# ---------------------------------------------------------------------------
# Requirements / interfaces / cpp section validation
# ---------------------------------------------------------------------------


def test_unknown_platform_raises():
    data = dict(VALID_PYTHON_MANIFEST)
    data["requirements"] = dict(data["requirements"], platforms=["solaris-sparc"])
    with pytest.raises(ManifestError, match="unknown platform"):
        AgentManifest.from_dict(data)


def test_negative_memory_raises():
    data = dict(VALID_PYTHON_MANIFEST)
    data["requirements"] = dict(data["requirements"], min_memory_gb=-1)
    with pytest.raises(ManifestError, match="min_memory_gb"):
        AgentManifest.from_dict(data)


# ---------------------------------------------------------------------------
# requirements.min_lemonade_version
#
# This block exists because the field was declared in a shipped manifest and
# silently dropped by the parser for months. A test that only compares the YAML
# text to a constant cannot catch that — it proves two files agree while the
# value reaches nobody. These assert the parsed object actually carries it.
# ---------------------------------------------------------------------------


def test_min_lemonade_version_survives_parsing():
    data = dict(VALID_PYTHON_MANIFEST)
    data["requirements"] = dict(data["requirements"], min_lemonade_version="10.2.0")

    parsed = AgentManifest.from_dict(data)

    assert parsed.requirements.min_lemonade_version == "10.2.0"


def test_min_lemonade_version_survives_a_round_trip_through_yaml(tmp_path):
    """The path a real agent takes: YAML on disk → parse → consumer."""
    data = dict(VALID_PYTHON_MANIFEST)
    data["requirements"] = dict(data["requirements"], min_lemonade_version="11.5.0")
    path = tmp_path / "gaia-agent.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert parse(path).requirements.min_lemonade_version == "11.5.0"


def test_min_lemonade_version_is_optional():
    """Most agents declare no backend floor; that is not an error."""
    data = dict(VALID_PYTHON_MANIFEST)
    data["requirements"] = {
        k: v for k, v in data["requirements"].items() if k != "min_lemonade_version"
    }

    assert AgentManifest.from_dict(data).requirements.min_lemonade_version is None


def test_absent_requirements_block_leaves_min_lemonade_version_none():
    data = dict(VALID_PYTHON_MANIFEST)
    data.pop("requirements")

    assert AgentManifest.from_dict(data).requirements.min_lemonade_version is None


def test_dict_to_requirements_coercion_keeps_min_lemonade_version():
    """The second dict → Requirements path must not drop it either.

    ``check_compatibility`` accepts a raw mapping and coerces it. That converter
    is a separate parse path from the manifest one, and a field added to only
    half of them is the same silent drop with a different entry point.
    """
    from gaia.hub.compatibility import _coerce_requirements

    coerced = _coerce_requirements({"min_lemonade_version": "10.2.0"})

    assert coerced.min_lemonade_version == "10.2.0"


def test_malformed_min_lemonade_version_raises():
    """A floor nothing can compare against is worse than none — fail loudly.

    A readiness check that cannot parse the declared minimum reports the
    version gate as indeterminate, so a typo here would silently disable the
    very check the field exists to drive.
    """
    data = dict(VALID_PYTHON_MANIFEST)
    data["requirements"] = dict(data["requirements"], min_lemonade_version="10.2")

    with pytest.raises(ManifestError, match="min_lemonade_version"):
        AgentManifest.from_dict(data)


def test_bool_tools_count_raises():
    data = dict(VALID_PYTHON_MANIFEST, tools_count=True)
    with pytest.raises(ManifestError, match="tools_count"):
        AgentManifest.from_dict(data)


def test_unknown_interface_key_raises():
    data = dict(VALID_PYTHON_MANIFEST)
    data["interfaces"] = {"tui": True, "websocket": True}
    with pytest.raises(ManifestError, match="unknown key"):
        AgentManifest.from_dict(data)


def test_non_bool_interface_raises():
    data = dict(VALID_PYTHON_MANIFEST)
    data["interfaces"] = {"cli": "yes"}
    with pytest.raises(ManifestError, match="interfaces.cli"):
        AgentManifest.from_dict(data)


def test_cpp_language_without_binaries_raises():
    data = dict(VALID_CPP_MANIFEST)
    del data["cpp"]
    with pytest.raises(ManifestError, match="no 'cpp.binaries'"):
        AgentManifest.from_dict(data)


def test_cpp_empty_binaries_raises():
    data = dict(VALID_CPP_MANIFEST, cpp={"binaries": {}, "static_linked": False})
    with pytest.raises(ManifestError, match="no 'cpp.binaries'"):
        AgentManifest.from_dict(data)


def test_cpp_unknown_platform_binary_raises():
    data = dict(
        VALID_CPP_MANIFEST,
        cpp={"binaries": {"bsd-x64": "bin/x"}},
    )
    with pytest.raises(ManifestError, match="unknown platform"):
        AgentManifest.from_dict(data)


def test_tags_not_list_of_strings_raises():
    data = dict(VALID_PYTHON_MANIFEST, tags=[1, 2, 3])
    with pytest.raises(ManifestError, match="tags must be a list of strings"):
        AgentManifest.from_dict(data)


def test_real_world_yaml_text(tmp_path):
    """Exercise an end-to-end YAML matching the spec example."""
    text = textwrap.dedent("""
        id: chatty
        name: Chatty
        version: 0.1.0
        description: "General conversation"
        author: AMD
        license: MIT

        category: conversation
        tags: [chat, general]
        icon: message-circle
        tools_count: 0

        language: python
        min_gaia_version: "0.18.0"
        models: [Qwen3.5-35B-A3B-GGUF]

        requirements:
          min_memory_gb: 8
          platforms: [win-x64, linux-x64, darwin-arm64]

        interfaces:
          tui: true
          cli: true
          pipe: true
          api_server: true
          mcp_server: true
        """)
    p = tmp_path / "gaia-agent.yaml"
    p.write_text(text, encoding="utf-8")
    m = parse(p)
    assert m.id == "chatty"
    assert m.requirements.min_memory_gb == 8
    assert m.interfaces.mcp_server is True


# ---------------------------------------------------------------------------
# Shipped manifests
#
# Mirrors the Worker's equivalent block (workers/agent-hub/test/manifest.test.ts):
# parse the manifests this repo actually publishes, with the parser that runs in
# production. Without this, a bad edit to a shipped manifest is only caught by
# the release job that tries to publish it.
# ---------------------------------------------------------------------------

SHIPPED_AGENT_MANIFESTS = sorted(
    (REPO_ROOT / "hub" / "agents").glob("*/python/gaia-agent.yaml")
)


def test_shipped_agent_manifests_are_discoverable():
    """A path typo above would silently parametrize zero manifests."""
    assert SHIPPED_AGENT_MANIFESTS, f"no shipped manifests under {REPO_ROOT}/hub/agents"


@pytest.mark.parametrize(
    "manifest_path", SHIPPED_AGENT_MANIFESTS, ids=lambda p: p.parent.parent.name
)
def test_shipped_agent_manifest_parses(manifest_path):
    # The id is not asserted against the directory name: some agents ship a
    # registry id that differs deliberately (analyst -> data, browser -> web).
    m = parse(manifest_path)
    assert m.id
    assert m.security_tier in VALID_SECURITY_TIERS


def test_email_agent_declares_the_verified_tier():
    """The email manifest omitted ``security_tier``, so it published as
    ``experimental`` — and a non-verified agent refuses to install without an
    explicit trust opt-in, making ``gaia agent install email`` fail for a
    first-time user of AMD's own agent.
    """
    path = REPO_ROOT / "hub" / "agents" / "email" / "python" / "gaia-agent.yaml"

    assert "security_tier" in yaml.safe_load(path.read_text(encoding="utf-8")), (
        "gaia-agent.yaml must declare security_tier explicitly — an omitted tier "
        f"defaults to {DEFAULT_SECURITY_TIER!r}, which forces `--trust` on install"
    )
    assert parse(path).security_tier == VERIFIED_TIER


def test_email_agent_installs_without_a_trust_opt_in():
    """The tier only matters through the install gate, so assert the gate itself
    rather than just the parsed value (the min_lemonade_version lesson above).
    """
    path = REPO_ROOT / "hub" / "agents" / "email" / "python" / "gaia-agent.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert requires_trust_ack(raw) is False
    ensure_trust_ack("email", raw, trusted=False)  # must not raise
