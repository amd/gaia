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

const fs = require('fs');
const path = require('path');
const pkg = require('./package.json');

// Locales to keep — English only. All others are deleted in the postPackage hook.
const KEEP_LOCALES = new Set(['en-US.pak']);

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
        // Only dist needs to be an extraResource (loaded via process.resourcesPath).
        // services/, preload.cjs, and assets/ are included in the asar via package.json "files".
        extraResource: ['./dist'],
        appVersion: semverVersion,
        asar: true,
        // Exclude source files, dev configs, local state, and build artifacts.
        // dist/ ships via extraResource; node_modules/ is pruned by flora-colossus.
        ignore: [
            /^\/dist\//,
            /^\/src\//,
            /^\/\.gaia\//,
            /^\/public\//,
            /^\/out\//,
            /\.tgz$/,
            /^\/gaia\.log/,
            /^\/index\.html/,
            /^\/tsconfig\.json/,
            /^\/vite\.config\.ts/,
            /^\/forge\.config\.cjs/,
            /^\/\.gitignore/,
            /^\/\.npmignore/,
            /^\/node_modules\//,
        ],
    },
    hooks: {
        // Prune Chromium locales to English only after packaging (saves ~45 MB).
        // The locales/ folder is part of the Electron binary distribution and cannot
        // be filtered via packagerConfig.ignore — it requires a post-copy hook.
        postPackage: async (_forgeConfig, options) => {
            for (const outputPath of options.outputPaths) {
                const localesDir = path.join(outputPath, 'locales');
                if (!fs.existsSync(localesDir)) continue;
                for (const file of fs.readdirSync(localesDir)) {
                    if (!KEEP_LOCALES.has(file)) {
                        fs.rmSync(path.join(localesDir, file));
                    }
                }
            }
        },
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
