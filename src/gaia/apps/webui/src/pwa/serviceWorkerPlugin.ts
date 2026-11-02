// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createHash } from 'crypto';
import { readFileSync } from 'fs';
import type { Plugin } from 'vite';

export const BUILD_ID_PLACEHOLDER = '__GAIA_BUILD_ID__';

/** A build id that changes whenever any emitted file name (and so any content hash) changes. */
export function buildIdFor(fileNames: string[]): string {
    return createHash('sha256').update([...fileNames].sort().join('\n')).digest('hex').slice(0, 16);
}

export function renderServiceWorker(template: string, buildId: string): string {
    if (!template.includes(BUILD_ID_PLACEHOLDER)) {
        throw new Error(`service worker template has no ${BUILD_ID_PLACEHOLDER} placeholder`);
    }
    return template.split(BUILD_ID_PLACEHOLDER).join(buildId);
}

/** Emit dist/sw.js from the template, stamped with this build's id. */
export function serviceWorkerPlugin(templatePath: string): Plugin {
    return {
        name: 'gaia-service-worker',
        apply: 'build',
        generateBundle(_options, bundle) {
            const source = renderServiceWorker(readFileSync(templatePath, 'utf8'), buildIdFor(Object.keys(bundle)));
            this.emitFile({ type: 'asset', fileName: 'sw.js', source });
        },
    };
}
