# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shared helpers for the skills unit tests (issue #888).

Every helper works off ``tmp_path`` so a test never reads or writes the
developer's real ``~/.gaia/skills`` or ``~/.claude/skills`` — the cold-state
rule from CLAUDE.md.
"""

from __future__ import annotations

import shutil
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "skills"


def copy_fixture(name: str, into: Path, *, as_name: str | None = None) -> Path:
    """Copy a fixture skill into ``into``, optionally under a different name."""
    source = FIXTURES / name
    if not source.is_dir():  # pragma: no cover - guards a typo in a test
        raise AssertionError(f"No skill fixture named {name!r} in {FIXTURES}")
    target = into / (as_name or source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def write_skill_dir(root: Path, name: str, text: str, tools: str | None = None) -> Path:
    """Write a one-off skill directory inline (for negative-path tests)."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    if tools is not None:
        (directory / "tools.py").write_text(tools, encoding="utf-8")
    return directory


def fake_hub(tmp_path: Path) -> "FakeHub":
    """A stand-in Agent Hub whose objects live under ``tmp_path``.

    Serves the four routes the installer uses — ``index.json``,
    ``skills/<n>/manifest.json``, ``skills/<n>/<v>/SKILL.md``, and the artifact —
    through a fetcher injected in place of the network. Nothing about the client
    is stubbed: it resolves, checksums, unpacks, and verifies signatures for real.
    """
    return FakeHub(tmp_path / "fake-hub")


class FakeHub:
    """In-memory hub object store plus the fetcher that reads it.

    Publishing through :meth:`accept_publish` runs the real
    ``POST /publish/skill`` contract client-side: it stores the artifact under its
    R2 key, records the server-computed SHA-256 in the per-skill manifest, and
    stores the raw ``SKILL.md`` for the version. That is what makes an install
    test meaningful — the bytes the installer verifies are the bytes a publish
    produced, not a hardcoded fixture.
    """

    BASE_URL = "https://fake-hub.test"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        #: ``{url: bytes}`` — every object the hub would serve.
        self.objects: dict[str, bytes] = {}
        #: Every publish request received, for shape assertions.
        self.publishes: list = []

    # -- fetcher -----------------------------------------------------------

    def fetcher(self, url: str) -> bytes:
        """Injectable :data:`gaia.hub.catalog.Fetcher` over :attr:`objects`."""
        try:
            return self.objects[url]
        except KeyError:
            raise FileNotFoundError(f"fake hub has no object at {url}") from None

    # -- publish -----------------------------------------------------------

    def accept_publish(self, url: str, request, token: str) -> dict:
        """Injectable :data:`gaia.skills.hub.Uploader` that stores what it receives."""
        import hashlib
        import re

        assert token, "publish must send a Bearer token"
        assert url.endswith("/publish/skill"), f"unexpected publish URL {url}"
        # The Worker rejects an artifact filename that is not one safe path
        # segment (400 invalid_artifact); assert the same rule here so a client
        # regression fails in the test, not in production.
        assert re.fullmatch(
            r"[A-Za-z0-9._+-]+", request.artifact_filename
        ), f"artifact filename {request.artifact_filename!r} is not a single safe segment"
        self.publishes.append(request)

        from gaia.skills.format import parse_skill

        skill = parse_skill(request.skill_markdown, source="<fake-hub publish>")
        name, version = skill.name, str(skill.version)
        digest = hashlib.sha256(request.artifact_bytes).hexdigest()

        self.put_artifact(
            name, version, request.artifact_filename, request.artifact_bytes
        )
        self.put_skill_doc(name, version, request.skill_markdown)
        self.put_manifest(
            name,
            version,
            filename=request.artifact_filename,
            sha256=digest,
            size_bytes=len(request.artifact_bytes),
            description=skill.description,
            security_tier=skill.security_tier,
            permissions=list(skill.gaia.permissions),
        )
        self.rebuild_index()
        return {
            "published": {
                "name": name,
                "version": version,
                "security_tier": skill.security_tier,
                "latest_version": version,
            }
        }

    # -- object writers ----------------------------------------------------

    def put_artifact(
        self, name: str, version: str, filename: str, payload: bytes
    ) -> None:
        self.objects[f"{self.BASE_URL}/skills/{name}/{version}/{filename}"] = payload

    def put_skill_doc(self, name: str, version: str, markdown: str) -> None:
        self.objects[f"{self.BASE_URL}/skills/{name}/{version}/SKILL.md"] = (
            markdown.encode("utf-8")
        )

    def put_manifest(
        self,
        name: str,
        version: str,
        *,
        filename: str,
        sha256: str,
        size_bytes: int = 0,
        description: str = "",
        security_tier: str = "experimental",
        permissions: list | None = None,
        author: str = "test-publisher",
    ) -> None:
        """Merge one version into the per-skill manifest, as the Worker does."""
        import json

        key = f"{self.BASE_URL}/skills/{name}/manifest.json"
        existing = json.loads(self.objects[key]) if key in self.objects else None
        versions = dict(existing["versions"]) if existing else {}
        versions[version] = {
            "version": version,
            "published_at": "2026-07-30T00:00:00+00:00",
            "publisher": author,
            "deprecated": False,
            "artifact": {
                "filename": filename,
                "path": f"skills/{name}/{version}/{filename}",
                "size_bytes": size_bytes,
                "sha256": sha256,
                "content_type": "application/zip",
            },
        }
        from gaia.hub.catalog import compare_versions

        latest = version
        for candidate in versions:
            if compare_versions(candidate, latest) > 0:
                latest = candidate
        self.objects[key] = json.dumps(
            {
                "name": name,
                "description": description or (existing or {}).get("description", ""),
                "author": author,
                "license": "MIT",
                "security_tier": security_tier,
                "permissions": permissions or [],
                "tools": [],
                "tools_required": [],
                "requirements": {},
                "audit": {"verdict": "ALLOW", "engine": "test/0.1.0", "findings": 0},
                "latest_version": latest,
                "versions": versions,
            }
        ).encode("utf-8")

    def rebuild_index(self) -> None:
        """Rebuild ``index.json`` with one ``type: "skill"`` entry per skill."""
        import json

        entries = []
        for url, raw in sorted(self.objects.items()):
            if not url.endswith("/manifest.json"):
                continue
            manifest = json.loads(raw)
            entries.append(
                {
                    "id": manifest["name"],
                    "name": manifest["name"],
                    "description": manifest.get("description", ""),
                    "category": "skills",
                    "latest_version": manifest["latest_version"],
                    "type": "skill",
                    "author": manifest.get("author", ""),
                    "security_tier": manifest.get("security_tier", ""),
                    "permissions": manifest.get("permissions", []),
                    "skill_metadata": {
                        "tools": manifest.get("tools", []),
                        "tools_required": manifest.get("tools_required", []),
                        "requirements": manifest.get("requirements", {}),
                        "audit": manifest.get("audit", {}),
                    },
                }
            )
        self.objects[f"{self.BASE_URL}/index.json"] = json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-07-30T00:00:00+00:00",
                "agents": entries,
            }
        ).encode("utf-8")


