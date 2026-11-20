// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaTransportMissingError } from '@/api/errors';

import { fetch as expoFetch } from 'expo/fetch';
import { Platform } from 'react-native';

import { platformFetch } from './fetch';

jest.mock('expo/fetch', () => ({ fetch: jest.fn() }));
jest.mock('../../modules/gaia-transport', () => ({ GaiaTransport: {}, transportAvailable: false }));

// Web has its own fetch.web.ts; this is the native module.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

describeNative('platformFetch outside a dev build', () => {
    const globals = globalThis as { __DEV__?: boolean };
    const dev = globals.__DEV__;
    beforeAll(() => {
        globals.__DEV__ = false;
    });
    afterAll(() => {
        globals.__DEV__ = dev;
    });

    it('refuses a request it cannot pin and never calls the plain fetch', async () => {
        await expect(platformFetch('http://192.168.1.5:4200/api/health', {})).rejects.toBeInstanceOf(GaiaTransportMissingError);
        expect(expoFetch).not.toHaveBeenCalled();
    });
});
