# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia init` must not report success when model verification finds broken models.

Model verification used to print remove-and-redownload instructions for a model
that failed its inference test and then return success, so `gaia init` exited 0
with a model that could not answer. The softer cases — Lemonade not reporting
the context size it loaded, and Windows Installer's process check failing —
must stay non-fatal but be visible.
"""

import io
import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from gaia.installer.init_command import InitCommand, MemoryShortfall
from gaia.installer.lemonade_installer import LemonadeInstaller
from gaia.ui.build import WebuiBuildResult, WebuiBuildStatus


@pytest.fixture(autouse=True)
def _isolate_remote_env(monkeypatch):
    """Strip LEMONADE_BASE_URL so InitCommand doesn't auto-flip to remote mode."""
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)


def _healthy_client():
    client = MagicMock()
    client.health_check.return_value = {"status": "ok"}
    client.check_model_available.return_value = True
    return client


def _run_init(inference, config_cls=None):
    """Run `gaia init --profile minimal --yes` with every step but verification stubbed."""
    cmd = InitCommand(profile="minimal", yes=True)
    config_cls = config_cls or MagicMock()
    with (
        patch.object(cmd, "_ensure_lemonade_ready", return_value=True),
        patch.object(cmd, "_download_models", return_value=True),
        patch.object(cmd, "_test_model_inference", side_effect=inference),
        patch(
            "gaia.ui.build.ensure_webui_built",
            return_value=WebuiBuildResult(status=WebuiBuildStatus.SKIPPED),
        ),
        patch("gaia.config.GaiaConfig", config_cls),
        patch(
            "gaia.llm.lemonade_client.LemonadeClient", return_value=_healthy_client()
        ),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ),
        patch("sys.stdout", new_callable=io.StringIO) as stdout,
    ):
        rc = cmd.run()
    return rc, stdout.getvalue()


def test_failed_model_makes_init_exit_nonzero_with_fix_steps():
    rc, out = _run_init(lambda _client, _model: (False, "llama-server crashed"))

    assert rc == 1
    assert "llama-server crashed" in out
    assert "gaia uninstall --models --yes" in out
    assert "gaia init --profile minimal --yes" in out
    assert "Model verification failed for:" in out
    assert "GAIA initialization complete!" not in out


def test_failed_verification_still_saves_the_profile():
    """A transient verification failure must not leave the user on the default profile."""
    config_cls = MagicMock()
    saved = MagicMock(default_model=None)
    config_cls.update.side_effect = lambda mutate, **_kw: mutate(saved)
    rc, out = _run_init(lambda _client, _model: (False, "timed out"), config_cls)

    assert rc == 1
    config_cls.update.assert_called_once()
    assert saved.profile == "minimal"
    assert "re-run `gaia init` first" in out


def test_all_models_passing_keeps_init_successful():
    rc, out = _run_init(lambda _client, _model: (True, None))

    assert rc == 0
    assert "Model verification failed" not in out


def _verify(profile, inference):
    cmd = InitCommand(profile=profile, yes=True)
    cmd.console = MagicMock()

    def _fake(client, model_id):
        return inference(cmd, model_id)

    with (
        patch(
            "gaia.llm.lemonade_client.LemonadeClient", return_value=_healthy_client()
        ),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ),
        patch.object(cmd, "_test_model_inference", side_effect=_fake),
    ):
        result = cmd._verify_setup()
    printed = "\n".join(
        str(c.args[0]) for c in cmd.console.print.call_args_list if c.args
    )
    return result, printed


def test_unverified_context_passes_with_actionable_warning():
    def llm_no_ctx(cmd, _model_id):
        cmd._ctx_verified = None
        return (True, None)

    result, printed = _verify("chat", llm_no_ctx)

    assert result is True
    assert "did not report a context size" in printed
    assert "re-run `gaia init --profile chat --yes`" in printed


