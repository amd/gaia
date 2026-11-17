// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const fs = require('node:fs');
const path = require('node:path');
const { AndroidConfig, withAndroidManifest, withDangerousMod } = require('expo/config-plugins');

const NAME = 'gaia_data_extraction_rules';
// Every storage domain, credential- and device-encrypted, so nothing of the app's leaves the phone.
const DOMAINS = ['root', 'file', 'database', 'sharedpref', 'external', 'device_root', 'device_file', 'device_database', 'device_sharedpref'];

function extractionRules() {
    const excludes = DOMAINS.map((domain) => `        <exclude domain="${domain}" path="." />`);
    return [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<data-extraction-rules>',
        '    <cloud-backup>',
        ...excludes,
        '    </cloud-backup>',
        '    <device-transfer>',
        ...excludes,
        '    </device-transfer>',
        '</data-extraction-rules>',
        '',
    ].join('\n');
}

/**
 * Keeps every app file out of cloud backup and out of Android 12+ device-to-device transfer, which
 * `allowBackup="false"` alone doesn't stop; `fullBackupContent="false"` covers Android 11 and older.
 */
module.exports = function withBackupExclusions(config) {
    config = withDangerousMod(config, [
        'android',
        async (cfg) => {
            const dir = path.join(cfg.modRequest.platformProjectRoot, 'app', 'src', 'main', 'res', 'xml');
            await fs.promises.mkdir(dir, { recursive: true });
            await fs.promises.writeFile(path.join(dir, `${NAME}.xml`), extractionRules());
            return cfg;
        },
    ]);
    return withAndroidManifest(config, (cfg) => {
        const application = AndroidConfig.Manifest.getMainApplicationOrThrow(cfg.modResults);
        application.$['android:dataExtractionRules'] = `@xml/${NAME}`;
        application.$['android:fullBackupContent'] = 'false';
        return cfg;
    });
};

module.exports.extractionRules = extractionRules;
