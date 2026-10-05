# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Retrieval benchmarks on real code: ``gaia eval retrieval --component code``.

Quality is scored against fixes that really landed (SWE-bench Verified and
SWE-rebench gold patches) with real embeddings and no LLM; scale, incremental
re-indexing and robustness are measured on real repositories.
"""
