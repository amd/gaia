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
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def gaia_home() -> Path:
    """Return the directory that holds GAIA's on-disk state.

    Precedence:

    1. ``GAIA_HOME`` relocates the whole tree.
    2. ``GAIA_CONFIG_DIR`` keeps the existing config-directory override.
    3. ``~/.gaia``.
    """
    for env_var in ("GAIA_HOME", "GAIA_CONFIG_DIR"):
        raw = os.environ.get(env_var)
        if raw is None:
            continue
        raw = raw.strip()
        if not raw:
            continue
        return Path(os.path.expandvars(os.path.expanduser(raw)))
    return default_gaia_home()


def default_gaia_home() -> Path:
    """``~/.gaia``: the home of a process with no ``GAIA_HOME``, and so of the
    machine's default daemon and its Lemonade Server."""
    return Path.home() / ".gaia"


def mcp_servers_path() -> Path:
    """Return GAIA's global MCP server list, ``<gaia_home>/mcp_servers.json``."""
    return gaia_home() / "mcp_servers.json"


def gaia_agents_dir() -> Path:
    """Return the folder installed agents live in, ``<gaia_home>/agents``."""
    return gaia_home() / "agents"


# Location is overridable for tests / non-standard installs. GAIA_CONFIG_FILE
# wins outright, then GAIA_CONFIG_DIR; otherwise config.json lives in
# ``gaia_home()``, so a run with its own GAIA_HOME never touches ~/.gaia/config.json.
GAIA_CONFIG_DIR = (
    Path(os.path.expanduser(os.environ["GAIA_CONFIG_DIR"].strip()))
    if os.environ.get("GAIA_CONFIG_DIR", "").strip()
    else gaia_home()
)
GAIA_CONFIG_FILE = Path(
    os.getenv("GAIA_CONFIG_FILE", str(GAIA_CONFIG_DIR / "config.json"))
)


class GaiaConfigError(Exception):
    """Raised when the persistent config exists but cannot be used.

    A *missing* config file is not an error (defaults are used); a *present
    but corrupt/unreadable* file is, so it surfaces loudly instead of being
    silently swallowed into defaults.
    """


class UnsafeGaiaHomeError(RuntimeError):
    """The resolved GAIA home is not a directory GAIA may own files in.

    Every caller that creates, serves or deletes files under the GAIA home
    raises this, so one ``except`` covers the uninstaller and the Agent UI
    alike.
    """


# Why a GAIA home is structurally unsafe, as returned by
# :func:`unsafe_gaia_home_reason`.
GAIA_HOME_IS_FS_ROOT = "fs_root"
GAIA_HOME_HOLDS_USER_HOME = "user_home"


def unsafe_gaia_home_reason(resolved: Path, user_home: Path) -> Optional[str]:
    """Return why owning files under ``resolved`` is unsafe, or None if it isn't.

    Shared so the uninstaller and the Agent UI cannot drift on the question,
    while each keeps its own wording and its own remedy. Both paths treat
    ``<GAIA_HOME>/documents`` as GAIA's to delete, and under ``GAIA_HOME=$HOME``
    that resolves onto the real ``Documents`` folder on Windows and APFS.

    Args:
        resolved: The GAIA home, already resolved to an absolute path.
        user_home: The user's home directory, already resolved.

    Returns:
        ``GAIA_HOME_IS_FS_ROOT``, ``GAIA_HOME_HOLDS_USER_HOME``, or None.
    """
    if resolved == resolved.parent:
        return GAIA_HOME_IS_FS_ROOT
    if resolved == user_home or user_home.is_relative_to(resolved):
        return GAIA_HOME_HOLDS_USER_HOME
    return None


