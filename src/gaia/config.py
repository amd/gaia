# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA persistent configuration.

Written by ``gaia init`` and ``gaia config set``, read at runtime by
LemonadeManager, the CLI model resolver, and the Agent UI.
Stored at ``~/.gaia/config.json``.
"""

import json
import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, List, Optional

log = logging.getLogger(__name__)

# Location is overridable for tests / non-standard installs. GAIA_CONFIG_FILE
# wins outright; otherwise GAIA_CONFIG_DIR sets the directory holding
# config.json; otherwise the default ~/.gaia.
GAIA_CONFIG_DIR = Path(os.getenv("GAIA_CONFIG_DIR", str(Path.home() / ".gaia")))
GAIA_CONFIG_FILE = Path(
    os.getenv("GAIA_CONFIG_FILE", str(GAIA_CONFIG_DIR / "config.json"))
)


class GaiaConfigError(Exception):
    """Raised when the persistent config exists but cannot be used.

    A *missing* config file is not an error (defaults are used); a *present
    but corrupt/unreadable* file is, so it surfaces loudly instead of being
    silently swallowed into defaults.
    """


@dataclass
class GaiaConfig:
    """Persistent GAIA configuration.

    Attributes:
        profile: Last ``gaia init`` profile used (e.g. 'chat', 'npu').
        default_device: Default inference device ('cpu', 'gpu', 'npu').
            GPU is the default — it's the most broadly available accelerated
            path on AMD hardware.
        default_model: Persistent default model ID for model-bearing commands
            (``gaia chat`` / ``gaia llm`` / ``gaia prompt``). ``None`` means
            "fall back to each command's built-in default". An explicit
            ``--model`` flag always wins over this value.
    """

    profile: str = "chat"
    default_device: str = "gpu"
    default_model: Optional[str] = None

    @classmethod
    def field_names(cls) -> List[str]:
        """Return the configurable field names (drives the CLI ``config`` cmd)."""
        return [f.name for f in fields(cls)]

    @staticmethod
    def config_path(path: Optional[Path] = None) -> Path:
        """Resolve the config file path.

        An explicit ``path`` (e.g. from ``--config``) wins; otherwise the
        module default (``GAIA_CONFIG_FILE``, itself env-overridable).
        """
        return Path(path) if path else GAIA_CONFIG_FILE

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "GaiaConfig":
        """Load config from the resolved config file.

        Returns defaults when the file does not exist (a fresh install is not
        an error). Raises :class:`GaiaConfigError` when the file exists but is
        unreadable or not valid JSON — a corrupt config must fail loudly with
        an actionable message, not silently degrade to defaults.
        """
        config_file = cls.config_path(path)
        try:
            text = config_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls()
        except OSError as e:
            raise GaiaConfigError(
                f"Cannot read GAIA config at {config_file}: {e}. "
                f"Check file permissions, or delete it to reset to defaults."
            ) from e

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise GaiaConfigError(
                f"GAIA config at {config_file} is not valid JSON: {e}. "
                f"Fix the file by hand, or delete it to reset to defaults "
                f"(then re-apply with `gaia config set ...`)."
            ) from e

        if not isinstance(data, dict):
            raise GaiaConfigError(
                f"GAIA config at {config_file} must be a JSON object, "
                f"got {type(data).__name__}. Delete it to reset to defaults."
            )

        known = set(cls.field_names())
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(**kwargs)

    def save(self, path: Optional[Path] = None) -> None:
        """Write config to the resolved config file."""
        config_file = self.config_path(path)
        # Create the file's own parent — the path can be overridden via the
        # --config flag or GAIA_CONFIG_FILE, independent of GAIA_CONFIG_DIR.
        config_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {f.name: getattr(self, f.name) for f in fields(self)}
        config_file.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        log.info(f"Saved GAIA config to {config_file}")

    def get(self, key: str) -> Any:
        """Return the value of a config field, raising on an unknown key."""
        if key not in self.field_names():
            raise GaiaConfigError(
                f"Unknown config key '{key}'. "
                f"Valid keys: {', '.join(self.field_names())}."
            )
        return getattr(self, key)

    def set(self, key: str, value: str) -> None:
        """Set a config field, raising on an unknown key."""
        if key not in self.field_names():
            raise GaiaConfigError(
                f"Unknown config key '{key}'. "
                f"Valid keys: {', '.join(self.field_names())}."
            )
        setattr(self, key, value)

    def resolve_model(
        self, cli_value: Optional[str], builtin_default: Optional[str]
    ) -> Optional[str]:
        """Resolve the effective model with documented precedence.

        Highest wins: explicit ``--model`` flag > config ``default_model`` >
        the command's built-in default.
        """
        if cli_value:
            return cli_value
        if self.default_model:
            return self.default_model
        return builtin_default
