// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Electron Forge configuration for GAIA Agent UI.
 *
 * Uses a JS config file (instead of inline package.json) to handle
 * dynamic version conversion. GAIA uses 4-part versions (e.g. 0.15.4.1)
 * but Squirrel for Windows requires strict SemVer (3-part: x.y.z).
 *
 * Conversion: "0.15.4.1" -> "0.15.41" (concatenate last two parts)
 */

const pkg = require('./package.json');

/**
 * Convert a GAIA version string to strict SemVer.
 * - 3-part versions pass through unchanged: "1.2.3" -> "1.2.3"
 * - 4-part versions concatenate the last two: "0.15.4.1" -> "0.15.41"
 */
function toSemVer(version) {
    const parts = version.split('.');
    if (parts.length <= 3) return version;
    // Concatenate parts 3 and 4: "0.15.4.1" -> "0.15.41"
    return `${parts[0]}.${parts[1]}.${parts.slice(2).join('')}`;
}

const semverVersion = toSemVer(pkg.version);

module.exports = {
    packagerConfig: {
        name: 'GAIA Agent UI',
        executableName: 'gaia-ui',
        icon: './assets/icon',
        extraResource: ['./dist'],
        appVersion: semverVersion,
    },
    makers: [
        {
            name: '@electron-forge/maker-squirrel',
            config: {
                name: 'gaia-ui',
                setupExe: 'gaia-ui-setup.exe',
                setupIcon: './assets/icon.ico',
                version: semverVersion,
            },
        },
        {
            name: '@electron-forge/maker-deb',
            config: {
                options: {
                    maintainer: 'AMD AI Group',
                    homepage: 'https://amd-gaia.ai',
                },
            },
        },
    ],
};
