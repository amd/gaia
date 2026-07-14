# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``python -m gaia.daemon`` -> run the daemon process."""

from __future__ import annotations

from gaia.daemon.server import run

if __name__ == "__main__":
    run()
