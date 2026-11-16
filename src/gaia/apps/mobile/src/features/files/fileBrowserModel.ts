// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey } from '@webui/i18n/registry';
import type { Translate } from '@webui/i18n/translator';

import type { FileEntry } from '@/api/client.types';

export const FILE_TYPE_FILTERS: readonly { label: MessageKey; value: string }[] = [
    { label: 'fileBrowser.filter.all', value: '' },
    { label: 'fileBrowser.filter.documents', value: 'pdf,doc,docx,txt,md' },
    { label: 'fileBrowser.filter.spreadsheets', value: 'csv,xlsx,xls,tsv' },
    { label: 'fileBrowser.filter.code', value: 'py,js,ts,java,c,cpp,go,rs,rb,sh' },
    { label: 'fileBrowser.filter.data', value: 'json,xml,yaml,yml,csv,tsv' },
];

export const QUICK_LINK_LABELS: Record<string, MessageKey> = {
    home: 'fileBrowser.home',
    desktop: 'fileBrowser.desktop',
    documents: 'fileBrowser.documents',
    download: 'fileBrowser.downloads',
};

export const isHiddenName = (name: string) => name.startsWith('.');

/** What a folder lists: hidden items only when asked for. */
export function visibleEntries(entries: readonly FileEntry[], showHidden: boolean): FileEntry[] {
    return showHidden ? [...entries] : entries.filter((e) => !isHiddenName(e.name));
}

/** Each folder of `path` with the absolute path that reaches it; a Windows drive keeps its trailing slash. */
export function breadcrumbs(path: string): { label: string; path: string }[] {
    const segments = path ? path.replace(/\\/g, '/').split('/').filter(Boolean) : [];
    const drive = segments[0]?.includes(':') ?? false;
    return segments.map((label, i) => ({
        label,
        path: (drive ? '' : '/') + segments.slice(0, i + 1).join('/') + (i === 0 && drive ? '/' : ''),
    }));
}

/** The name of a folder as the PC refused it. */
export function folderName(path: string): string {
    return path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path;
}

/** The empty-list message: the search found nothing, only hidden items are there, or the folder is empty. */
export function emptyKey(searching: boolean, hiddenCount: number): MessageKey {
    if (searching) return 'fileBrowser.noFiles';
    return hiddenCount > 0 ? 'fileBrowser.onlyHidden' : 'fileBrowser.empty';
}

/** Add to library and Ask GAIA need a selection and wait while files are being added. */
export const canAct = (selected: number, adding: boolean) => selected > 0 && !adding;

/** The question sent into the chat; the wording is localized, never hard-coded. */
export function askPrompt(names: readonly string[], t: Translate): string {
    if (names.length === 1) return t('mobileApp.files.askOne', { name: names[0]! });
    return t('mobileApp.files.askMany', { list: names.map((n) => `- ${n}`).join('\n') });
}
