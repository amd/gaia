# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA persistent configuration.

Written by ``gaia init``, read at runtime by LemonadeManager and Agent UI.
Stored at ``~/.gaia/config.json``.
"""

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

GAIA_CONFIG_DIR = Path.home() / ".gaia"
GAIA_CONFIG_FILE = GAIA_CONFIG_DIR / "config.json"


@dataclass
class GaiaConfig:
    """Persistent GAIA configuration.

    Attributes:
        profile: Last ``gaia init`` profile used (e.g. 'chat', 'npu').
        default_device: Default inference device ('cpu', 'gpu', 'npu').
            GPU is the default — it's the most broadly available accelerated
            path on AMD hardware.
    """

    profile: str = "chat"
    default_device: str = "gpu"

    @classmethod
    def load(cls) -> "GaiaConfig":
        """Load config from ~/.gaia/config.json, or return defaults."""
        try:
            data = json.loads(GAIA_CONFIG_FILE.read_text(encoding="utf-8"))
            return cls(
                profile=data.get("profile", "chat"),
                default_device=data.get("default_device", "gpu"),
            )
        except FileNotFoundError:
            return cls()
        except (json.JSONDecodeError, TypeError, OSError) as e:
            log.warning(f"Failed to load {GAIA_CONFIG_FILE}, using defaults: {e}")
            return cls()

    def save(self) -> None:
        """Write config to ~/.gaia/config.json."""
        GAIA_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        GAIA_CONFIG_FILE.write_text(
            json.dumps(asdict(self), indent=2) + "\n",
            encoding="utf-8",
        )
        log.info(f"Saved GAIA config to {GAIA_CONFIG_FILE}")
