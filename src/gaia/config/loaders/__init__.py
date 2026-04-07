# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Configuration Loaders Module.

Provides configuration file loaders with support for:
- JSON configuration files
- YAML configuration files
- Environment variables
- Hot-reload with file watching

Example:
    from gaia.config.loaders import (
        JSONLoader,
        YAMLLoader,
        EnvLoader,
        FileWatcherLoader,
    )

    # Load JSON config
    json_config = JSONLoader("./config.json").load()

    # Load YAML config
    yaml_config = YAMLLoader("./config.yaml").load()

    # Load environment variables
    env_config = EnvLoader(prefix="APP_").load()

    # Hot-reload config
    with FileWatcherLoader("./config.json") as loader:
        config = loader.get_config()
"""

from gaia.config.loaders.json_loader import JSONLoader, load_json, save_json

from gaia.config.loaders.yaml_loader import (
    YAMLLoader,
    load_yaml,
    save_yaml,
    YAML_AVAILABLE,
)

from gaia.config.loaders.env_loader import (
    EnvLoader,
    load_env,
    get_env,
)

from gaia.config.loaders.file_watcher_loader import (
    FileWatcherLoader,
    ConfigHotReload,
)

__all__ = [
    # JSON loader
    "JSONLoader",
    "load_json",
    "save_json",
    # YAML loader
    "YAMLLoader",
    "load_yaml",
    "save_yaml",
    "YAML_AVAILABLE",
    # Environment loader
    "EnvLoader",
    "load_env",
    "get_env",
    # Hot-reload loader
    "FileWatcherLoader",
    "ConfigHotReload",
]

__version__ = "1.0.0"
