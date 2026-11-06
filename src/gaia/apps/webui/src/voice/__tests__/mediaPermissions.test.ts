// @vitest-environment node
// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createRequire } from 'module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const perms = require('../../../services/media-permissions.cjs') as {
    allowRequest: (permission: string, details: object, opts: { isPackaged: boolean }) => boolean;
    allowCheck: (permission: string, origin: string, details: object, opts: { isPackaged: boolean }) => boolean;
    micSettingsUrl: (platform: string) => string | null;
    installMediaPermissions: (deps: object) => void;
};

const APP = 'file:///C:/Program%20Files/GAIA/resources/app.asar/dist/index.html?api=http%3A%2F%2F127.0.0.1%3A4321%2Fapi';
const packaged = { isPackaged: true };

describe('Electron media permissions', () => {
    it('grants the microphone to the app page', () => {
        expect(perms.allowRequest('media', { mediaTypes: ['audio'], requestingUrl: APP }, packaged)).toBe(true);
        expect(perms.allowCheck('media', 'file:///', { mediaType: 'audio' }, packaged)).toBe(true);
    });

    it('never grants the camera or screen, even to the app page', () => {
        expect(perms.allowRequest('media', { mediaTypes: ['audio', 'video'], requestingUrl: APP }, packaged)).toBe(false);
        expect(perms.allowRequest('media', { mediaTypes: ['video'], requestingUrl: APP }, packaged)).toBe(false);
        expect(perms.allowRequest('media', { mediaTypes: [], requestingUrl: APP }, packaged)).toBe(false);
        expect(perms.allowCheck('media', 'file:///', { mediaType: 'video' }, packaged)).toBe(false);
        expect(perms.allowRequest('display-capture', { requestingUrl: APP }, packaged)).toBe(false);
    });

    it('denies any other origin', () => {
        expect(perms.allowRequest('media', { mediaTypes: ['audio'], requestingUrl: 'https://example.com/' }, packaged)).toBe(false);
        expect(perms.allowRequest('notifications', { requestingUrl: 'https://example.com/' }, packaged)).toBe(false);
        // The dev server only counts when unpackaged.
        expect(perms.allowRequest('media', { mediaTypes: ['audio'], requestingUrl: 'http://localhost:5174/' }, packaged)).toBe(false);
        expect(perms.allowRequest('media', { mediaTypes: ['audio'], requestingUrl: 'http://localhost:5174/' }, { isPackaged: false })).toBe(true);
    });

    it('denies everything the app does not use', () => {
        for (const p of ['geolocation', 'midi', 'midiSysex', 'pointerLock', 'openExternal', 'hid', 'serial', 'usb', 'clipboard-read']) {
            expect(perms.allowRequest(p, { requestingUrl: APP }, packaged)).toBe(false);
        }
    });

    it('keeps what the UI already relies on: copy, chat alerts, the screen on in voice mode', () => {
        for (const p of ['clipboard-sanitized-write', 'notifications', 'screen-wake-lock', 'fullscreen']) {
            expect(perms.allowRequest(p, { requestingUrl: APP }, packaged)).toBe(true);
        }
    });

    it('opens the OS microphone pane where there is one', () => {
        expect(perms.micSettingsUrl('win32')).toBe('ms-settings:privacy-microphone');
        expect(perms.micSettingsUrl('darwin')).toContain('Privacy_Microphone');
        expect(perms.micSettingsUrl('linux')).toBeNull();
    });

    it('installs both handlers and the IPC', async () => {
        const session = { setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn() };
        const handlers: Record<string, () => Promise<unknown>> = {};
        const ipcMain = { handle: vi.fn((name: string, fn: () => Promise<unknown>) => { handlers[name] = fn; }) };
        const shell = { openExternal: vi.fn(async () => undefined) };
        perms.installMediaPermissions({ session, ipcMain, shell, isPackaged: true, platform: 'win32' });
        const request = session.setPermissionRequestHandler.mock.calls[0][0];
        const callback = vi.fn();
        request(null, 'media', callback, { mediaTypes: ['audio'], requestingUrl: APP });
        expect(callback).toHaveBeenCalledWith(true);
        expect(await handlers['system:open-mic-settings']()).toBe(true);
        expect(shell.openExternal).toHaveBeenCalledWith('ms-settings:privacy-microphone');
    });
});
