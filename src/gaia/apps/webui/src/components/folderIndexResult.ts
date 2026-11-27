// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey, Say } from '../i18n/registry';
import type { SkippedFile } from '../types';

/** The skipped-file detail returned by folder indexing. */
export interface FolderSkippedFiles {
    skipped_files?: SkippedFile[];
    skipped_files_truncated?: boolean;
}

/** Match the server's cap when several folders' results land in one status line. */
const MAX_SKIPPED_NAMES = 50;

/** Group the named skipped files by reason, for display after the existing counts. */
export function describeSkippedFiles(say: Say, result: FolderSkippedFiles): string[] {
    const files = result.skipped_files ?? [];
    if (files.length === 0) return [];

    const shown = files.slice(0, MAX_SKIPPED_NAMES);
    const parts: string[] = [];
    const addGroup = (reason: string, key: MessageKey) => {
        const names = shown.filter((entry) => entry.reason === reason).map((entry) => entry.file);
        if (names.length > 0) parts.push(say(key, { names: names.join(', ') }));
    };
    addGroup('unsupported_type', 'docs.folder.skippedFiles.unsupported');
    addGroup('credential_file', 'docs.folder.skippedFiles.credential');
    if (result.skipped_files_truncated || files.length > MAX_SKIPPED_NAMES) {
        parts.push(say('docs.folder.skippedFiles.truncated'));
    }
    return parts;
}
