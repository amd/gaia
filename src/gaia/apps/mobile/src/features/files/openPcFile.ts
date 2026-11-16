// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ENDPOINTS } from '@/api/constants';
import { isPcUrl } from '@/platform/fetch';
import { checkLink } from '@/platform/linkPolicy';
import { openLinkFromPress } from '@/platform/openLink';

import { usePreviewStore, type PreviewTarget } from './previewStore';

export type OpenResult = 'preview' | 'browser' | 'ignored';

export interface CitedDocument {
    docId: string;
    name: string;
    page?: number;
    /** The file on the PC; a paired device reads it through file preview, not the library route. */
    path?: string;
}

const WINDOWS_PATH = /^[A-Za-z]:[\\/]/;
const FILE_URL = /^file:\/\//i;
const DOCUMENT_FILE = new RegExp(`^${ENDPOINTS.documentFile.replace('{doc_id}', '([^/]+)')}$`);

/** A `file://` URL or an absolute path, which names a file on the paired PC. */
export function isFileLink(url: string): boolean {
    return FILE_URL.test(url) || WINDOWS_PATH.test(url) || (url.startsWith('/') && !url.startsWith('//'));
}

function pathOfFileLink(link: string): string {
    if (!FILE_URL.test(link)) return link;
    const path = decodeURIComponent(link.replace(FILE_URL, '').split(/[?#]/)[0]!);
    return /^\/[A-Za-z]:[\\/]/.test(path) ? path.slice(1) : path;
}

// A PC URL names the file the same way the PC's own routes do: a library document, or a path read for a preview or an image.
function targetOfPcUrl(url: string, sessionId: string): PreviewTarget | null {
    let parsed: URL;
    try {
        parsed = new URL(url, 'http://pc.invalid');
    } catch {
        return null;
    }
    const doc = DOCUMENT_FILE.exec(parsed.pathname);
    if (doc) {
        const page = Number(/^#page=(\d+)$/.exec(parsed.hash)?.[1]);
        return { kind: 'document', docId: decodeURIComponent(doc[1]!), name: '', sessionId, ...(page ? { page } : {}) };
    }
    const path = parsed.searchParams.get('path');
    if (path && (parsed.pathname === ENDPOINTS.filesImage || parsed.pathname === ENDPOINTS.filesPreview)) {
        return { kind: 'path', path, sessionId };
    }
    return null;
}

/**
 * Opens a link from an answer or an activity list: a file on the paired PC shows in the app's preview, anything else
 * on the web goes to the browser. A PC link is never handed to the browser, which has neither the pin nor the token.
 */
export function openPcFile(input: string | CitedDocument, sessionId: string | null): OpenResult {
    if (typeof input !== 'string') {
        if (!sessionId) return 'ignored';
        const { path, ...doc } = input;
        usePreviewStore.getState().show(path ? { kind: 'path', path, sessionId } : { kind: 'document', ...doc, sessionId });
        return 'preview';
    }
    const link = input.trim();
    if (/^https?:\/\//i.test(link) && !isPcUrl(link)) {
        openLinkFromPress(link);
        return 'browser';
    }
    if (/^mailto:/i.test(link)) {
        openLinkFromPress(link);
        return 'browser';
    }
    const verdict = checkLink(link);
    if (!verdict.allowed && !isFileLink(link) && !link.startsWith('/api/') && (verdict.scheme !== null || link.startsWith('//'))) {
        openLinkFromPress(link);
        return 'ignored';
    }
    if (!sessionId) return 'ignored';
    const isUrl = /^https?:\/\//i.test(link) || link.startsWith('/api/');
    const target: PreviewTarget | null = isUrl
        ? isPcUrl(link)
            ? targetOfPcUrl(link, sessionId)
            : null
        : isFileLink(link)
          ? { kind: 'path', path: pathOfFileLink(link), sessionId }
          : null;
    if (!target) return 'ignored';
    usePreviewStore.getState().show(target);
    return 'preview';
}
