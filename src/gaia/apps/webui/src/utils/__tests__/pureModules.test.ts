// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// @vitest-environment node

/**
 * The chat logic the mobile app imports from here: it must load without a
 * DOM, and nothing it reaches at runtime may pull in the browser client,
 * a store, a component, an icon set, CSS or Electron.
 */

import { readFileSync, existsSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '../..');

const PURE_MODULES = [
    'utils/chatStream',
    'utils/citations',
    'utils/chatAlerts.core',
    'utils/format',
    'utils/liveStatus',
    'utils/longContent',
    'utils/markdown',
    'utils/messageQueue',
    'utils/modelLabels',
    'utils/palette.core',
    'utils/remarkSoftBreaks',
    'utils/remarkWindowsPaths',
    'utils/renderCards.core',
    'utils/permissionPrompts.core',
    'utils/requestErrors.core',
    'utils/responseShape',
    'utils/sessionGrouping',
    'utils/sessionTitle',
    'utils/streamErrors.core',
    'utils/streamSteps.core',
    'utils/toolCallContent',
    'utils/toolEvents.core',
    'utils/windowsPaths',
    'services/apiErrors',
    'i18n/translator',
];

const FORBIDDEN = [
    /^components\//, /^stores\//, /^services\/api$/, /^utils\/theme$/, /\.css$/,
    /^lucide-react/, /^react-dom/, /^electron/,
];

/** Specifiers a module loads at runtime: `import type` and `export type` are erased. */
function runtimeImports(source: string): string[] {
    const specs: string[] = [];
    const re = /^\s*(import|export)\s+(type\s+)?([^;'"]*?\bfrom\s+)?['"]([^'"]+)['"]/gm;
    for (const m of source.matchAll(re)) {
        if (m[2]) continue;
        if (m[1] === 'export' && !m[3]) continue;
        specs.push(m[4]);
    }
    return specs;
}

function resolveModule(fromFile: string, spec: string): string | null {
    if (!spec.startsWith('.')) return null;
    const base = resolve(dirname(fromFile), spec);
    for (const ext of ['.ts', '.tsx', '/index.ts']) {
        if (existsSync(base + ext)) return base + ext;
    }
    if (existsSync(base)) return base;
    throw new Error(`${relative(SRC, fromFile)} imports ${spec}, which does not resolve`);
}

/** Every forbidden module reachable at runtime from `entry`, as "via → target". */
function forbiddenReach(entry: string): string[] {
    const seen = new Set<string>();
    const hits: string[] = [];
    const walk = (file: string) => {
        if (seen.has(file)) return;
        seen.add(file);
        for (const spec of runtimeImports(readFileSync(file, 'utf8'))) {
            const target = resolveModule(file, spec);
            const name = target ? relative(SRC, target).replace(/(\/index)?\.tsx?$/, '') : spec;
            if (FORBIDDEN.some((re) => re.test(name))) {
                hits.push(`${relative(SRC, file)} -> ${name}`);
                continue;
            }
            if (target && /\.tsx?$/.test(target)) walk(target);
        }
    };
    walk(resolve(SRC, `${entry}.ts`));
    return hits;
}

describe('modules shared with the mobile app', () => {
    it.each(PURE_MODULES)('%s reaches no browser-only module', (mod) => {
        expect(forbiddenReach(mod)).toEqual([]);
    });

    it.each(PURE_MODULES)('%s loads without a DOM', async (mod) => {
        expect(typeof window).toBe('undefined');
        expect(typeof document).toBe('undefined');
        await expect(import(/* @vite-ignore */ `../../${mod}.ts`)).resolves.toBeTypeOf('object');
    });
});