@dataclass
class GaiaConfig:
    """Persistent GAIA configuration.

    Attributes:
        profile: Last ``gaia init`` profile used (e.g. 'chat', 'npu').
        default_device: Default inference device ('cpu', 'gpu', 'npu').
            GPU is the default — it's the most broadly available accelerated
            path on AMD hardware.
        default_model: Persistent default model ID for model-bearing commands
            (``gaia chat`` / ``gaia llm`` / ``gaia prompt``) and for new
            Agent UI sessions (``ChatDatabase.create_session``). ``None``
            means "fall back to each caller's built-in default". An
            explicit ``--model`` flag, or an explicit model picked in the
            UI, always wins over this value.
        full_access: Start every session with confirmation prompts off, so the
            agent runs gated tools without asking. Opt-in and OFF by default.

            This is the ONLY thing that turns full access on without someone
            asking for it on that launch, which is why it lives here and
            nowhere else: ``~/.gaia/config.json`` is the user's own file. A
            project-local ``.env`` or a checked-in config must never be able to
            switch off another person's confirmation prompts. The TUI still
            shows its banner on every frame while it is on.
        last_provider: Provider of the model last chosen in the TUI
            (``local``, ``fireworks``, ``amd``, ``claude``, ...). Saved with
            ``last_model`` after every switch the agent confirms.
        last_model: Model id last chosen in the TUI, restored on its next
            launch. A restore that fails is reported, never replaced with
            another model. ``gaia config set last_model ""`` forgets it.
        lemonade_mode: ``"gaia"`` runs GAIA's own private Lemonade;
            ``"external"`` uses the server at ``lemonade_base_url`` and never
            starts one. ``None`` means nobody has chosen yet (behaves as
            ``"gaia"``). See :mod:`gaia.llm.lemonade_choice`.
        lemonade_base_url: The user's Lemonade Server, for ``external`` mode.
        lemonade_api_key_stored: Its API key is in the OS credential store.
            The key itself is never written here.
        lemonade_idle_timeout_minutes: Stop GAIA's own Lemonade Server once no
            GAIA process has held it, and it has served no request, for this
            many minutes, so its memory goes back to the system. 0 keeps it
            running until ``gaia daemon stop``. Never applies to a server
            ``LEMONADE_BASE_URL`` names.
        llamacpp_backend: The llama.cpp backend (``vulkan``, ``rocm``, ``metal``
            or ``cpu``) GAIA asks Lemonade for when it loads a local chat
            model. ``None`` (set it to ``auto``) leaves the choice to Lemonade.
            Takes effect on the next load; the embedding model keeps its own.
        context_sizes: Context window, in tokens, the user chose per local
            model in the Agent UI (model id -> tokens). Every load of that
            model uses it; a model not listed gets GAIA's automatic size.
    """

    profile: str = "chat"
    default_device: str = "gpu"
    default_model: Optional[str] = None
    full_access: bool = False
    last_provider: Optional[str] = None
    last_model: Optional[str] = None
    lemonade_mode: Optional[str] = None
    lemonade_base_url: Optional[str] = None
    lemonade_api_key_stored: bool = False
    lemonade_idle_timeout_minutes: int = 15
    llamacpp_backend: Optional[str] = None
    context_sizes: Dict[str, int] = field(default_factory=dict)

    #: Strings accepted for a boolean field, and what each means. Anything
    #: else raises — "false" silently meaning True is the exact accident this
    #: table exists to prevent, and it would turn confirmation prompts OFF.
    _BOOL_WORDS = {
        "true": True,
        "yes": True,
        "on": True,
        "1": True,
        "false": False,
        "no": False,
        "off": False,
        "0": False,
    }

    @classmethod
    def _is_bool_field(cls, key: str) -> bool:
        """True when *key* is declared ``bool``."""
        declared = {f.name: f.type for f in fields(cls)}.get(key)
        return declared is bool or declared == "bool"

    @classmethod
    def _is_int_field(cls, key: str) -> bool:
        """True when *key* is declared ``int``."""
        declared = {f.name: f.type for f in fields(cls)}.get(key)
        return declared is int or declared == "int"

    @classmethod
    def _coerce(cls, key: str, value: Any) -> Any:
        """Convert a raw value to the type the field declares.

        The CLI hands every value through as a string, so a ``bool`` field
        given ``"false"`` would otherwise be stored truthy and read back as
        enabled.
        """
        if key == "lemonade_mode":
            return cls._coerce_lemonade_mode(value)
        if key == "lemonade_base_url":
            return cls._coerce_lemonade_url(value)
        if cls._is_int_field(key):
            return cls._whole_number(key, value)
        if not cls._is_bool_field(key) or isinstance(value, bool):
            return value
        word = str(value).strip().lower()
        if word not in cls._BOOL_WORDS:
            raise GaiaConfigError(
                f"Config key '{key}' is a true/false setting, but got {value!r}. "
                f"Use one of: {', '.join(sorted(cls._BOOL_WORDS))}."
            )
        return cls._BOOL_WORDS[word]

    @staticmethod
    def _coerce_lemonade_mode(value: Any) -> Optional[str]:
        from gaia.llm.lemonade_choice import MODES

        word = str(value or "").strip().lower()
        if not word:
            return None
        if word not in MODES:
            raise GaiaConfigError(
                f"lemonade_mode must be one of {', '.join(MODES)}, got {value!r}. "
                "'gaia' runs GAIA's own Lemonade; 'external' uses the one at "
                "lemonade_base_url."
            )
        return word

    @staticmethod
    def _coerce_lemonade_url(value: Any) -> Optional[str]:
        from gaia.llm.lemonade_choice import (
            LemonadeChoiceError,
            normalize_external_url,
        )

        if value is None or not str(value).strip():
            return None
        try:
            return normalize_external_url(str(value))
        except LemonadeChoiceError as e:
            raise GaiaConfigError(f"lemonade_base_url: {e}") from e

    @staticmethod
    def _whole_number(key: str, value: Any) -> int:
        """*value* as a non-negative int, or a GaiaConfigError naming *key*."""
        if isinstance(value, bool):
            number = None
        elif isinstance(value, int):
            number = value
        else:
            try:
                number = int(str(value).strip())
            except ValueError:
                number = None
        if number is None or number < 0:
            raise GaiaConfigError(
                f"Config key '{key}' takes a whole number of 0 or more, but got "
                f"{value!r}."
            )
        return number

    #: Values ``llamacpp_backend`` accepts; ``auto`` (or empty) is stored as None.
    LLAMACPP_BACKEND_CHOICES = ("auto", "vulkan", "rocm", "metal", "cpu")

    @classmethod
    def _check_llamacpp_backend(cls, value: Any, where: str) -> Optional[str]:
        """Normalise a ``llamacpp_backend`` value, raising on anything else."""
        if value is None:
            return None
        word = str(value).strip().lower()
        if word in ("", "auto"):
            return None
        if word not in cls.LLAMACPP_BACKEND_CHOICES:
            raise GaiaConfigError(
                f"{where}: 'llamacpp_backend' must be one of "
                f"{', '.join(cls.LLAMACPP_BACKEND_CHOICES)}, but it is {value!r}. "
                "Run `gaia config set llamacpp_backend auto` to let Lemonade choose."
            )
        return word

    @staticmethod
    def _check_context_sizes(value: Any, config_file: Path) -> None:
        if not isinstance(value, dict) or not all(
            isinstance(k, str)
            and isinstance(v, int)
            and not isinstance(v, bool)
            and v > 0
            for k, v in value.items()
        ):
            raise GaiaConfigError(
                f"GAIA config at {config_file}: 'context_sizes' must map model ids "
                f"to a positive number of tokens, but it is {value!r}. Fix it by "
                "hand, or delete the key to go back to automatic sizes."
            )

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
        except (OSError, UnicodeDecodeError) as e:
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
        for key, value in data.items():
            # Strict, as the TUI's reader is: a string is never a true/false
            # setting on disk, so "yes" cannot mean on here and off there.
            if key in known and cls._is_bool_field(key):
                if not isinstance(value, bool):
                    raise GaiaConfigError(
                        f"GAIA config at {config_file}: '{key}' must be true or "
                        f"false (unquoted), but it is {value!r}. Fix it by hand, "
                        f"or delete the key to go back to the default."
                    )
            if key in known and cls._is_int_field(key):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise GaiaConfigError(
                        f"GAIA config at {config_file}: '{key}' must be a whole "
                        f"number of 0 or more (unquoted), but it is {value!r}. "
                        "Fix it by hand, or delete the key to go back to the "
                        "default."
                    )
        mode = data.get("lemonade_mode")
        if mode is not None and mode not in ("gaia", "external"):
            raise GaiaConfigError(
                f"GAIA config at {config_file}: 'lemonade_mode' must be \"gaia\" "
                f'or "external", but it is {mode!r}. Fix or delete that key in '
                "the file, then retry."
            )
        if "context_sizes" in data:
            cls._check_context_sizes(data["context_sizes"], config_file)
        kwargs = {k: v for k, v in data.items() if k in known}
        if "llamacpp_backend" in kwargs:
            kwargs["llamacpp_backend"] = cls._check_llamacpp_backend(
                kwargs["llamacpp_backend"], f"GAIA config at {config_file}"
            )
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

    @classmethod
    def save_key(
        cls, key: str, value: Any, path: Optional[Path] = None
    ) -> "GaiaConfig":
        """Set one key in the config file, keeping every other key in it.

        Unlike :meth:`save`, keys this class does not declare (written by a
        newer GAIA or by the TUI) survive. Returns the reloaded config.
        """
        config = cls.load(path)
        config.set(key, value)
        config_file = cls.config_path(path)
        try:
            doc = json.loads(config_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            doc = {}
        doc[key] = getattr(config, key)
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        log.info(f"Saved {key} to GAIA config at {config_file}")
        return config

    def get(self, key: str) -> Any:
        """Return the value of a config field, raising on an unknown key."""
        if key not in self.field_names():
            raise GaiaConfigError(
                f"Unknown config key '{key}'. "
                f"Valid keys: {', '.join(self.field_names())}."
            )
        return getattr(self, key)

    def set(self, key: str, value: str) -> None:
        """Set a config field, raising on an unknown key or an unusable value."""
        if key not in self.field_names():
            raise GaiaConfigError(
                f"Unknown config key '{key}'. "
                f"Valid keys: {', '.join(self.field_names())}."
            )
        if key == "llamacpp_backend":
            setattr(self, key, self._check_llamacpp_backend(value, "gaia config"))
            return
        if key == "context_sizes":
            raise GaiaConfigError(
                "context_sizes is set per model: choose a context size in the "
                "Agent UI under Settings › Model, or edit config.json by hand."
            )
        setattr(self, key, self._coerce(key, value))

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
