// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { seedUrl } from './connection';

describe('the development PC address', () => {
    it('never reaches a production phone build, which pairs over pinned TLS instead', () => {
        expect(seedUrl('http://192.168.1.5:4200', false, 'ios')).toBeNull();
        expect(seedUrl('http://192.168.1.5:4200', false, 'android')).toBeNull();
        expect(seedUrl('http://127.0.0.1:4200', true, 'ios')).toBe('http://127.0.0.1:4200');
        expect(seedUrl('http://127.0.0.1:4200', false, 'web')).toBe('http://127.0.0.1:4200');
        expect(seedUrl(undefined, true, 'ios')).toBeNull();
    });
});