def allow_report(engine: str = "test-audit/0.1.0") -> dict:
    """A cleared audit report in the shape ``POST /publish/skill`` accepts."""
    return {
        "verdict": "ALLOW",
        "engine": engine,
        "audited_at": "2026-07-30T00:00:00+00:00",
        "findings": [],
    }


def write_audit_report(tmp_path: Path, payload: dict | None = None) -> Path:
    """Write an audit report JSON and return its path (for ``--audit-report``)."""
    import json

    directory = Path(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "audit.json"
    path.write_text(
        json.dumps(payload if payload is not None else allow_report()), "utf-8"
    )
    return path


def make_key(tmp_path: Path, name: str = "publisher"):
    """Generate a publisher signing keypair rooted in ``tmp_path``."""
    from gaia.skills.signing import generate_key

    return generate_key(tmp_path, name=name)


def make_marketplace(tmp_path: Path) -> "Marketplace":
    """A cold marketplace: empty hub, empty skills root, empty trust store.

    Publish and install run for real against :class:`FakeHub`, so a lock entry
    produced here carries the same digests, signature, and enforced tier a real
    install would write.
    """
    return Marketplace(tmp_path)


class Marketplace:
    """Bundles the fake hub with an isolated skills root and its helpers."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = Path(tmp_path)
        self.hub = fake_hub(self.tmp_path)
        self.skills_root = self.tmp_path / "gaia-home" / "skills"
        self.manager = isolated_manager(
            self.tmp_path, user_skills_root=self.skills_root
        )
        self.audit = write_audit_report(self.tmp_path)

    def publish(self, source, **kwargs):
        from gaia.skills.publish import publish_skill

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
        from gaia.skills.install import install_skill

        return install_skill(
            reference,
            manager=self.manager,
            base_url=self.hub.BASE_URL,
            fetcher=self.hub.fetcher,
            **kwargs,
        )

    def trust(self, key, *, role="publisher", publisher="acme"):
        import base64

        from gaia.skills.signing import TrustStore

        store = TrustStore.load(self.skills_root)
        store.add(
            public_key_b64=base64.b64encode(key.public_bytes).decode("ascii"),
            publisher=publisher,
            role=role,
        )
        store.save()

    def keygen(self, name: str = "publisher"):
        return make_key(self.skills_root, name=name)


def isolated_manager(tmp_path: Path, **kwargs):
    """A :class:`SkillManager` whose roots are all inside ``tmp_path``.

    Claude-import roots are pointed at an empty tmp directory rather than
    disabled, so precedence tests exercise the real three-root ordering.
    """
    from gaia.skills.manager import SkillManager

    user_root = kwargs.pop("user_skills_root", tmp_path / "gaia-home" / "skills")
    claude_dirs = kwargs.pop("claude_skill_dirs", [tmp_path / "claude" / "skills"])
    return SkillManager(
        user_skills_root=user_root,
        claude_skill_dirs=claude_dirs,
        **kwargs,
    )
