// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { readFileSync, existsSync } from 'fs';
import { resolve } from 'path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { registerServiceWorker } from '../register';
import { BUILD_ID_PLACEHOLDER, buildIdFor, renderServiceWorker } from '../serviceWorkerPlugin';

const WEBUI = resolve(__dirname, '..', '..', '..');
const PUBLIC = resolve(WEBUI, 'public');
const SW_TEMPLATE = readFileSync(resolve(WEBUI, 'src', 'pwa', 'sw.js'), 'utf8');

function pngSize(path: string): [number, number] {
    const buf = readFileSync(path);
    expect(buf.subarray(1, 4).toString('latin1')).toBe('PNG');
    return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
}

describe('registerServiceWorker', () => {
    afterEach(() => vi.restoreAllMocks());

    function container(result: Promise<unknown> = Promise.resolve({})) {
        return { register: vi.fn(() => result) } as unknown as Pick<ServiceWorkerContainer, 'register'> & {
            register: ReturnType<typeof vi.fn>;
        };
    }

    it('registers ./sw.js with a ./ scope in a production https page', async () => {
        const sw = container();
        await expect(registerServiceWorker({ isProduction: true, protocol: 'https:', serviceWorker: sw })).resolves.toBe(true);
        expect(sw.register).toHaveBeenCalledWith('./sw.js', { scope: './' });
    });

    it('registers on plain http too (localhost is a secure context)', async () => {
        const sw = container();
        await expect(registerServiceWorker({ isProduction: true, protocol: 'http:', serviceWorker: sw })).resolves.toBe(true);
    });

    it.each([
        ['the dev server', { isProduction: false, protocol: 'http:' }],
        ['the desktop app on file://', { isProduction: true, protocol: 'file:' }],
        ['a data: page', { isProduction: true, protocol: 'data:' }],
    ])('does not register in %s', async (_label, env) => {
        const sw = container();
        await expect(registerServiceWorker({ ...env, serviceWorker: sw })).resolves.toBe(false);
        expect(sw.register).not.toHaveBeenCalled();
    });

    it('does nothing when the browser exposes no service workers', async () => {
        await expect(registerServiceWorker({ isProduction: true, protocol: 'http:' })).resolves.toBe(false);
    });

    it('reports a failed registration instead of hiding it', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => {});
        const sw = container(Promise.reject(new Error('bad MIME type')));
        await expect(registerServiceWorker({ isProduction: true, protocol: 'https:', serviceWorker: sw })).resolves.toBe(false);
        expect(error).toHaveBeenCalledWith(expect.stringContaining('[pwa]'), expect.objectContaining({ message: 'bad MIME type' }));
    });
});

describe('web app manifest', () => {
    const manifest = JSON.parse(readFileSync(resolve(PUBLIC, 'manifest.json'), 'utf8'));

    it('launches standalone from the page it was installed from', () => {
        expect(manifest).toMatchObject({ name: 'GAIA', short_name: 'GAIA', display: 'standalone' });
        // Relative so a tunnel URL or a sub-path both resolve against the manifest itself.
        expect(manifest.start_url).toBe('./');
        expect(manifest.scope).toBe('./');
        expect(manifest.theme_color).toMatch(/^#[0-9A-Fa-f]{6}$/);
        expect(manifest.background_color).toMatch(/^#[0-9A-Fa-f]{6}$/);
    });

    it('lists 192 and 512 icons, plain and maskable, each matching its file', () => {
        const seen = new Set<string>();
        for (const icon of manifest.icons) {
            expect(icon.src.startsWith('/')).toBe(false);
            const [w, h] = pngSize(resolve(PUBLIC, icon.src));
            expect(`${w}x${h}`).toBe(icon.sizes);
            expect(icon.type).toBe('image/png');
            seen.add(`${icon.sizes}:${icon.purpose}`);
        }
        expect([...seen].sort()).toEqual(['192x192:any', '192x192:maskable', '512x512:any', '512x512:maskable']);
    });

    it('fills the corners of the maskable and Apple icons, which platforms crop or blacken', () => {
        for (const name of ['icon-maskable-192.png', 'icon-maskable-512.png', 'apple-touch-icon.png']) {
            // Colour type 2 is truecolour with no alpha channel.
            expect(readFileSync(resolve(PUBLIC, 'icons', name))[25]).toBe(2);
        }
        expect(pngSize(resolve(PUBLIC, 'icons', 'apple-touch-icon.png'))).toEqual([180, 180]);
    });
});

describe('index.html', () => {
    const html = readFileSync(resolve(WEBUI, 'index.html'), 'utf8');

    it('links the manifest and the Apple touch icon, and both exist', () => {
        const manifest = html.match(/<link rel="manifest" href="\/([^"]+)"/);
        const touch = html.match(/<link rel="apple-touch-icon" href="\/([^"]+)"/);
        expect(manifest && existsSync(resolve(PUBLIC, manifest[1]))).toBe(true);
        expect(touch && existsSync(resolve(PUBLIC, touch[1]))).toBe(true);
        expect(html).toContain('<meta name="apple-mobile-web-app-capable" content="yes" />');
    });
});

