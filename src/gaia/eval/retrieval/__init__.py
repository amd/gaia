# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Retrieval-quality, answer-accuracy and scale benchmark for GAIA's RAG.

``gaia eval retrieval --component rag`` indexes real documents with the
production :class:`gaia.rag.sdk.RAGSDK`, scores retrieval against labelled
evidence locations (recall@k, MRR) separately from answer accuracy, measures
indexing and query cost as the corpus grows, and runs hard cases such as a
document replaced after indexing or a corrupted cache.

See ``docs/reference/eval.mdx`` (section "Retrieval benchmark").
"""
