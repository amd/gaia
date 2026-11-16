// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Directory, File, Paths } from 'expo-file-system';

import { GaiaApiError, GaiaError } from '@/api/errors';
import type { GaiaClient } from '@/api/client.types';

import { shareFile } from './shareFile';

const EXPORT_FOLDER = 'chat-exports';

/** The desktop's rule for the exported file's name: path and reserved characters become `_`, at most 100 characters. */
export function exportFileName(title: string | undefined): string {
    return `${(title || 'chat').replace(/[/\\:*?"<>|]/g, '_').slice(0, 100)}.md`;
}

/** Fetches the chat as Markdown from the PC and opens the share sheet for `<title>.md`. */
export async function exportChat(client: GaiaClient, sessionId: string, title: string | undefined): Promise<void> {
    let content: string;
    try {
        ({ content } = await client.exportSessionMarkdown(sessionId));
    } catch (error) {
        throw error instanceof GaiaApiError
            ? new GaiaError('mobileApp.share.exportHttp', { status: error.status }, { cause: error })
            : new GaiaError('mobileApp.share.exportUnreachable', {}, { cause: error });
    }
    const folder = new Directory(Paths.cache, EXPORT_FOLDER);
    folder.create({ idempotent: true, intermediates: true });
    const file = new File(folder, exportFileName(title));
    file.create({ overwrite: true });
    file.write(content);
    await shareFile(file.uri, { mimeType: 'text/markdown' });
}
