// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Linking } from 'react-native';

import { documentFileUrl } from '@/api/constants';
import { useConnectionStore } from '@/state/connection';

import { openPcFile } from './openPcFile';
import { usePreviewStore } from './previewStore';

describe('openPcFile', () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);

    beforeEach(() => {
        open.mockClear();
        usePreviewStore.getState().close();
        useConnectionStore.getState().setConnection({ baseUrl: 'http://pc.local:4200', token: null });
    });
    afterAll(() => useConnectionStore.getState().clearConnection());

    it.each([
        ['a PC image URL', 'http://pc.local:4200/api/files/image?path=C%3A%5Cout%5Ca.png&session_id=s1', 'C:\\out\\a.png'],
        ['a relative preview URL', '/api/files/preview?path=%2Fhome%2Fme%2Fa.txt', '/home/me/a.txt'],
        ['a file:// URL', 'file:///C:/out/a%20b.txt', 'C:/out/a b.txt'],
        ['a Windows path', 'C:\\out\\a.txt', 'C:\\out\\a.txt'],
        ['a bare absolute path', '/home/me/a.txt', '/home/me/a.txt'],
    ])('%s opens the in-app preview for the current chat, not the browser', (_name, input, path) => {
        expect(openPcFile(input, 's1')).toBe('preview');
        expect(usePreviewStore.getState().target).toEqual({ kind: 'path', path, sessionId: 's1' });
        expect(open).not.toHaveBeenCalled();
    });

    it('a PC document URL opens the preview at its page', () => {
        openPcFile('http://pc.local:4200/api/documents/d%201/file#page=3', 's1');
        expect(usePreviewStore.getState().target).toMatchObject({ kind: 'document', docId: 'd 1', page: 3, sessionId: 's1' });
    });

    it('a PC document URL that names its chat opens the preview too', () => {
        openPcFile(documentFileUrl('http://pc.local:4200', 'd 1', 's1', 3), 's1');
        expect(usePreviewStore.getState().target).toMatchObject({ kind: 'document', docId: 'd 1', page: 3, sessionId: 's1' });
    });

    it('an external link goes to the browser and opens no preview', () => {
        expect(openPcFile('https://example.com/api/files/image?path=a', 's1')).toBe('browser');
        expect(open).toHaveBeenCalledWith('https://example.com/api/files/image?path=a');
        expect(usePreviewStore.getState().target).toBeNull();
    });

    it('with no chat in scope a PC link opens nothing, and never the browser', () => {
        expect(openPcFile('http://pc.local:4200/api/files/image?path=a', null)).toBe('ignored');
        expect(openPcFile('C:\\out\\a.txt', null)).toBe('ignored');
        expect(open).not.toHaveBeenCalled();
        expect(usePreviewStore.getState().target).toBeNull();
    });
});
