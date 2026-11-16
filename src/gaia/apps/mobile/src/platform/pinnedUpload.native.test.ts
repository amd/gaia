// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Platform } from 'react-native';

import { createHttpClient } from '@/api/http';
import { useConnectionStore } from '@/state/connection';

import { GaiaTransport, type NativeBodySegment } from '../../modules/gaia-transport';
import { platformFetch } from './fetch';

jest.mock('../../modules/gaia-transport', () => ({
    GaiaTransport: { start: jest.fn(), upload: jest.fn(), read: jest.fn(), cancel: jest.fn() },
    transportAvailable: true,
}));
jest.mock('expo/fetch', () => ({ fetch: jest.fn(() => Promise.reject(new Error('unpinned fetch used'))) }));

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

// The phone's FormData (React Native's, with Expo's patch), not Node's, which only takes Blobs.
const nodeFormData = globalThis.FormData;
beforeAll(() => {
    const phoneFormData = jest.requireActual('react-native/Libraries/Network/FormData').default;
    jest.requireActual('expo/src/winter/FormData').installFormDataPatch(phoneFormData);
    globalThis.FormData = phoneFormData;
});
afterAll(() => {
    globalThis.FormData = nodeFormData;
});

const BASE = 'https://192.168.1.20:4443';
const native = jest.mocked(GaiaTransport);

describeNative('an upload to a paired PC', () => {
    it('goes over the pinned transport as a multipart form whose file is read from disk', async () => {
        useConnectionStore.getState().setConnection({ baseUrl: BASE, pcId: 'pc-1', pinned: true });
        native.upload!.mockResolvedValue({ status: 200, headers: { 'content-type': 'application/json' } });
        const reply = new TextEncoder().encode(JSON.stringify({ id: 'd1', filename: 'Q3 "final".pdf' }));
        native.read.mockResolvedValueOnce(reply).mockResolvedValueOnce(null);

        const client = createHttpClient({ baseUrl: BASE, token: null, fetch: platformFetch });
        const uri = 'file:///var/mobile/Caches/DocumentPicker/1F2E.pdf';
        await client.uploadDocument({ uri, name: 'Q3 "final".pdf', mimeType: 'application/pdf', size: 3 });

        expect(native.start).not.toHaveBeenCalled();
        const [, pcId, method, url, headers, segments] = native.upload!.mock.calls[0]!;
        expect([pcId, method, url]).toEqual(['pc-1', 'POST', `${BASE}/api/documents/upload`]);
        const boundary = /^multipart\/form-data; boundary=(\S+)$/.exec(headers['Content-Type'] ?? '')?.[1];
        expect(boundary).toBeTruthy();
        expect(headers).not.toHaveProperty('Authorization');
        const body = (segments as NativeBodySegment[]).map((s) => ('file' in s ? `<${s.file}>` : s.text)).join('');
        expect(body).toBe(
            `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="Q3 %22final%22.pdf"\r\n` +
                `Content-Type: application/pdf\r\n\r\n<${uri}>\r\n--${boundary}--\r\n`,
        );
    });
});
