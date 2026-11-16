// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const fs = require('node:fs');
const path = require('node:path');
const { AndroidConfig, withAndroidManifest, withDangerousMod } = require('expo/config-plugins');

const NAME = 'network_security_config';
const HOST = /^[A-Za-z0-9.-]+$/;

function securityConfig(hosts) {
    const lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<network-security-config>',
        '    <base-config cleartextTrafficPermitted="false" />',
    ];
    if (hosts.length > 0) {
        lines.push('    <domain-config cleartextTrafficPermitted="true">');
        for (const host of hosts) lines.push(`        <domain includeSubdomains="false">${host}</domain>`);
        lines.push('    </domain-config>');
    }
    lines.push('</network-security-config>', '');
    return lines.join('\n');
}

/**
 * Android's plain-HTTP policy, the same in every build variant: refused for every host except `hosts`.
 * It replaces the debug variant's blanket `usesCleartextTraffic`, which Android ignores once this file exists.
 */
module.exports = function withCleartextHosts(config, { hosts } = {}) {
    if (!Array.isArray(hosts) || hosts.some((host) => typeof host !== 'string' || !HOST.test(host))) {
        throw new Error(
            `withCleartextHosts: "hosts" must list the host names or IP addresses that may use plain HTTP ([] for none), got ${JSON.stringify(hosts)}. See app.config.ts.`,
        );
    }
    config = withDangerousMod(config, [
        'android',
        async (cfg) => {
            const dir = path.join(cfg.modRequest.platformProjectRoot, 'app', 'src', 'main', 'res', 'xml');
            await fs.promises.mkdir(dir, { recursive: true });
            await fs.promises.writeFile(path.join(dir, `${NAME}.xml`), securityConfig(hosts));
            return cfg;
        },
    ]);
    return withAndroidManifest(config, (cfg) => {
        AndroidConfig.Manifest.getMainApplicationOrThrow(cfg.modResults).$['android:networkSecurityConfig'] = `@xml/${NAME}`;
        return cfg;
    });
};
