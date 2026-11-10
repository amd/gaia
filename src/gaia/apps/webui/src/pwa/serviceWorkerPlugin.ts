// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createHash } from 'crypto';
import { readFileSync } from 'fs';
import type { Plugin } from 'vite';

export const BUILD_ID_PLACEHOLDER = '__GAIA_BUILD_ID__';

// Vite names a chunk after a hash of its content, so a chunk holding the version
// is renamed when the version changes, and so is every chunk that imports it.
// Mirrors _VITE_HASHED in util/release_train.py.
const VITE_HASHED = /([A-Za-z0-9_.$]+(?:-[A-Za-z0-9_.$]+)*?)-[A-Za-z0-9_-]{8}\.(js|mjs|css|woff2?|ttf|svg|png|jpg|webp|ico|map)\b/g;
const SAFE_VERSION = /^[0-9A-Za-z.+!_-]+$/;
const VERSION_MARK = '\u0000version\u0000';

function versionFree(text: string, version: string): string {
    return text.split(version).join(VERSION_MARK).replace(VITE_HASHED, '$1-#.$2');
}

/**
 * The build id: the version, then a hash of every emitted file with the version
 * masked out. A new release or any real code change gives a new id, while an RC
 * and its final build differ only by the version string the release twin check
 * already accounts for.
 */
export function buildIdFor(files: Record<string, string | Uint8Array>, version: string): string {
    if (!SAFE_VERSION.test(version)) {
        throw new Error(`service worker build id: version ${JSON.stringify(version)} has characters a cache name should not carry`);
    }
    const entries = Object.entries(files).map(([name, content]) => {
        // latin1 maps bytes 1:1, so masking ASCII never corrupts binary assets.
        const text = typeof content === 'string' ? content : Buffer.from(content).toString('latin1');
        const digest = createHash('sha256').update(versionFree(text, version), 'latin1').digest('hex');
        return `${versionFree(name, version)}\t${digest}`;
    });
    const hash = createHash('sha256').update(entries.sort().join('\n')).digest('hex').slice(0, 16);
    return `${version}-${hash}`;
}

export function renderServiceWorker(template: string, buildId: string): string {
    if (!template.includes(BUILD_ID_PLACEHOLDER)) {
        throw new Error(`service worker template has no ${BUILD_ID_PLACEHOLDER} placeholder`);
    }
    return template.split(BUILD_ID_PLACEHOLDER).join(buildId);
}

/** Emit dist/sw.js from the template, stamped with this build's id. */
export function serviceWorkerPlugin(templatePath: string, version: string): Plugin {
    return {
        name: 'gaia-service-worker',
        apply: 'build',
        generateBundle(_options, bundle) {
            const files: Record<string, string | Uint8Array> = {};
            for (const [fileName, output] of Object.entries(bundle)) {
                files[fileName] = output.type === 'chunk' ? output.code : output.source;
            }
            const source = renderServiceWorker(readFileSync(templatePath, 'utf8'), buildIdFor(files, version));
            this.emitFile({ type: 'asset', fileName: 'sw.js', source });
        },
    };
}