def test_unreported_ctx_size_is_logged(caplog):
    cmd = InitCommand(profile="chat", yes=True)
    client = MagicMock()
    client.check_model_loaded.return_value = False
    client.list_models.return_value = {
        "data": [{"id": "Gemma-4-E4B-it-GGUF", "recipe_options": {}}]
    }
    client.chat_completions.return_value = {"choices": [{"message": {"content": "ok"}}]}

    with caplog.at_level(logging.WARNING, logger="gaia.installer.init_command"):
        ok, error = cmd._test_model_inference(client, "Gemma-4-E4B-it-GGUF")

    assert (ok, error) == (True, None)
    assert cmd._ctx_verified is None
    assert any(
        "did not report ctx_size for Gemma-4-E4B-it-GGUF" in r.getMessage()
        for r in caplog.records
    )


def test_msi_check_failure_is_announced_not_silent(caplog, capsys):
    installer = LemonadeInstaller()
    installer.system = "windows"

    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tasklist", timeout=5),
        ),
        caplog.at_level(logging.WARNING, logger="gaia.installer.lemonade_installer"),
    ):
        assert installer.wait_for_msi_mutex(timeout=5) is True

    assert "Could not check for other Windows Installer operations" in (
        capsys.readouterr().out
    )
    assert any("msiexec" in r.getMessage() for r in caplog.records)


_SHORT = MemoryShortfall(
    "Not enough free memory to load Gemma-4-E4B-it-GGUF with a 32,768-token "
    "window: it needs about 7.8 GB and only 6.1 GB is free."
)


def test_a_model_short_of_memory_is_not_called_corrupted():
    """rc10 on a 16 GB Mac: told to delete every model for a memory shortfall."""
    rc, out = _run_init(lambda _client, _model: (False, _SHORT))
    out = " ".join(out.split())  # Rich wraps the console at its own width.

    assert rc == 1
    assert "not have enough free memory" in out
    assert "nothing needs re-downloading" in out
    assert "may be corrupted" not in out
    assert "gaia uninstall --models" not in out


def test_a_broken_model_beside_a_short_one_still_gets_the_re_download_steps():
    def inference(_client, model_id):
        if "embed" in model_id:
            return (False, "Empty embedding")
        return (False, _SHORT)

    cmd = InitCommand(profile="chat", yes=True)
    cmd.console = MagicMock()
    with (
        patch(
            "gaia.llm.lemonade_client.LemonadeClient", return_value=_healthy_client()
        ),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ),
        patch.object(cmd, "_test_model_inference", side_effect=inference),
    ):
        assert cmd._verify_setup() is False
    printed = "\n".join(
        str(c.args[0]) for c in cmd.console.print.call_args_list if c.args
    )
    assert "not have enough free memory" in printed
    assert "may be corrupted" in printed
    assert "embeddinggemma" in printed.split("may be corrupted")[1]


def test_a_load_refused_for_memory_comes_back_whole_and_marked():
    from gaia.llm.lemonade_client import InsufficientMemoryError

    message = "Not enough free memory to load X: " + "detail " * 30
    client = MagicMock()
    client.load_model.side_effect = InsufficientMemoryError(message)
    cmd = InitCommand(profile="minimal", yes=True)

    ok, error = cmd._test_model_inference(client, "Gemma-4-E4B-it-GGUF")

    assert ok is False
    assert isinstance(error, MemoryShortfall)
    assert error == message


def test_the_cache_folder_is_found_whatever_its_case(tmp_path):
    """The hub stores Gemma as models--unsloth--gemma-4-E4B-it-GGUF."""
    (tmp_path / "models--unsloth--gemma-4-E4B-it-GGUF").mkdir()

    with patch("gaia.installer.init_command._hf_hub_cache", return_value=str(tmp_path)):
        _result, printed = _verify(
            "chat", lambda _cmd, _model: (False, "llama-server crashed")
        )

    assert "models--unsloth--gemma-4-E4B-it-GGUF" in printed
