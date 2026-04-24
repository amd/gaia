// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Resolve the backend API base URL.
 *
 * In the packaged Electron app the bundle is served via `file://`, the
 * backend binds a random free port picked by `services/port-manager.cjs`,
 * and `main.cjs` passes that port to the renderer as an `?api=` query
 * param when loading `dist/index.html`.
 *
 * In the vite dev server and when the backend serves the frontend itself,
 * a same-origin relative path works and no query param is present.
 *
 * See issue #851 for the regression this resolves.
 */
export function getApiBase(): string {
    if (typeof window === 'undefined') return '/api';

    if (window.location.protocol === 'file:') {
        const fromQuery = new URLSearchParams(window.location.search).get('api');
        if (fromQuery) return fromQuery;
        // Legacy fallback: keeps manual `file://.../index.html` opens working
        // against a dev backend on the historical default port.
        return 'http://localhost:4200/api';
    }

    return '/api';
}
