// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** One memory switch, one name: the composer, Settings and the Memory dashboard all call it what Settings does. */

import { describe, expect, it } from 'vitest';
import { stripIsolates, translateBare } from '../registry';

describe.each(['en', 'ar'])('the memory switch in %s', (code) => {
    const say = (key: Parameters<typeof translateBare>[1]) => stripIsolates(translateBare(code, key));
    const name = say('settings.memory.remember');

    it('has the Settings name on the Memory dashboard', () => {
        expect(say('mem.settings.memory')).toBe(name);
        expect(say('mem.settings.memoryOn')).toContain(name);
        expect(say('mem.settings.memoryOff')).toContain(name);
        expect(say('mem.toast.memoryEnabled')).toContain(name);
    });

    it('has the Settings name on the message box chip', () => {
        expect(say('composer.memoryOn')).toContain(name);
        expect(say('composer.memoryOff')).toContain(name);
        expect(say('composer.memoryUnknown')).toContain(name);
        expect(say('composer.memoryOffTitle')).toContain(name);
    });
});
