// @vitest-environment node
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The release train builds every RC next to its final twin and fails unless the
// two differ only by the version string. This builds a tiny app twice through
// the real Vite pipeline to prove dist/sw.js passes that check.

import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join, resolve } from 'path';
import { afterAll, describe, expect, it } from 'vitest';
import { build } from 'vite';
import { serviceWorkerPlugin } from '../serviceWorkerPlugin';

const TEMPLATE = resolve(__dirname, '..', 'sw.js');
const root = mkdtempSync(join(tmpdir(), 'gaia-sw-twin-'));

writeFileSync(join(root, 'index.html'), '<!doctype html><script type="module" src="./main.js"></script>');
writeFileSync(join(root, 'version.js'), 'export const VERSION = __APP_VERSION__;');
writeFileSync(join(root, 'main.js'), "import { VERSION } from './version.js';\nconsole.log(VERSION);\nimport('./lazy.js').then((m) => m.show());");
writeFileSync(join(root, 'lazy.js'), "import { VERSION } from './version.js';\nexport function show() { return 'v' + VERSION + ' __CODE__'; }");

async function swFor(version: string, code = 'one'): Promise<string> {
    writeFileSync(join(root, 'lazy.js'), readFileSync(join(root, 'lazy.js'), 'utf8').replace(/'v' \+ VERSION \+ ' [^']*'/, `'v' + VERSION + ' ${code}'`));
    const outDir = join(root, `dist-${version}-${code}`);
    await build({
        root,
        configFile: false,
        logLevel: 'silent',
        base: './',
        define: { __APP_VERSION__: JSON.stringify(version) },
        plugins: [serviceWorkerPlugin(TEMPLATE, version)],
        build: { outDir, emptyOutDir: true, minify: false },
    });
    return readFileSync(join(outDir, 'sw.js'), 'utf8');
}

const cacheName = (sw: string) => sw.match(/const CACHE_NAME = CACHE_PREFIX \+ '([^']+)'/)![1];

afterAll(() => rmSync(root, { recursive: true, force: true }));

describe('service worker in an RC and its final twin', () => {
    it('differs only by the version string', async () => {
        const rc = await swFor('9.8.7rc2');
        const final = await swFor('9.8.7');
        expect(rc).not.toBe(final);
        expect(rc.split('9.8.7rc2').join('9.8.7')).toBe(final);
    }, 60_000);

    it('still gets a new cache for a new release and for a code change', async () => {
        const base = cacheName(await swFor('9.8.7'));
        const bumped = cacheName(await swFor('9.8.8'));
        expect(bumped).toMatch(/^9\.8\.8-/);
        expect(bumped).not.toBe(base);
        const changed = cacheName(await swFor('9.8.7', 'two'));
        expect(changed).toMatch(/^9\.8\.7-/);
        expect(changed).not.toBe(base);
    }, 60_000);
});
