// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isolate, type MessageKey, type Say } from '../i18n/registry';
import type { SkippedFile, SkippedFileReason } from '../types';

/** The skipped-file detail returned by folder indexing. */
export interface FolderSkippedFiles {
    skipped_files?: SkippedFile[];
    skipped_files_truncated?: boolean;
}

/** Match the server's cap when several folders' results land in one status line. */
export const MAX_SKIPPED_NAMES = 50;

/** Every reason the server can return, so adding one requires a translated label. */
const REASON_LABELS: Record<SkippedFileReason, MessageKey> = {
    unsupported_type: 'docs.folder.skippedFiles.unsupported',
    credential_file: 'docs.folder.skippedFiles.credential',
};

/** Group the named skipped files by reason, for display after the existing counts. */
export function describeSkippedFiles(say: Say, result: FolderSkippedFiles): string[] {
    const files = result.skipped_files ?? [];
    if (files.length === 0) return [];

    const shown = files.slice(0, MAX_SKIPPED_NAMES);
    const parts: string[] = [];
    for (const reason of Object.keys(REASON_LABELS) as SkippedFileReason[]) {
        const names = shown
            .filter((entry) => entry.reason === reason)
            .map((entry) => isolate(entry.file, 'ltr'));
        if (names.length > 0) {
            parts.push(say(REASON_LABELS[reason], { names: names.join(', ') }));
        }
    }
    if (result.skipped_files_truncated || files.length > MAX_SKIPPED_NAMES) {
        parts.push(say('docs.folder.skippedFiles.truncated', { n: MAX_SKIPPED_NAMES }));
    }
    return parts;
}
