# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Harness x model benchmarks for ``gaia eval tasks``.

``gaia.eval.flagship_tasks`` runs, scores, judges and gates the tasks. This
package holds what it needs to run them through more than one agent harness
under equal conditions: the model gateway, the ``gh`` stand-in, the TheRock
checkout, the sandbox fence, the leak scrubber, metering and the reports.
"""
