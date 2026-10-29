# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Make this Strix Halo look like a 16 GB laptop with little memory free.

Put this directory on ``PYTHONPATH`` and set ``GAIA_HW_FREE_GB``. Only the OS
memory reading is replaced: GAIA still asks the real Lemonade for
``/system-info``, still reads the real amdgpu card, and still sends (or
refuses) the real load. The GPU's carve-out is reported full and system RAM is
cut to the budget, so the budget is all a load can take.
"""

import os

_budget = os.environ.get("GAIA_HW_FREE_GB")
if _budget:
    from dataclasses import replace

    from gaia.llm import model_fit

    _free = float(_budget)
    _real = model_fit.read_os_memory

    def _capped():
        real = _real()
        cards = tuple(
            replace(
                card,
                vram_used_gb=card.vram_total_gb,
                gtt_used_gb=max(0.0, card.gtt_total_gb - _free),
            )
            for card in real.gpus
        )
        return model_fit.OsMemory(
            ram_total_gb=16.0, ram_available_gb=_free, swap_free_gb=0.0, gpus=cards
        )

    model_fit.read_os_memory = _capped
    model_fit.ram_total_gb = lambda: 16.0
