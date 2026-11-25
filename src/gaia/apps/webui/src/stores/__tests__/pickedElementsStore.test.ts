// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Composer chips for elements picked in the browser pane. The payload is
 * checked against tests/fixtures/browser_pane/picked/, the same files the
 * UI server validates and main's picker builds.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api');

import * as api from '../../services/api';
import { usePickedElementsStore } from '../pickedElementsStore';
import type { PickedElement } from '../../../services/browser-pane/contract';
import saveButton from '../../../../../../../tests/fixtures/browser_pane/picked/save_button.json';
import wireSaveButton from '../../../../../../../tests/fixtures/browser_pane/picked/wire_save_button.json';
import wireTogglesOff from '../../../../../../../tests/fixtures/browser_pane/picked/wire_toggles_off.json';

const SAVE = saveButton as PickedElement;
const CROP = btoa('png');

function upload(url: string) {
    return { filename: 'f', original_name: 'f', url, size: 3, content_type: 'image/png', is_image: true };
}

const store = () => usePickedElementsStore.getState();
const flush = () => new Promise((r) => setTimeout(r, 0));

describe('picked element chips', () => {
    beforeEach(() => {
        usePickedElementsStore.setState({ chips: [], dismissed: [] });
        vi.mocked(api.uploadFile).mockReset();
    });

    it('send the shared wire payload once the crop has uploaded', async () => {
        vi.mocked(api.uploadFile).mockResolvedValue(upload('/api/files/uploads/picked-p1.png'));
        store().applyPick({ run: 1, elements: [{ ...SAVE, crop: CROP }] });
        expect(store().chips[0].uploading).toBe(true);
        await flush();
        const file = vi.mocked(api.uploadFile).mock.calls[0][0];
        expect([file.name, file.type]).toEqual(['picked-p1.png', 'image/png']);
        store().toggle('p1', 'screenshot');
        expect(store().take()).toEqual([wireSaveButton]);
        expect(store().chips).toEqual([]);
    });

    it('toggled-off parts go out as null', async () => {
        vi.mocked(api.uploadFile).mockResolvedValue(upload('/api/files/uploads/picked-p1.png'));
        store().applyPick({ run: 1, elements: [{ ...SAVE, crop: CROP }] });
        await flush();
        store().toggle('p1', 'html');
        store().toggle('p1', 'styles');
        store().toggle('p1', 'screenshot');
        expect(store().take()).toEqual([wireTogglesOff]);
    });

    it('a later event of the same run replaces its chips and keeps the toggles of those that stay', () => {
        store().applyPick({ run: 1, elements: [SAVE] });
        store().toggle('p1', 'html');
        store().applyPick({ run: 1, elements: [SAVE, { ...SAVE, ref: 'p2' }] });
        expect(store().chips.map((c) => [c.element.ref, c.include.html])).toEqual([['p1', false], ['p2', true]]);
        store().applyPick({ run: 1, elements: [{ ...SAVE, ref: 'p3' }] });
        expect(store().chips.map((c) => c.element.ref)).toEqual(['p3']);
    });

    it('chips of an earlier run stay when a new run picks', () => {
        store().applyPick({ run: 1, elements: [SAVE] });
        store().applyPick({ run: 2, elements: [{ ...SAVE, ref: 'p2' }] });
        expect(store().chips.map((c) => c.element.ref)).toEqual(['p1', 'p2']);
    });

    it('a removed chip stays removed while its run keeps picking', () => {
        store().applyPick({ run: 1, elements: [SAVE] });
        store().remove('p1');
        store().applyPick({ run: 1, elements: [SAVE, { ...SAVE, ref: 'p2' }] });
        expect(store().chips.map((c) => c.element.ref)).toEqual(['p2']);
    });

    it('leaves the screenshot off until the user turns it on', async () => {
        vi.mocked(api.uploadFile).mockResolvedValue(upload('/api/files/uploads/picked-p1.png'));
        store().applyPick({ run: 1, elements: [{ ...SAVE, crop: CROP }] });
        await flush();
        expect(store().chips[0]?.include.screenshot).toBe(false);
        expect(store().take()?.[0]?.cropUrl).toBeNull();
    });

    it('without a crop there is no screenshot to send and nothing to upload', () => {
        store().applyPick({ run: 1, elements: [SAVE] });
        expect(store().chips[0]).toMatchObject({ uploading: false, include: { screenshot: false } });
        expect(api.uploadFile).not.toHaveBeenCalled();
        expect(store().take()?.[0].cropUrl).toBeNull();
    });

    it('a crop that fails to upload is shown, and the element still sends', async () => {
        vi.mocked(api.uploadFile).mockRejectedValue(new Error('disk full'));
        store().applyPick({ run: 1, elements: [{ ...SAVE, crop: CROP }] });
        await flush();
        expect(store().chips[0]).toMatchObject({ uploading: false, error: 'disk full' });
        expect(store().take()?.[0]).toMatchObject({ ref: 'p1', cropUrl: null });
    });

    it('nothing picked sends no page_elements at all', () => {
        expect(store().take()).toBeUndefined();
    });
});
