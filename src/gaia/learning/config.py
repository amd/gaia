# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Learning settings, stored in ``<gaia_home>/learning/config.json``.

Learning is off until the user turns it on. Every field is validated on load
and on save; a bad value is an error that names the field, never a silent
default.
"""

import json
import re
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Optional

from gaia.learning.errors import LearningError
from gaia.learning.paths import config_path
from gaia.utils.file_lock import file_lock, write_text_atomic

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
BACKENDS = ("peft", "unsloth")


@dataclass
class LearningConfig:
    enabled: bool = False
    # Local time window the nightly cycle may start in. May wrap midnight.
    window_start: str = "01:00"
    window_end: str = "06:00"
    # Minutes with no chat activity before a cycle may start.
    min_idle_minutes: int = 30
    # Laptops: only train on mains power.
    require_ac_power: bool = True
    # The chat model to personalise. None means GAIA's default chat model.
    base_model: Optional[str] = None
    # How far back the first cycle reads history; later cycles read since the
    # last successful one.
    lookback_days: int = 14
    max_new_examples: int = 300
    backend: str = "peft"
    load_in_4bit: bool = True
    rank: int = 16
    alpha: int = 32
    learning_rate: float = 2e-4
    epochs: int = 5
    max_steps: int = 600
    max_seq_len: int = 2048
    # Adapters kept on disk besides the active one.
    keep_adapters: int = 7

    def validate(self) -> None:
        for name in ("window_start", "window_end"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _HHMM.match(value):
                raise LearningError(
                    f"learning config: {name}={value!r} is not a 24-hour HH:MM "
                    "time. Fix it with `gaia learn config --window 01:00-06:00`."
                )
        if self.backend not in BACKENDS:
            raise LearningError(
                f"learning config: backend={self.backend!r}; expected one of "
                f"{list(BACKENDS)}."
            )
        positive = (
            "min_idle_minutes",
            "lookback_days",
            "max_new_examples",
            "rank",
            "alpha",
            "epochs",
            "max_steps",
            "max_seq_len",
        )
        for name in positive:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise LearningError(
                    f"learning config: {name}={value!r} must be a positive integer."
                )
        if not isinstance(self.keep_adapters, int) or self.keep_adapters < 0:
            raise LearningError(
                f"learning config: keep_adapters={self.keep_adapters!r} must be "
                "zero or more."
            )
        if not 0 < float(self.learning_rate) < 1:
            raise LearningError(
                f"learning config: learning_rate={self.learning_rate!r} must be "
                "between 0 and 1."
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LearningConfig":
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise LearningError(
                f"learning config has unknown keys {unknown}; known keys are "
                f"{sorted(known)}. Edit or delete {config_path()}."
            )
        cfg = cls(**data)
        cfg.validate()
        return cfg


def load_config() -> LearningConfig:
    path = config_path()
    if not path.exists():
        return LearningConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise LearningError(
            f"Could not read the learning config at {path}: {e}. Fix or delete "
            "the file; `gaia learn config` rewrites it."
        ) from e
    if not isinstance(data, dict):
        raise LearningError(f"The learning config at {path} is not a JSON object.")
    return LearningConfig.from_dict(data)


def save_config(cfg: LearningConfig) -> None:
    cfg.validate()
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        write_text_atomic(path, json.dumps(cfg.to_dict(), indent=2))


def update_config(**changes: Any) -> LearningConfig:
    with file_lock(config_path()):
        data = load_config().to_dict()
        data.update(changes)
        cfg = LearningConfig.from_dict(data)
        save_config(cfg)
    return cfg
