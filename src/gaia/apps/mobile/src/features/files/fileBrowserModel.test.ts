// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { FileEntry } from '@/api/client.types';

import { breadcrumbs, emptyKey, folderName, visibleEntries } from './fileBrowserModel';

const entry = (name: string): FileEntry => ({ name, path: `/${name}`, type: 'file', size: 1 });

describe('file browser model', () => {
    it('hides dot names until asked, and says why a folder looks empty', () => {
        const entries = [entry('.git'), entry('a.txt')];
        expect(visibleEntries(entries, false).map((e) => e.name)).toEqual(['a.txt']);
        expect(visibleEntries(entries, true)).toHaveLength(2);
        expect(emptyKey(false, 1)).toBe('fileBrowser.onlyHidden');
        expect(emptyKey(false, 0)).toBe('fileBrowser.empty');
        expect(emptyKey(true, 3)).toBe('fileBrowser.noFiles');
    });

    it('builds crumbs that reach each folder, drives included', () => {
        expect(breadcrumbs('/Users/me/Documents')).toEqual([
            { label: 'Users', path: '/Users' },
            { label: 'me', path: '/Users/me' },
            { label: 'Documents', path: '/Users/me/Documents' },
        ]);
        expect(breadcrumbs('C:\\Users\\me').map((c) => c.path)).toEqual(['C:/', 'C:/Users', 'C:/Users/me']);
        expect(folderName('C:\\Users\\me\\')).toBe('me');
    });
});
