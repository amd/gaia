// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** What one chat searches at once: ``RAGConfig.max_indexed_files`` and ``max_total_chunks`` in ``gaia/rag/sdk.py``. */
export const SEARCHED_AT_ONCE = 100;
export const PASSAGES_AT_ONCE = 10000;

/**
 * Which limit stops the chat's index short of *docs*, walked in the order they load:
 * ``'size'`` for passages, ``'files'`` for the document count, null when all fit.
 * Mirrors ``RAGSDK.full_by``: a load past the passage limit pushes one out and stops there.
 */
export function indexLimitHit(docs: { chunk_count: number }[]): 'files' | 'size' | null {
    let held = 0;
    let passages = 0;
    for (const d of docs) {
        if (passages >= PASSAGES_AT_ONCE) return 'size';
        if (held >= SEARCHED_AT_ONCE) return 'files';
        passages += d.chunk_count;
        held += 1;
        if (passages > PASSAGES_AT_ONCE && held > 1) return 'size';
    }
    return null;
}