describe('service worker build stamp', () => {
    it('stamps every placeholder with the build id', () => {
        const out = renderServiceWorker(SW_TEMPLATE, 'abc123');
        expect(out).not.toContain(BUILD_ID_PLACEHOLDER);
        expect(out).toContain("const CACHE_NAME = CACHE_PREFIX + 'abc123';");
    });

    it('refuses a template without the placeholder rather than ship an unversioned cache', () => {
        expect(() => renderServiceWorker("const CACHE_NAME = 'gaia-assets-v1';", 'abc')).toThrow(BUILD_ID_PLACEHOLDER);
    });

    it('gives a new id whenever an emitted file name changes, regardless of order', () => {
        const a = buildIdFor(['index.html', 'assets/index-AAAA.js']);
        expect(buildIdFor(['assets/index-AAAA.js', 'index.html'])).toBe(a);
        expect(buildIdFor(['index.html', 'assets/index-BBBB.js'])).not.toBe(a);
        expect(a).toMatch(/^[0-9a-f]{16}$/);
    });
});

// ── The worker itself, run against fakes of the service-worker globals ──────

interface FakeResponse {
    ok: boolean;
    body: string;
    clone: () => FakeResponse;
}

function response(body: string, ok = true): FakeResponse {
    const r: FakeResponse = { ok, body, clone: () => response(body, ok) };
    return r;
}

function loadWorker({ scope = 'https://gaia.example/', buildId = 'build2', existing = [] as string[] } = {}) {
    const listeners: Record<string, (event: unknown) => void> = {};
    const stores = new Map<string, Map<string, FakeResponse>>();
    for (const name of existing) stores.set(name, new Map());
    const caches = {
        keys: vi.fn(async () => [...stores.keys()]),
        delete: vi.fn(async (name: string) => stores.delete(name)),
        open: vi.fn(async (name: string) => {
            if (!stores.has(name)) stores.set(name, new Map());
            const store = stores.get(name)!;
            return {
                match: vi.fn(async (req: { url: string }) => store.get(req.url)),
                put: vi.fn(async (req: { url: string }, res: FakeResponse) => {
                    store.set(req.url, res);
                }),
            };
        }),
    };
    const fetchMock = vi.fn(async (req: { url: string }) => response(`network:${req.url}`));
    const self = {
        registration: { scope },
        location: new URL(scope),
        skipWaiting: vi.fn(),
        clients: { claim: vi.fn(async () => {}) },
        addEventListener: (type: string, fn: (event: unknown) => void) => {
            listeners[type] = fn;
        },
    };
    new Function('self', 'caches', 'fetch', renderServiceWorker(SW_TEMPLATE, buildId))(self, caches, fetchMock);

    async function request(url: string, method = 'GET') {
        let responded: Promise<FakeResponse> | undefined;
        listeners.fetch({
            request: { url, method },
            respondWith: (p: Promise<FakeResponse>) => {
                responded = p;
            },
        });
        return responded ? await responded : undefined;
    }

    return { listeners, stores, caches, fetchMock, self, request };
}

describe('service worker', () => {
    it('takes over on install so a new build applies on the next launch', () => {
        const w = loadWorker();
        w.listeners.install({});
        expect(w.self.skipWaiting).toHaveBeenCalled();
    });

    it('on activate deletes only older GAIA asset caches', async () => {
        const w = loadWorker({ existing: ['gaia-assets-build1', 'gaia-assets-build2', 'someone-else'] });
        let done: Promise<unknown> = Promise.resolve();
        w.listeners.activate({ waitUntil: (p: Promise<unknown>) => (done = p) });
        await done;
        expect([...w.stores.keys()].sort()).toEqual(['gaia-assets-build2', 'someone-else']);
        expect(w.self.clients.claim).toHaveBeenCalled();
    });

    it('serves a hashed asset from the cache after the first load', async () => {
        const w = loadWorker();
        const url = 'https://gaia.example/assets/index-AAAA.js';
        expect((await w.request(url))?.body).toBe(`network:${url}`);
        expect((await w.request(url))?.body).toBe(`network:${url}`);
        expect(w.fetchMock).toHaveBeenCalledTimes(1);
        expect(w.stores.get('gaia-assets-build2')?.has(url)).toBe(true);
    });

    it('does not cache a failed asset response', async () => {
        const w = loadWorker();
        w.fetchMock.mockImplementation(async () => response('Not Found', false));
        const url = 'https://gaia.example/assets/missing-BBBB.js';
        expect((await w.request(url))?.ok).toBe(false);
        await w.request(url);
        expect(w.fetchMock).toHaveBeenCalledTimes(2);
    });

    it.each([
        ['the page itself', 'https://gaia.example/'],
        ['the QR token landing', 'https://gaia.example/?token=abc'],
        ['an API call', 'https://gaia.example/api/sessions'],
        ['a chat stream', 'https://gaia.example/api/chat/send'],
        ['the manifest', 'https://gaia.example/manifest.json'],
        ['the worker script', 'https://gaia.example/sw.js'],
        ['a look-alike path', 'https://gaia.example/assetsX/index.js'],
        ['an asset path nested elsewhere', 'https://gaia.example/api/assets/x.js'],
        ['another origin', 'https://cdn.example/assets/index-AAAA.js'],
    ])('leaves %s to the network', async (_label, url) => {
        const w = loadWorker();
        expect(await w.request(url)).toBeUndefined();
        expect(w.caches.open).not.toHaveBeenCalled();
    });

    it('never intercepts a non-GET request, even under assets/', async () => {
        const w = loadWorker();
        expect(await w.request('https://gaia.example/assets/index-AAAA.js', 'POST')).toBeUndefined();
    });

    it('scopes the asset path to where the UI is served', async () => {
        const w = loadWorker({ scope: 'https://gaia.example/ui/' });
        expect(await w.request('https://gaia.example/assets/index-AAAA.js')).toBeUndefined();
        expect(await w.request('https://gaia.example/ui/assets/index-AAAA.js')).toBeDefined();
    });
});
