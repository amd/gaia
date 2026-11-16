// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as Sharing from 'expo-sharing';

import { GaiaApiError, GaiaNetworkError } from '@/api/errors';
import { createFakeClient } from '@/api/fake';
import { describeError } from '@/i18n/useT';
import { translator } from '@webui/i18n/translator';

import { exportChat, exportFileName } from './exportChat';

jest.mock('expo-file-system', () => ({
    Paths: { cache: 'file:///cache/' },
    Directory: class {
        create() {}
    },
    File: class {
        uri: string;
        constructor(_folder: unknown, name: string) {
            this.uri = `file:///cache/chat-exports/${name}`;
        }
        create() {}
        write() {}
    },
}));
jest.mock('expo-sharing', () => ({ shareAsync: jest.fn(), isAvailableAsync: jest.fn(async () => true) }));

const SESSION = { id: 's1', title: 'a/b: c?', created_at: '', updated_at: '', model: '', system_prompt: null, message_count: 0, document_ids: [] };

describe('exportFileName', () => {
    it('replaces reserved characters with _ and caps the title at 100 characters', () => {
        expect(exportFileName('a/b\\c:d*e?f"g<h>i|j')).toBe('a_b_c_d_e_f_g_h_i_j.md');
        expect(exportFileName('x'.repeat(150))).toBe(`${'x'.repeat(100)}.md`);
        expect(exportFileName(undefined)).toBe('chat.md');
    });
});

describe('exportChat', () => {
    const t = translator('en');

    it('shares <title>.md through the share sheet', async () => {
        await exportChat(createFakeClient({ sessions: [SESSION] }), 's1', SESSION.title);
        expect(Sharing.shareAsync).toHaveBeenCalledWith('file:///cache/chat-exports/a_b_ c_.md', { mimeType: 'text/markdown' });
    });

    it("words a PC error as the desktop's export bar does", async () => {
        const client = createFakeClient({ sessions: [SESSION] });
        client.exportSessionMarkdown = async () => {
            throw new GaiaApiError(500, 'boom');
        };
        const error = await exportChat(client, 's1', 'x').catch((e: unknown) => e);
        expect(describeError(error, t)).toBe(
            "Couldn't export this chat: GAIA on your PC returned an error (HTTP 500). Try Export again; if it keeps failing, check the log on your PC.",
        );
        client.exportSessionMarkdown = async () => {
            throw new GaiaNetworkError('http://pc', new Error('down'));
        };
        const unreachable = await exportChat(client, 's1', 'x').catch((e: unknown) => e);
        expect(describeError(unreachable, t)).toBe("Couldn't export this chat because GAIA on your PC isn't reachable. Try Export again once GAIA is answering.");
    });
});
