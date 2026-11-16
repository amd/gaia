// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { DocumentListResponse } from '@/api/client.types';

type LibraryDoc = DocumentListResponse['documents'][number];

// Attached documents shown before the rest fold behind "+N more" (NewChat.tsx's DOCS_SHOWN).
export const DOCS_SHOWN = 5;

// What this chat will search, and the indexed library documents still on offer.
export function splitDocuments(documents: readonly LibraryDoc[], attachedIds: readonly string[]) {
    return {
        attached: documents.filter((d) => attachedIds.includes(d.id)),
        offered: documents.filter((d) => d.indexing_status === 'complete' && !attachedIds.includes(d.id)),
    };
}

export function foldDocuments<T>(attached: readonly T[], expanded: boolean): { shown: readonly T[]; hidden: number } {
    const hidden = Math.max(0, attached.length - DOCS_SHOWN);
    return { shown: expanded || hidden === 0 ? attached : attached.slice(0, DOCS_SHOWN), hidden };
}
