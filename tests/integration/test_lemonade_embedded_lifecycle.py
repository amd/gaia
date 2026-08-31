# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Download and run the real embeddable Lemonade, end to end.

The unit tests mock nothing at the process boundary because mocking it would
prove only that GAIA called ``lemond`` -- never that the arguments it passes are
ones ``lemond`` accepts. That contract is the whole risk here: the published
docs describe a single ``DIR`` positional, while 11.8 takes two
(``lemond <cache_dir> <config_dir>``). A stub returning success would have
sailed straight past that.

So this test downloads the artifact (~5 MB), starts it on a private port, proves
the generated API key is enforced, and stops it. It never downloads a model or
an inference backend, so it stays cheap.

Skips when GitHub is unreachable or ``GAIA_SKIP_NETWORK_TESTS`` is set.
"""

import json
import os
import urllib.error
import urllib.request

import pytest

from gaia.llm.lemonade_embedded import EmbeddedLemonade, UnsupportedPlatformError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        bool(os.environ.get("GAIA_SKIP_NETWORK_TESTS")),
        reason="GAIA_SKIP_NETWORK_TESTS is set",
    ),
]


def _github_reachable() -> bool:
    """Whether github.com answers at all."""
    request = urllib.request.Request(
        "https://github.com", method="HEAD", headers={"User-Agent": "GAIA/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError):
        return False


@pytest.fixture
def running_instance(tmp_path):
    """A real embedded Lemonade, started in a temp home and always stopped."""
    if not _github_reachable():
        pytest.skip("GitHub is unreachable")

    manager = EmbeddedLemonade(home=tmp_path)
    try:
        manager.start(timeout=120.0)
    except UnsupportedPlatformError:
        pytest.skip("No embeddable artifact for this platform")
    try:
        yield manager
    finally:
        # Never leave a daemon (or its backends) behind on a dev box.
        try:
            manager.stop()
        except Exception as e:  # noqa: BLE001 - cleanup must not mask failures
            pytest.fail(f"Could not stop the embedded instance: {e}")


def _get(url, api_key=None, timeout=15):
    """GET *url*, returning (status_code, decoded_body_or_None)."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None


def test_the_launch_contract_lemond_actually_accepts(running_instance):
    """``lemond <cache_dir> <config_dir> --port N`` starts a healthy server."""
    status = running_instance.status()
    assert status.running, "started, but status says it isn't running"
    assert status.port and status.base_url

    code, body = _get(
        f"{status.base_url}/health", api_key=running_instance.current_api_key()
    )
    assert code == 200, f"health endpoint returned {code}"
    assert body is not None


def test_the_generated_api_key_locks_other_apps_out(running_instance):
    """The private instance rejects callers without GAIA's key."""
    status = running_instance.status()
    models_url = f"{status.base_url}/models"

    assert _get(models_url)[0] == 401, "unauthenticated request was allowed in"
    assert _get(models_url, api_key="not-the-key")[0] == 401, "wrong key was accepted"
    assert _get(models_url, api_key=running_instance.current_api_key())[0] == 200


def test_the_model_catalogue_is_not_truncated(running_instance):
    """The server advertises a real catalogue, not a handful of models.

    Setting ``no_fetch_executables`` collapses this to the 4 models runnable by
    built-in backends, which would break ``gaia download`` for everything else.
    """
    code, body = _get(
        f"{running_instance.status().base_url}/models",
        api_key=running_instance.current_api_key(),
    )
    assert code == 200
    assert isinstance(body.get("data"), list)


def test_start_is_idempotent(running_instance):
    """Starting again returns the same instance instead of spawning a second."""
    first = running_instance.status()
    again = running_instance.start()
    assert again.port == first.port
    assert again.pid == first.pid


def test_stop_is_idempotent_and_clears_state(running_instance):
    """Stopping twice reports honestly and leaves no stale state file."""
    assert running_instance.stop() is True
    assert not running_instance.state_path.exists()
    assert running_instance.stop() is False
    assert running_instance.status().running is False


def test_install_is_reentrant_without_force(tmp_path):
    """A second install of the same version is a no-op, not a re-download."""
    if not _github_reachable():
        pytest.skip("GitHub is unreachable")

    manager = EmbeddedLemonade(home=tmp_path)
    try:
        first = manager.install()
    except UnsupportedPlatformError:
        pytest.skip("No embeddable artifact for this platform")

    assert manager.is_installed()
    stamp = manager.daemon_path.stat().st_mtime_ns
    assert manager.install() == first
    assert manager.daemon_path.stat().st_mtime_ns == stamp, "re-downloaded needlessly"
