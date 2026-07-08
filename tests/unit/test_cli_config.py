# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for the persistent CLI config and the default_model resolution (#98).

Covers:
  * GaiaConfig.default_model round-trip + generic get/set
  * resolve_model precedence: --model flag > config default_model > built-in
  * `gaia config show|get|set` CLI integration
  * The CLI injection that feeds config default_model into model-bearing commands
"""

import json
import sys

import pytest

# ── GaiaConfig.default_model + generic accessors ──────────────────────────


class TestDefaultModelConfig:
    def test_default_model_round_trip(self, tmp_path, monkeypatch):
        from gaia import config as config_mod
        from gaia.config import GaiaConfig

        config_file = tmp_path / "config.json"
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)

        cfg = GaiaConfig(default_model="Qwen3.5-35B-A3B-GGUF")
        cfg.save()

        data = json.loads(config_file.read_text())
        assert data["default_model"] == "Qwen3.5-35B-A3B-GGUF"

        loaded = GaiaConfig.load()
        assert loaded.default_model == "Qwen3.5-35B-A3B-GGUF"

    def test_unknown_keys_in_file_are_ignored(self, tmp_path, monkeypatch):
        from gaia import config as config_mod
        from gaia.config import GaiaConfig

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"profile": "npu", "bogus_key": 1}))
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)

        cfg = GaiaConfig.load()
        assert cfg.profile == "npu"
        assert not hasattr(cfg, "bogus_key")

    def test_get_set_generic(self):
        from gaia.config import GaiaConfig

        cfg = GaiaConfig()
        cfg.set("default_model", "Foo-GGUF")
        assert cfg.get("default_model") == "Foo-GGUF"
        assert "default_model" in cfg.field_names()

    def test_get_unknown_key_raises(self):
        from gaia.config import GaiaConfig, GaiaConfigError

        with pytest.raises(GaiaConfigError):
            GaiaConfig().get("nope")

    def test_set_unknown_key_raises(self):
        from gaia.config import GaiaConfig, GaiaConfigError

        with pytest.raises(GaiaConfigError):
            GaiaConfig().set("nope", "x")

    def test_load_non_object_raises(self, tmp_path, monkeypatch):
        from gaia import config as config_mod
        from gaia.config import GaiaConfig, GaiaConfigError

        config_file = tmp_path / "config.json"
        config_file.write_text("[1, 2, 3]")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)

        with pytest.raises(GaiaConfigError):
            GaiaConfig.load()

    def test_load_unreadable_raises(self, tmp_path, monkeypatch):
        # A path that exists but can't be read as a file (here: a directory)
        # is an OSError, which must surface as a loud GaiaConfigError.
        from gaia import config as config_mod
        from gaia.config import GaiaConfig, GaiaConfigError

        a_dir = tmp_path / "config.json"
        a_dir.mkdir()
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", a_dir)

        with pytest.raises(GaiaConfigError) as exc:
            GaiaConfig.load()
        assert str(a_dir) in str(exc.value)

    def test_empty_default_model_resolves_to_builtin(self):
        # An empty string is falsy and must not shadow the built-in default.
        from gaia.config import GaiaConfig

        cfg = GaiaConfig(default_model="")
        assert cfg.resolve_model(None, "builtin") == "builtin"

    def test_none_default_model_round_trips(self, tmp_path, monkeypatch):
        from gaia import config as config_mod
        from gaia.config import GaiaConfig

        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)
        GaiaConfig(profile="npu").save()  # default_model stays None
        assert GaiaConfig.load().default_model is None


# ── resolve_model precedence ──────────────────────────────────────────────


class TestResolveModel:
    def test_flag_wins(self):
        from gaia.config import GaiaConfig

        cfg = GaiaConfig(default_model="config-model")
        assert cfg.resolve_model("flag-model", "builtin") == "flag-model"

    def test_config_wins_over_builtin(self):
        from gaia.config import GaiaConfig

        cfg = GaiaConfig(default_model="config-model")
        assert cfg.resolve_model(None, "builtin") == "config-model"

    def test_builtin_when_nothing_set(self):
        from gaia.config import GaiaConfig

        cfg = GaiaConfig()
        assert cfg.resolve_model(None, "builtin") == "builtin"


# ── `gaia config` CLI integration ─────────────────────────────────────────


def _run_main(argv, monkeypatch, tmp_path):
    """Run gaia.cli.main() with config paths redirected to tmp_path."""
    from gaia import config as config_mod

    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["gaia"] + argv)

    from gaia.cli import main

    main()


class TestConfigCLI:
    def test_set_then_get(self, monkeypatch, tmp_path, capsys):
        _run_main(
            ["config", "set", "default_model", "Qwen3.5-35B-A3B-GGUF"],
            monkeypatch,
            tmp_path,
        )
        out = capsys.readouterr().out
        assert "default_model = Qwen3.5-35B-A3B-GGUF" in out

        # Persisted to disk
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["default_model"] == "Qwen3.5-35B-A3B-GGUF"

        _run_main(["config", "get", "default_model"], monkeypatch, tmp_path)
        out = capsys.readouterr().out
        assert "Qwen3.5-35B-A3B-GGUF" in out

    def test_show_includes_path_and_fields(self, monkeypatch, tmp_path, capsys):
        _run_main(["config", "show"], monkeypatch, tmp_path)
        out = capsys.readouterr().out
        assert str(tmp_path / "config.json") in out
        assert "default_model" in out
        assert "default_device" in out

    def test_set_unknown_key_exits_nonzero(self, monkeypatch, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            _run_main(["config", "set", "bogus", "x"], monkeypatch, tmp_path)
        assert exc.value.code != 0
        assert "Unknown config key" in capsys.readouterr().err

    def test_get_unknown_key_exits_nonzero(self, monkeypatch, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            _run_main(["config", "get", "bogus"], monkeypatch, tmp_path)
        assert exc.value.code != 0
        assert "Unknown config key" in capsys.readouterr().err

    def test_get_unset_value_prints_empty(self, monkeypatch, tmp_path, capsys):
        # default_model is unset → `get` prints an empty line, not "None".
        _run_main(["config", "get", "default_model"], monkeypatch, tmp_path)
        out = capsys.readouterr().out
        assert out.strip() == ""
        assert "None" not in out

    def test_no_subaction_exits_nonzero(self, monkeypatch, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            _run_main(["config"], monkeypatch, tmp_path)
        assert exc.value.code != 0
        assert "No config action" in capsys.readouterr().err

    def test_show_marks_unset_default_model(self, monkeypatch, tmp_path, capsys):
        _run_main(["config", "show"], monkeypatch, tmp_path)
        out = capsys.readouterr().out
        assert "default_model = (unset)" in out


# ── CLI default_model injection into model-bearing commands ───────────────


class TestModelInjection:
    def _capture_run_cli(self, monkeypatch):
        captured = {}

        def fake_run_cli(action, **kwargs):
            captured["action"] = action
            captured["kwargs"] = kwargs
            return None

        monkeypatch.setattr("gaia.cli.run_cli", fake_run_cli)
        return captured

    def test_prompt_uses_config_default_model(self, monkeypatch, tmp_path):
        from gaia import config as config_mod
        from gaia.config import GaiaConfig

        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)
        GaiaConfig(default_model="Configured-GGUF").save()

        captured = self._capture_run_cli(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["gaia", "prompt", "hello"])
        from gaia.cli import main

        main()
        assert captured["kwargs"].get("model") == "Configured-GGUF"

    def test_flag_overrides_config(self, monkeypatch, tmp_path):
        from gaia import config as config_mod
        from gaia.config import GaiaConfig

        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)
        GaiaConfig(default_model="Configured-GGUF").save()

        captured = self._capture_run_cli(monkeypatch)
        monkeypatch.setattr(
            sys, "argv", ["gaia", "prompt", "hello", "--model", "Flag-GGUF"]
        )
        from gaia.cli import main

        main()
        assert captured["kwargs"].get("model") == "Flag-GGUF"

    def test_no_config_leaves_model_unset(self, monkeypatch, tmp_path):
        # No config file → no injection; downstream uses its built-in default.
        from gaia import config as config_mod

        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "missing.json")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)

        captured = self._capture_run_cli(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["gaia", "prompt", "hello"])
        from gaia.cli import main

        main()
        # None is filtered out of kwargs, so the key is simply absent.
        assert "model" not in captured["kwargs"]

    def test_chat_uses_config_default_model(self, monkeypatch, tmp_path):
        from gaia import config as config_mod
        from gaia.config import GaiaConfig

        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)
        GaiaConfig(default_model="Configured-GGUF").save()

        captured = self._capture_run_cli(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["gaia", "chat", "--query", "hi"])
        from gaia.cli import main

        main()
        assert captured["kwargs"].get("model") == "Configured-GGUF"

    def test_chat_explicit_device_skips_config_default(self, monkeypatch, tmp_path):
        # An explicit `chat --device` selects a device-specific model and must
        # take precedence over the config default (model stays unset here).
        from gaia import config as config_mod
        from gaia.config import GaiaConfig

        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)
        GaiaConfig(default_model="Configured-GGUF").save()

        captured = self._capture_run_cli(monkeypatch)
        monkeypatch.setattr(
            sys, "argv", ["gaia", "chat", "--query", "hi", "--device", "npu"]
        )
        from gaia.cli import main

        main()
        assert "model" not in captured["kwargs"]

    def test_corrupt_config_fails_loudly_on_model_command(
        self, monkeypatch, tmp_path, capsys
    ):
        # A corrupt config must abort a model-bearing command loudly, not
        # silently fall through to the built-in default.
        from gaia import config as config_mod

        bad = tmp_path / "config.json"
        bad.write_text("not valid json{{{")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", bad)
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)

        self._capture_run_cli(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["gaia", "prompt", "hello"])
        from gaia.cli import main

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code != 0
        assert str(bad) in capsys.readouterr().err

    def test_llm_uses_config_default_model(self, monkeypatch, tmp_path):
        # `gaia llm` is dispatched separately from run_cli, so capture the
        # model at the llm-app boundary instead.
        from gaia import config as config_mod
        from gaia.config import GaiaConfig

        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)
        GaiaConfig(default_model="Configured-GGUF").save()

        monkeypatch.setattr(
            "gaia.cli.initialize_lemonade_for_agent", lambda **kw: (True, None)
        )
        captured = {}

        def fake_llm(**kwargs):
            captured.update(kwargs)
            return "ok"

        monkeypatch.setattr("gaia.apps.llm.app.main", fake_llm)
        monkeypatch.setattr(sys, "argv", ["gaia", "llm", "hello"])
        from gaia.cli import main

        main()
        assert captured.get("model") == "Configured-GGUF"


# ── Config-file location is overridable via environment variables ─────────


class TestConfigPathEnvOverride:
    def test_env_overrides_file_path(self, tmp_path, monkeypatch):
        import importlib

        target = tmp_path / "custom" / "myconfig.json"
        monkeypatch.setenv("GAIA_CONFIG_FILE", str(target))

        from gaia import config as config_mod

        config_mod = importlib.reload(config_mod)
        try:
            assert config_mod.GAIA_CONFIG_FILE == target
            config_mod.GaiaConfig(default_model="Env-GGUF").save()
            assert target.exists()
            assert config_mod.GaiaConfig.load().default_model == "Env-GGUF"
        finally:
            # Restore module-level constants for any later tests in the session.
            monkeypatch.delenv("GAIA_CONFIG_FILE", raising=False)
            importlib.reload(config_mod)

    def test_env_dir_override(self, tmp_path, monkeypatch):
        import importlib

        monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "cfgdir"))

        from gaia import config as config_mod

        config_mod = importlib.reload(config_mod)
        try:
            assert config_mod.GAIA_CONFIG_FILE == tmp_path / "cfgdir" / "config.json"
        finally:
            monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
            importlib.reload(config_mod)


# ── `--config PATH` flag points at an explicit config file ────────────────


class TestConfigFlag:
    def test_set_get_show_use_custom_path(self, monkeypatch, tmp_path, capsys):
        custom = tmp_path / "custom.json"

        _run_main(
            [
                "config",
                "set",
                "default_model",
                "Flag-Path-GGUF",
                "--config",
                str(custom),
            ],
            monkeypatch,
            tmp_path,
        )
        # Written to the --config path, NOT the default location.
        assert json.loads(custom.read_text())["default_model"] == "Flag-Path-GGUF"
        assert not (tmp_path / "config.json").exists()

        _run_main(
            ["config", "get", "default_model", "--config", str(custom)],
            monkeypatch,
            tmp_path,
        )
        assert "Flag-Path-GGUF" in capsys.readouterr().out

        _run_main(["config", "show", "--config", str(custom)], monkeypatch, tmp_path)
        assert str(custom) in capsys.readouterr().out

    def test_flag_injects_model_for_prompt(self, monkeypatch, tmp_path):
        from gaia import config as config_mod
        from gaia.config import GaiaConfig

        # Custom file has the model; the *default* location is empty, so the
        # injected model can only have come from --config.
        custom = tmp_path / "custom.json"
        GaiaConfig(default_model="Flag-Path-GGUF").save(custom)
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "default.json")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)

        captured = {}

        def fake_run_cli(action, **kwargs):
            captured["kwargs"] = kwargs

        monkeypatch.setattr("gaia.cli.run_cli", fake_run_cli)
        monkeypatch.setattr(
            sys, "argv", ["gaia", "prompt", "hi", "--config", str(custom)]
        )
        from gaia.cli import main

        main()
        assert captured["kwargs"].get("model") == "Flag-Path-GGUF"
        # --config is consumed for resolution, not forwarded as a runtime param.
        assert "config" not in captured["kwargs"]

    def test_flag_overrides_env_and_default(self, monkeypatch, tmp_path, capsys):
        # --config wins over GAIA_CONFIG_FILE for `gaia config` operations.
        from gaia import config as config_mod
        from gaia.config import GaiaConfig

        env_file = tmp_path / "env.json"
        GaiaConfig(default_model="Env-GGUF").save(env_file)
        custom = tmp_path / "flag.json"
        GaiaConfig(default_model="Flag-GGUF").save(custom)
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", env_file)

        monkeypatch.setattr(
            sys,
            "argv",
            ["gaia", "config", "get", "default_model", "--config", str(custom)],
        )
        from gaia.cli import main

        main()
        assert "Flag-GGUF" in capsys.readouterr().out
