// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { countText } from '@webui/utils/documentLibrary.core';
import { formatSize } from '@webui/utils/format';

import type { DocumentListResponse } from '@/api/client.types';
import type { Translate } from '@/i18n/useT';

type LibraryDoc = DocumentListResponse['documents'][number];

export interface LibraryStats {
    readable: string;
    unreadable: string | null;
    size: string;
}

// The count, the unreadable count and the total size; null when the library is empty.
export function libraryStats(documents: readonly LibraryDoc[], t: Translate): LibraryStats | null {
    if (documents.length === 0) return null;
    const unreadable = documents.filter((d) => d.indexing_status === 'failed').length;
    return {
        readable: countText(t, 'docs.count', documents.length - unreadable),
        unreadable: unreadable > 0 ? countText(t, 'docs.unreadable', unreadable) : null,
        size: formatSize(documents.reduce((sum, d) => sum + d.file_size, 0)),
    };
}
