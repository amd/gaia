// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { translator } from '../../i18n/useT';
import { stripIsolates } from '../../i18n/registry';
import { updateErrorText } from '../updateError';

const en = translator('en');
const ar = translator('ar');

/** An updater error as it arrives through Electron's IPC. */
function fromMain(coded: object): Error {
    return new Error(
        `Error invoking remote method 'gaia:update:restart': Error: gaia-update-error:${JSON.stringify(coded)}`,
    );
}

describe('updateErrorText', () => {
    it('shows a coded refusal in the chosen language', () => {
        const err = fromMain({ code: 'diskSpace', params: { need: '1 GB', free: '412 MB' }, message: 'English' });
        expect(en.locale).toBe('en');
        expect(updateErrorText(err, en)).toBe(
            'GAIA needs about 1 GB free to install this update, and this disk has 412 MB. Free some space, then restart from the update chip.',
        );
        const arabic = stripIsolates(updateErrorText(err, ar));
        expect(arabic).toContain('يحتاج GAIA إلى نحو 1 GB');
        expect(arabic).toContain('412 MB');
    });

    it('adds the pin note after the cause', () => {
        const err = fromMain({
            code: 'noInstaller',
            params: { tag: 'v0.25.3', url: 'https://github.com/amd/gaia/releases' },
            note: { code: 'stillPinned', params: { tag: 'v0.25.2' } },
        });
        expect(updateErrorText(err, en)).toBe(
            'GAIA v0.25.3 has no installer for this PC. Download it from https://github.com/amd/gaia/releases instead. Still pinned to v0.25.2.',
        );
    });

    it("keeps an uncoded error's own words, without Electron's prefix", () => {
        const err = new Error("Error invoking remote method 'gaia:update:check': Error: net::ERR_TIMED_OUT");
        expect(updateErrorText(err, ar)).toBe('net::ERR_TIMED_OUT');
    });

    it('shows the English of a code this build has no message for', () => {
        const err = fromMain({ code: 'fromTheFuture', message: 'A newer reason.' });
        expect(updateErrorText(err, en)).toBe('A newer reason.');
    });
});
