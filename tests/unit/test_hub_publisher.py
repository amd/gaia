# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.hub.publisher`` (dual-publish to R2 + PyPI).

No real network or PyPI: the R2 upload mocks ``requests.post`` and the PyPI
upload uses an injected ``twine_runner``. Token storage uses a fake in-memory
keyring so the OS credential store is never touched.
"""

import pytest

from gaia.hub import publisher
from gaia.hub.packager import PackResult

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeKeyring:
    """Minimal in-memory keyring backend."""

    def __init__(self):
        self.store = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


@pytest.fixture
def fake_keyring(monkeypatch):
    kr = _FakeKeyring()
    monkeypatch.setattr(publisher, "_keyring", lambda: kr)
    return kr


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv(publisher.HUB_TOKEN_ENV, raising=False)
    monkeypatch.delenv(publisher.PYPI_TOKEN_ENV, raising=False)


@pytest.fixture
def pack_result(tmp_path):
    wheel = tmp_path / "gaia_agent_demo-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"PK\x03\x04 wheel")
    return PackResult(
        wheel_path=wheel,
        sha256="deadbeef",
        size_bytes=8,
        agent_id="demo",
        version="0.1.0",
        dist_name="gaia-agent-demo",
    )


@pytest.fixture
def manifest_file(tmp_path):
    p = tmp_path / "gaia-agent.yaml"
    p.write_text("id: demo\nversion: 0.1.0\nauthor: AMD\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Token storage / resolution
# ---------------------------------------------------------------------------


def test_store_and_get_hub_token(fake_keyring, clean_env):
    publisher.store_token("hub", "secret-hub")
    assert publisher.get_hub_token() == "secret-hub"


def test_store_and_get_pypi_token(fake_keyring, clean_env):
    publisher.store_token("pypi", "pypi-abc")
    assert publisher.get_pypi_token() == "pypi-abc"


def test_env_overrides_keyring(fake_keyring, monkeypatch):
    publisher.store_token("hub", "from-keyring")
    monkeypatch.setenv(publisher.HUB_TOKEN_ENV, "from-env")
    assert publisher.get_hub_token() == "from-env"


def test_get_token_none_when_unset(fake_keyring, clean_env):
    assert publisher.get_hub_token() is None
    assert publisher.get_pypi_token() is None


def test_store_empty_token_raises(fake_keyring):
    with pytest.raises(publisher.PublisherError, match="empty"):
        publisher.store_token("hub", "  ")


def test_store_unknown_kind_raises(fake_keyring):
    with pytest.raises(publisher.PublisherError, match="unknown token kind"):
        publisher.store_token("bogus", "x")


# ---------------------------------------------------------------------------
# Dual publish — success
# ---------------------------------------------------------------------------


def test_publish_posts_to_r2_and_twine(
    fake_keyring, clean_env, monkeypatch, pack_result, manifest_file
):
    publisher.store_token("hub", "hub-tok")
    publisher.store_token("pypi", "pypi-tok")

    posted = {}

    def _fake_post(url, headers=None, files=None, timeout=None):
        posted["url"] = url
        posted["auth"] = headers["Authorization"]
        posted["files"] = set(files)
        return _FakeResponse(201, '{"published": {}}')

    import requests

    monkeypatch.setattr(requests, "post", _fake_post)

    twine_calls = {}

    def _fake_twine(cmd, env):
        twine_calls["cmd"] = cmd
        twine_calls["user"] = env["TWINE_USERNAME"]
        twine_calls["pass"] = env["TWINE_PASSWORD"]
        return 0, "Uploading… done"

    result = publisher.publish(
        pack_result,
        manifest_file,
        hub_url="https://hub.example",
        twine_runner=_fake_twine,
    )

    assert posted["url"] == "https://hub.example/publish"
    assert posted["auth"] == "Bearer hub-tok"
    assert posted["files"] == {"manifest", "artifact"}
    assert twine_calls["user"] == "__token__"
    assert twine_calls["pass"] == "pypi-tok"
    assert str(pack_result.wheel_path) in twine_calls["cmd"]

    assert not result.r2.skipped
    assert not result.pypi.skipped
    assert result.agent_id == "demo"


def test_publish_r2_includes_readme_when_present(
    fake_keyring, clean_env, monkeypatch, pack_result, manifest_file
):
    """A README.md next to the manifest ships as the 'readme' form field."""
    publisher.store_token("hub", "hub-tok")
    readme = manifest_file.parent / "README.md"
    readme.write_text("# Demo\n\nHello hub.\n", encoding="utf-8")

    posted = {}

    def _fake_post(url, headers=None, files=None, timeout=None):
        posted["files"] = dict(files)
        return _FakeResponse(201, "{}")

    import requests

    monkeypatch.setattr(requests, "post", _fake_post)

    publisher.publish(
        pack_result, manifest_file, hub_url="https://hub.example", skip_pypi=True
    )

    assert set(posted["files"]) == {"manifest", "artifact", "readme"}
    name, content, content_type = posted["files"]["readme"]
    assert name == "README.md"
    assert content == "# Demo\n\nHello hub.\n"
    assert content_type == "text/markdown"


def test_publish_r2_omits_readme_when_absent(
    fake_keyring, clean_env, monkeypatch, pack_result, manifest_file
):
    publisher.store_token("hub", "hub-tok")
    posted = {}

    def _fake_post(url, headers=None, files=None, timeout=None):
        posted["files"] = set(files)
        return _FakeResponse(201, "{}")

    import requests

    monkeypatch.setattr(requests, "post", _fake_post)

    publisher.publish(
        pack_result, manifest_file, hub_url="https://hub.example", skip_pypi=True
    )
    assert posted["files"] == {"manifest", "artifact"}


def test_publish_r2_includes_changelog_when_present(
    fake_keyring, clean_env, monkeypatch, pack_result, manifest_file
):
    """A CHANGELOG.md next to the manifest ships as the 'changelog' form field."""
    publisher.store_token("hub", "hub-tok")
    changelog = manifest_file.parent / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 0.1.0\n\n- First.\n", encoding="utf-8")

    posted = {}

    def _fake_post(url, headers=None, files=None, timeout=None):
        posted["files"] = dict(files)
        return _FakeResponse(201, "{}")

    import requests

    monkeypatch.setattr(requests, "post", _fake_post)

    publisher.publish(
        pack_result, manifest_file, hub_url="https://hub.example", skip_pypi=True
    )

    assert set(posted["files"]) == {"manifest", "artifact", "changelog"}
    name, content, content_type = posted["files"]["changelog"]
    assert name == "CHANGELOG.md"
    assert content == "# Changelog\n\n## 0.1.0\n\n- First.\n"
    assert content_type == "text/markdown"


def test_publish_skip_pypi(
    fake_keyring, clean_env, monkeypatch, pack_result, manifest_file
):
    publisher.store_token("hub", "hub-tok")
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(201, "{}"))

    result = publisher.publish(
        pack_result, manifest_file, hub_url="https://hub.example", skip_pypi=True
    )
    assert not result.r2.skipped
    assert result.pypi.skipped


def test_publish_skip_r2_uses_only_twine(
    fake_keyring, clean_env, pack_result, manifest_file
):
    publisher.store_token("pypi", "pypi-tok")
    result = publisher.publish(
        pack_result,
        manifest_file,
        skip_r2=True,
        twine_runner=lambda cmd, env: (0, "done"),
    )
    assert result.r2.skipped
    assert not result.pypi.skipped


def test_publish_both_skipped_raises(pack_result, manifest_file):
    with pytest.raises(publisher.PublisherError, match="nothing to publish"):
        publisher.publish(pack_result, manifest_file, skip_r2=True, skip_pypi=True)


# ---------------------------------------------------------------------------
# Missing tokens
# ---------------------------------------------------------------------------


def test_publish_r2_missing_token_raises(
    fake_keyring, clean_env, pack_result, manifest_file
):
    with pytest.raises(publisher.PublisherError, match="no Hub publish token"):
        publisher.publish(pack_result, manifest_file, skip_pypi=True)


def test_publish_pypi_missing_token_raises(
    fake_keyring, clean_env, pack_result, manifest_file
):
    with pytest.raises(publisher.PublisherError, match="no PyPI token"):
        publisher.publish(pack_result, manifest_file, skip_r2=True)


# ---------------------------------------------------------------------------
# Version immutability / auth rejections
# ---------------------------------------------------------------------------


def test_publish_r2_version_exists_raises(
    fake_keyring, clean_env, monkeypatch, pack_result, manifest_file
):
    publisher.store_token("hub", "hub-tok")
    import requests

    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: _FakeResponse(409, "version_exists"),
    )
    with pytest.raises(publisher.PublisherError, match="already exists"):
        publisher.publish(pack_result, manifest_file, skip_pypi=True)


def test_publish_r2_unauthorized_raises(
    fake_keyring, clean_env, monkeypatch, pack_result, manifest_file
):
    publisher.store_token("hub", "bad")
    import requests

    monkeypatch.setattr(
        requests, "post", lambda *a, **k: _FakeResponse(401, "unauthorized")
    )
    with pytest.raises(publisher.PublisherError, match="rejected the publish token"):
        publisher.publish(pack_result, manifest_file, skip_pypi=True)


def test_publish_r2_network_error_raises(
    fake_keyring, clean_env, monkeypatch, pack_result, manifest_file
):
    publisher.store_token("hub", "hub-tok")
    import requests

    def _boom(*a, **k):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(requests, "post", _boom)
    with pytest.raises(publisher.PublisherError, match="could not reach"):
        publisher.publish(pack_result, manifest_file, skip_pypi=True)


def test_publish_pypi_already_exists_raises(
    fake_keyring, clean_env, pack_result, manifest_file
):
    publisher.store_token("pypi", "pypi-tok")

    def _twine(cmd, env):
        return 1, "ERROR: File already exists for gaia-agent-demo"

    with pytest.raises(publisher.PublisherError, match="immutable"):
        publisher.publish(pack_result, manifest_file, skip_r2=True, twine_runner=_twine)


def test_publish_pypi_generic_failure_raises(
    fake_keyring, clean_env, pack_result, manifest_file
):
    publisher.store_token("pypi", "pypi-tok")

    def _twine(cmd, env):
        return 2, "something unexpected broke"

    with pytest.raises(publisher.PublisherError, match="twine upload failed"):
        publisher.publish(pack_result, manifest_file, skip_r2=True, twine_runner=_twine)


def test_publish_missing_wheel_raises(fake_keyring, clean_env, manifest_file, tmp_path):
    missing = PackResult(
        wheel_path=tmp_path / "gone.whl",
        sha256="x",
        size_bytes=0,
        agent_id="demo",
        version="0.1.0",
        dist_name="gaia-agent-demo",
    )
    with pytest.raises(publisher.PublisherError, match="wheel not found"):
        publisher.publish(missing, manifest_file)
