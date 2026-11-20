// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ExpoConfig } from 'expo/config';

import { LOCALES } from '@webui/i18n/registry';

import appConfig, { readIosBundleId, readAppEnv, type AppEnv } from '../app.config';

function configFor(env: AppEnv): ExpoConfig {
    const saved = process.env.APP_ENV;
    process.env.APP_ENV = env;
    try {
        return appConfig();
    } finally {
        if (saved === undefined) delete process.env.APP_ENV;
        else process.env.APP_ENV = saved;
    }
}

function pluginOptions(config: ExpoConfig, name: string): Record<string, unknown> {
    for (const plugin of config.plugins ?? []) {
        if (Array.isArray(plugin) && plugin[0] === name) return (plugin as [string, Record<string, unknown>])[1];
    }
    throw new Error(`app.config.ts no longer applies ${name}`);
}

describe('app config', () => {
    it('takes the iOS bundle id from the environment and keeps the URL scheme fixed', () => {
        expect(readIosBundleId('production', {})).toBe('ai.amd.gaia.mobile');
        expect(readIosBundleId('development', { GAIA_IOS_BUNDLE_ID_DEV: '' })).toBe('ai.amd.gaia.mobile.dev');
        expect(readIosBundleId('production', { GAIA_IOS_BUNDLE_ID: 'com.example.gaia' })).toBe('com.example.gaia');
        expect(() => readIosBundleId('production', { GAIA_IOS_BUNDLE_ID: 'not a id' })).toThrow(/GAIA_IOS_BUNDLE_ID .* "not a id"/);

        const saved = process.env.GAIA_IOS_BUNDLE_ID;
        process.env.GAIA_IOS_BUNDLE_ID = 'com.example.gaia';
        try {
            const config = configFor('production');
            expect(config.ios?.bundleIdentifier).toBe('com.example.gaia');
            expect(config.scheme).toBe('ai.amd.gaia.mobile');
        } finally {
            if (saved === undefined) delete process.env.GAIA_IOS_BUNDLE_ID;
            else process.env.GAIA_IOS_BUNDLE_ID = saved;
        }
    });

    it('refuses to load without a known APP_ENV', () => {
        expect(() => readAppEnv(undefined)).toThrow(/APP_ENV .* not set/);
        expect(() => readAppEnv('staging')).toThrow(/"staging"/);
    });

    it('allows plain HTTP only in development, and every build reaches the home network', () => {
        const dev = configFor('development');
        const prod = configFor('production');

        expect(dev.ios?.infoPlist?.NSAppTransportSecurity).toEqual({
            NSAllowsArbitraryLoads: false,
            NSAllowsLocalNetworking: true,
        });
        expect(pluginOptions(dev, './plugins/withCleartextHosts').hosts).toEqual(['10.0.2.2', '127.0.0.1', 'localhost']);

        // The paired PC is a home-network address in production too; arbitrary loads stay off.
        expect(prod.ios?.infoPlist?.NSAppTransportSecurity).toEqual({
            NSAllowsArbitraryLoads: false,
            NSAllowsLocalNetworking: true,
        });
        expect(pluginOptions(prod, './plugins/withCleartextHosts').hosts).toEqual([]);
    });

    it('declares the web UI languages to iOS and Android', () => {
        expect(pluginOptions(configFor('development'), 'expo-localization').supportedLocales).toEqual(
            LOCALES.map((locale) => locale.meta.code),
        );
    });
});
