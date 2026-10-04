// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { pathAccessQuestion } from '../notificationStore';

describe('pathAccessQuestion', () => {
    it('asks about a file as a file', () => {
        expect(pathAccessQuestion({ path: 'C:\\Users\\me\\sales_data_2025.csv', kind: 'file' })).toBe(
            'GAIA wants to use the file C:\\Users\\me\\sales_data_2025.csv, which this chat cannot reach yet. Allow it for this chat?'
        );
    });

    it('says a folder grant covers what is in it', () => {
        expect(pathAccessQuestion({ path: 'C:\\Users\\me\\reports', kind: 'folder' })).toContain(
            'the folder C:\\Users\\me\\reports and everything in it'
        );
    });

    it('keeps the scope warning when the agent did not say file or folder', () => {
        const question = pathAccessQuestion({ path: 'C:\\Users\\me\\x' });
        expect(question).toBe(
            'GAIA wants to use C:\\Users\\me\\x (and anything inside it, including changes), ' +
            'which this chat cannot reach yet. Allow it for this chat?'
        );
    });
});
