// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { BrowseResponse, PreflightReport } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';

export type FileBrowserScene = 'root' | 'folder' | 'search' | 'denied' | 'searchStopped' | 'selected';

const HOME = '/Users/me';
const DOCS = `${HOME}/Documents`;
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const root: BrowseResponse = {
    current_path: HOME,
    parent_path: '/Users',
    quick_links: [
        { name: 'me', path: HOME, icon: 'home' },
        { name: 'Desktop', path: `${HOME}/Desktop`, icon: 'desktop' },
        { name: 'Documents', path: DOCS, icon: 'documents' },
        { name: 'Downloads', path: `${HOME}/Downloads`, icon: 'download' },
    ],
    entries: [
        { name: 'Desktop', path: `${HOME}/Desktop`, type: 'folder', size: 0, extension: null, modified: ago(40) },
        { name: 'Documents', path: DOCS, type: 'folder', size: 0, extension: null, modified: ago(3 * 60) },
        { name: 'Downloads', path: `${HOME}/Downloads`, type: 'folder', size: 0, extension: null, modified: ago(2 * 24 * 60) },
        { name: '.zshrc', path: `${HOME}/.zshrc`, type: 'file', size: 1200, extension: '', modified: ago(9 * 24 * 60) },
        { name: 'notes.txt', path: `${HOME}/notes.txt`, type: 'file', size: 2048, extension: '.txt', modified: ago(12) },
    ],
};

const folder: BrowseResponse = {
    current_path: DOCS,
    parent_path: HOME,
    quick_links: root.quick_links,
    entries: [
        { name: 'Reports', path: `${DOCS}/Reports`, type: 'folder', size: 0, extension: null, modified: ago(5 * 24 * 60) },
        { name: 'q3-board.pdf', path: `${DOCS}/q3-board.pdf`, type: 'file', size: 481_000, extension: '.pdf', modified: ago(5 * 24 * 60) },
        { name: 'budget.xlsx', path: `${DOCS}/budget.xlsx`, type: 'file', size: 92_160, extension: '.xlsx', modified: ago(26 * 60) },
        { name: 'holiday.mov', path: `${DOCS}/holiday.mov`, type: 'file', size: 88_000_000, extension: '.mov', modified: ago(30 * 24 * 60) },
    ],
};

/** The file browser on a fake PC: `root`, `folder`, `search`, `searchStopped` or `denied`. */
export function fileBrowserScene(scene: FileBrowserScene) {
    return createFakeClient({
        sessions: [{ id: 'chat-1', title: 'Board prep', created_at: ago(60), updated_at: ago(5), model: '', system_prompt: null, message_count: 1, document_ids: [] }],
        preflight: { os: 'mac-arm64', detected_platform: 'mac-arm64', ram_gb: 32, disk_free_gb: 200, npu_detected: false, gpu_name: null, gpu_vram_gb: null, lemonade_running: true, compatible: true } as PreflightReport,
        fileFolders: { '': root, [DOCS]: folder, [`${HOME}/Desktop`]: 'denied' },
        fileSearch: {
            query: 'board',
            total: 2,
            truncated: scene === 'searchStopped',
            searched_locations: [HOME, `${HOME}/Projects`],
            results: [
                { name: 'q3-board.pdf', path: `${DOCS}/q3-board.pdf`, directory: DOCS, size: 481_000, size_display: '470 KB', extension: '.pdf', modified: ago(5 * 24 * 60) },
                { name: 'board-notes.md', path: `${HOME}/board-notes.md`, directory: HOME, size: 3000, size_display: '3 KB', extension: '.md', modified: ago(90) },
            ],
        },
    });
}

export const FILE_BROWSER_START: Record<FileBrowserScene, { path?: string; search?: string; selected?: string[] }> = {
    root: {},
    folder: { path: DOCS },
    selected: { path: DOCS, selected: [`${DOCS}/q3-board.pdf`, `${DOCS}/budget.xlsx`] },
    search: { search: 'board' },
    searchStopped: { search: 'board' },
    denied: { path: `${HOME}/Desktop` },
};

export type FilePreviewScene = 'loading' | 'text' | 'table' | 'binary';

const previewBase = { extension: '', modified: ago(60), is_text: true, encoding: 'utf-8' };

/** The preview screen on a fake PC; `loading` never answers, so the spinner stays. */
export function filePreviewScene(scene: FilePreviewScene) {
    const client = createFakeClient({
        filePreviews: {
            [`${DOCS}/notes.txt`]: { ...previewBase, name: 'notes.txt', path: `${DOCS}/notes.txt`, extension: '.txt', size: 2048, size_display: '2 KB', total_lines: 84, preview_lines: ['Board prep', '', '- Confirm Q3 numbers with finance', '- Send the deck on Thursday', '- Ask legal about the vendor clause'] },
            [`${DOCS}/budget.csv`]: { ...previewBase, name: 'budget.csv', path: `${DOCS}/budget.csv`, extension: '.csv', size: 9216, size_display: '9 KB', total_lines: 241, row_count: 240, columns: ['Region', 'Quarter', 'Revenue', 'Cost'], preview_lines: ['Region,Quarter,Revenue,Cost', 'EMEA,Q1,120400,88200', 'APAC,Q1,98100,70400', 'AMER,Q1,210900,150300'] },
            [`${DOCS}/q3-board.pdf`]: { ...previewBase, name: 'q3-board.pdf', path: `${DOCS}/q3-board.pdf`, extension: '.pdf', is_text: false, size: 481_000, size_display: '470 KB', preview_lines: [] },
        },
    });
    if (scene === 'loading') client.getFilePreview = () => new Promise(() => {});
    return client;
}

export const FILE_PREVIEW_PATH: Record<FilePreviewScene, string> = {
    loading: `${DOCS}/notes.txt`,
    text: `${DOCS}/notes.txt`,
    table: `${DOCS}/budget.csv`,
    binary: `${DOCS}/q3-board.pdf`,
};
