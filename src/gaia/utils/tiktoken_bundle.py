# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""tiktoken's ``cl100k_base``, shipped inside a frozen GAIA binary (#5066).

Agents count tokens with ``cl100k_base`` (``gaia.agents.base.turn_metrics``).
tiktoken finds that encoding through a plugin module it discovers by scanning
the ``tiktoken_ext`` namespace package, which PyInstaller cannot see, and then
downloads the BPE file on first use. A frozen binary therefore names the plugin
as a hidden import and carries the file, so counts are exact offline too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ENCODING = "cl100k_base"

#: The ``tiktoken_ext`` plugin that defines :data:`ENCODING`.
PLUGIN_MODULE = "tiktoken_ext.openai_public"

#: Folder inside the bundle (``sys._MEIPASS``) holding tiktoken's cache.
BUNDLE_DIR = "tiktoken_cache"

_CACHE_ENV = ("TIKTOKEN_CACHE_DIR", "DATA_GYM_CACHE_DIR")


def use_bundled_cache() -> None:
    """In a frozen binary, have tiktoken read the bundled cache.

    A cache folder the user set wins. Outside a frozen binary this does nothing.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle is None or any(name in os.environ for name in _CACHE_ENV):
        return
    os.environ["TIKTOKEN_CACHE_DIR"] = os.path.join(bundle, BUNDLE_DIR)


def stage_cache(dest: Path) -> Path:
    """Recreate *dest* holding only :data:`ENCODING`'s cache file.

    Build time only: runs tiktoken in a child process with its cache pointed at
    *dest*, so the file lands under the name tiktoken looks it up by. The child
    downloads the file, so the build needs network access.
    """
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k not in _CACHE_ENV}
    env["TIKTOKEN_CACHE_DIR"] = str(dest)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import tiktoken; tiktoken.get_encoding({ENCODING!r})",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    staged = [p for p in dest.iterdir() if p.is_file()]
    if result.returncode != 0 or not staged:
        raise SystemExit(
            f"freeze: could not stage tiktoken's {ENCODING} data into {dest}. "
            "Without it the binary downloads the file on first use and counts "
            "tokens by estimate on a machine that cannot reach it. Install "
            "tiktoken in the freeze environment and allow access to "
            f"openaipublic.blob.core.windows.net, then re-run.\n{result.stderr}"
        )
    return dest
