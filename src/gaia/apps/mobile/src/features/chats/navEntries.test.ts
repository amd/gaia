// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { declaredNavEntries } from './navEntries';

describe('declaredNavEntries', () => {
    it('lists an entry only when its route is declared, in the web order', () => {
        expect(declaredNavEntries(['index', 'new', 'schedules']).map((e) => e.key)).toEqual(['schedules']);
        expect(declaredNavEntries(['settings', 'skills', 'memory']).map((e) => e.key)).toEqual(['memory', 'skills', 'settings']);
    });
});
