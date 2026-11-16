// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isDeviceRefused, routeAccess } from './routeAccess';

describe('isDeviceRefused', () => {
    it('matches plain paths and the method in any case', () => {
        expect(isDeviceRefused('GET', '/api/providers')).toBe(true);
        expect(isDeviceRefused('get', '/api/providers')).toBe(true);
        expect(isDeviceRefused('POST', '/api/providers/key-store/retry')).toBe(true);
        expect(isDeviceRefused('GET', '/api/settings')).toBe(false);
        expect(isDeviceRefused('POST', '/api/providers')).toBe(false);
    });

    it('matches {param} segments, one segment each', () => {
        expect(isDeviceRefused('DELETE', '/api/providers/fireworks/key')).toBe(true);
        expect(isDeviceRefused('DELETE', '/api/providers/a/b/key')).toBe(false);
        expect(isDeviceRefused('POST', '/api/providers/fireworks/connect')).toBe(false);
    });

    it('matches {param:path} across slashes', () => {
        expect(isDeviceRefused('PUT', '/api/connectors/github/grants/my/agent')).toBe(true);
        expect(isDeviceRefused('DELETE', '/api/connectors/github/grants/my/agent')).toBe(false);
    });

    it('ignores the query string', () => {
        expect(isDeviceRefused('GET', '/api/logs?lines=200')).toBe(true);
        expect(isDeviceRefused('GET', '/api/memory/activity?days=30')).toBe(false);
    });

    it('prefers a literal segment over a parameter, as the backend does', () => {
        expect(routeAccess('GET', '/api/connectors/_debug')).toBe('not_on_listener');
        expect(routeAccess('GET', '/api/connectors/github')).toBe('remote');
        expect(routeAccess('DELETE', '/api/devices/me')).toBe('device_self');
        expect(routeAccess('DELETE', '/api/devices/abc123')).toBe('pc');
    });

    it('knows nothing of a path with no route', () => {
        expect(routeAccess('GET', '/api/nowhere')).toBeNull();
        expect(isDeviceRefused('GET', '/api/nowhere')).toBe(false);
    });
});
