# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for ``hub/skills/deploy-cloudish/tools.py``.

The HTTP layer is stubbed: these check the request each tool builds (endpoint,
method, headers, JSON vs multipart shape) and that bad input is refused before
anything leaves the machine. No call reaches cloudish.ai.
"""

from __future__ import annotations

import importlib.util
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "hub" / "skills" / "deploy-cloudish"


class _Response:
    def __init__(self, payload: Any = None, status_code: int = 200) -> None:
        self._payload = {} if payload is None else payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = json.dumps(self._payload)

    def json(self) -> Any:
        return self._payload


@pytest.fixture
def cloudish():
    """The skill's module, with the global tool registry restored afterwards."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    before = dict(_TOOL_REGISTRY)
    spec = importlib.util.spec_from_file_location(
        "deploy_cloudish_tools", SKILL_ROOT / "tools.py"
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)


@pytest.fixture
def calls(cloudish, monkeypatch) -> list[dict[str, Any]]:
    """Record every outgoing request and answer with ``{"ok": true}``."""
    recorded: list[dict[str, Any]] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> _Response:
        recorded.append({"method": method, "url": url, **kwargs})
        return _Response({"ok": True})

    monkeypatch.setattr(cloudish.requests, "request", fake_request)
    return recorded


@pytest.fixture
def context_dir(cloudish, monkeypatch, tmp_path: Path) -> Path:
    """A source folder wired to the module's checked-read helpers."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / ".env").write_text("SECRET=1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("x\n")

    monkeypatch.setattr(cloudish, "check_read", lambda path: Path(path))
    monkeypatch.setattr(
        cloudish,
        "list_readable",
        lambda base: sorted(p for p in base.rglob("*") if p.is_file()),
    )
    monkeypatch.setattr(cloudish, "open_read", lambda path, mode="rb": open(path, mode))
    return tmp_path


def test_image_deploy_posts_json_to_the_fixed_endpoint(calls, cloudish):
    result = cloudish.cloudish_deploy(
        api_key="k",
        name="my-app",
        image="ghcr.io/acme/app:1.0.0",
        port=3000,
        env={"MODE": "prod"},
        always_on=True,
    )

    assert result == {"ok": True}
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://cloudish.ai/api/v1/projects"
    assert call["headers"] == {"Authorization": "Bearer k"}
    assert call["json"] == {
        "name": "my-app",
        "image": "ghcr.io/acme/app:1.0.0",
        "port": 3000,
        "env": {"MODE": "prod"},
        "alwaysOn": True,
    }
    assert call["data"] is None
    assert call["files"] is None


def test_source_deploy_sends_multipart_with_string_form_fields(
    calls, cloudish, context_dir
):
    cloudish.cloudish_deploy(
        api_key="k",
        name="my-app",
        context_dir=str(context_dir),
        sleep_after_minutes=30,
        volume_size_gb=5,
        build_cpu_cores=1.0,
        build_memory_gb=2,
    )

    call = calls[0]
    assert call["json"] is None
    assert call["data"] == {
        "name": "my-app",
        "port": "8080",
        "sleepAfterMinutes": "30",
        "volumeEnabled": "true",
        "volumeSizeGb": "5",
        "buildCpuCores": "1.0",
        "buildMemoryGb": "2",
    }
    filename, archive, content_type = call["files"]["context"]
    assert filename == "context.tar.gz"
    assert content_type == "application/gzip"

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = {member.name for member in tar.getmembers()}
    assert names == {"Dockerfile", "app.py"}


def test_context_without_a_dockerfile_is_refused(calls, cloudish, monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n")
    monkeypatch.setattr(cloudish, "check_read", lambda path: Path(path))

    result = cloudish.cloudish_deploy(
        api_key="k", name="my-app", context_dir=str(tmp_path)
    )

    assert "Dockerfile" in result["error"]
    assert calls == []


def test_folder_outside_the_allowed_list_is_returned_as_an_error(
    calls, cloudish, monkeypatch
):
    from gaia.skills.errors import SkillPermissionError

    def refuse(_path):
        raise SkillPermissionError("outside the agent's allowed folders")

    monkeypatch.setattr(cloudish, "check_read", refuse)

    result = cloudish.cloudish_deploy(
        api_key="k", name="my-app", context_dir="/elsewhere"
    )

    assert "allowed folders" in result["error"]
    assert calls == []


def test_exactly_one_source_is_required(calls, cloudish, context_dir):
    both = cloudish.cloudish_deploy(
        api_key="k", name="a", image="x", context_dir=str(context_dir)
    )
    neither = cloudish.cloudish_deploy(api_key="k", name="a")

    assert "exactly one" in both["error"]
    assert "exactly one" in neither["error"]
    assert calls == []


@pytest.mark.parametrize("port", [0, 70000])
def test_port_is_range_checked_before_the_request(calls, cloudish, port):
    result = cloudish.cloudish_deploy(
        api_key="k",
        name="a",
        image="ghcr.io/acme/app:1.0.0",
        port=port,
    )

    assert "port must be between 1 and 65535" in result["error"]
    assert calls == []


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("sleep_after_minutes", 1441, "sleep_after_minutes"),
        ("volume_size_gb", 2, "volume_size_gb"),
        ("build_cpu_cores", 4.0, "build_cpu_cores"),
        ("build_memory_gb", 3, "build_memory_gb"),
    ],
)
def test_runtime_choices_are_validated(calls, cloudish, field, value, expected):
    result = cloudish.cloudish_deploy(
        api_key="k",
        name="a",
        image="ghcr.io/acme/app:1.0.0",
        **{field: value},
    )

    assert expected in result["error"]
    assert calls == []


def test_build_status_reads_the_build_endpoint(calls, cloudish):
    cloudish.cloudish_build_status(api_key="k", build_id=123)

    call = calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://cloudish.ai/api/v1/images/builds/123"


def test_owner_and_name_keep_their_path_segments(calls, cloudish):
    cloudish.cloudish_logs(api_key="k", owner="my alias", name="app")

    assert calls[0]["url"] == "https://cloudish.ai/api/v1/docker/my%20alias/app/logs"


def test_exhausted_credits_are_reported_as_402(cloudish, monkeypatch):
    monkeypatch.setattr(
        cloudish.requests,
        "request",
        lambda *args, **kwargs: _Response({"error": "no credit"}, status_code=402),
    )

    result = cloudish.cloudish_status(api_key="k", owner="o", name="n")

    assert result["status"] == 402
    assert "credit" in result["error"]


def test_configure_requires_at_least_one_change(calls, cloudish):
    result = cloudish.cloudish_configure(api_key="k", owner="o", name="n")

    assert "Nothing to change" in result["error"]
    assert calls == []


def test_secrets_are_put_as_json(calls, cloudish):
    cloudish.cloudish_set_secrets(
        api_key="k", owner="o", name="n", secrets={"OPENAI_API_KEY": "sk-x"}
    )

    call = calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == "https://cloudish.ai/api/v1/projects/o/n/secrets"
    assert call["json"] == {"OPENAI_API_KEY": "sk-x"}


def test_the_skill_registers_its_seven_tools(tmp_path, monkeypatch):
    """The manifest and ``tools.py`` stay in step, through the real loader."""
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.skills.loader import register_skill_tools, unregister_skill_tools
    from gaia.skills.manager import SkillManager

    # An unlocked copy in a user root is held to `experimental`, which this
    # skill's network:write exceeds. The tier gate itself is covered by
    # tests/unit/test_skill_import_gate.py; here the contract under test is the
    # manifest-to-signature match.
    monkeypatch.setattr(
        "gaia.skills.loader.enforce_skill_tier_ceiling", lambda skill: None
    )

    manager = SkillManager(
        user_skills_root=SKILL_ROOT.parent,
        claude_skill_dirs=[tmp_path / "claude-skills"],
    )
    skill = manager.load("deploy-cloudish")
    before = dict(_TOOL_REGISTRY)
    try:
        registered = register_skill_tools(skill)

        assert set(registered) == {
            "deploy-cloudish/cloudish_create_key",
            "deploy-cloudish/cloudish_deploy",
            "deploy-cloudish/cloudish_build_status",
            "deploy-cloudish/cloudish_status",
            "deploy-cloudish/cloudish_logs",
            "deploy-cloudish/cloudish_configure",
            "deploy-cloudish/cloudish_set_secrets",
        }
        deploy = registered["deploy-cloudish/cloudish_deploy"]
        assert deploy["parameters"]["context_dir"]["required"] is False
        assert deploy["parameters"]["context_dir"]["type"] == "string"
        assert deploy["parameters"]["env"]["required"] is False
    finally:
        unregister_skill_tools("deploy-cloudish")
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)
