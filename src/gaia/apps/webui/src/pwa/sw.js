// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Service worker for the installable Agent UI. The build (vite.config.ts)
// stamps the build id into CACHE_NAME so every build ships a new worker and a new cache.
//
// It caches only the content-hashed files under assets/, which never change
// once built. Pages, the API and streams always go to the network, so a phone
// never sees a stale UI or stale data.

const CACHE_PREFIX = 'gaia-assets-';
const CACHE_NAME = CACHE_PREFIX + '__GAIA_BUILD_ID__';
const ASSETS_PATH = new URL('assets/', self.registration.scope).pathname;

self.addEventListener('install', () => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches
            .keys()
            .then((names) =>
                Promise.all(
                    names
                        .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
                        .map((name) => caches.delete(name)),
                ),
            )
            .then(() => self.clients.claim()),
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;
    const url = new URL(request.url);
    if (url.origin !== self.location.origin || !url.pathname.startsWith(ASSETS_PATH)) return;

    event.respondWith(
        caches.open(CACHE_NAME).then(async (cache) => {
            const cached = await cache.match(request);
            if (cached) return cached;
            const response = await fetch(request);
            if (response.ok) await cache.put(request, response.clone());
            return response;
        }),
    );
});
