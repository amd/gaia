// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import Placeholder from './panes/Placeholder';
import { SETTINGS_SECTIONS } from './sections';

jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { version: 'test' } } }));

describe('SETTINGS_SECTIONS', () => {
    it('wires a real pane for every section', () => {
        const stubs = Object.entries(SETTINGS_SECTIONS).filter(([, s]) => s.pane === Placeholder);
        expect(stubs.map(([id]) => id)).toEqual([]);
    });
});
