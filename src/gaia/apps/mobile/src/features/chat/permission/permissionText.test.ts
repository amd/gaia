// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { glossParts } from '@webui/i18n/registry';
import { translator } from '@webui/i18n/translator';
import type { GaiaNotification } from '@webui/types/agent';
import { alwaysScopeWith, outsideFolderPath, permissionPromptText } from '@webui/utils/permissionPrompts.core';

const ARABIC = /[؀-ۿ]/;
const prompt = { type: 'permission_request', tool: 'edit_file', toolLabel: 'editing file', alwaysScope: 'edit_file on a.txt' } as GaiaNotification;

describe('permission text in Arabic', () => {
    it('fills each half of the title and question in its own language', () => {
        const t = translator('ar');
        const { heading, question } = permissionPromptText(prompt, t);
        for (const line of [heading, question]) {
            const english = glossParts(line)?.filter((part) => part.gloss) ?? [];
            expect(english.length).toBeGreaterThan(0);
            for (const part of english) expect(part.text).not.toMatch(ARABIC);
        }
        const scope = t.compose((say) => alwaysScopeWith(prompt, say) ?? '');
        for (const part of glossParts(scope)?.filter((p) => p.gloss) ?? []) expect(part.text).not.toMatch(ARABIC);
    });

    it('finds the path in the outside-folders note, and only in that note', () => {
        const note = "/Users/x/a b.txt is outside this chat's folders. Allowing this also lets GAIA use that file for the rest of the chat.";
        expect(outsideFolderPath(note)).toBe('/Users/x/a b.txt');
        expect(outsideFolderPath('This command deletes files.')).toBeNull();
    });
});
