// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Resolve the backend API base URL.
 *
 * In the packaged Electron app the bundle is served via `file://`, the
 * backend binds a random free port picked by `services/port-manager.cjs`,
 * and `main.cjs` passes that port to the renderer as an `?api=` query
 * param when loading `dist/index.html` (see `services/index-query.cjs`).
 *
 * In the vite dev server and when the backend serves the frontend itself,
 * a same-origin relative path works and no query param is present.
 *
 * Defense-in-depth: the `?api=` value is validated against an allowlist
 * (loopback URLs ending in `/api` only). Today no protocol handler or
 * file association exists that could let an external party set this
 * param, but the allowlist guards against a future regression that adds
 * one — an attacker-controlled value would otherwise redirect every API
 * call to a server they control (SSRF / data exfiltration). Keep the
 * regex in sync with `services/index-query.cjs`'s `buildIndexQuery`
 * output. The cross-file invariant is enforced by
 * tests/electron/test_loadapp_query.mjs.
 *
 * See issue #851 for the regression this resolves.
 */

/** Loopback URLs ending in `/api` (no trailing slash). Anything else is rejected. */
const TRUSTED_API_RE = /^https?:\/\/(127\.0\.0\.1|localhost):\d+\/api$/;

/** Legacy fallback for manual `file://.../index.html` opens against a dev backend. */
const LEGACY_FALLBACK = 'http://localhost:4200/api';

export function getApiBase(): string {
    if (typeof window === 'undefined') return '/api';

    if (window.location.protocol === 'file:') {
        const fromQuery = new URLSearchParams(window.location.search).get('api');
        if (fromQuery && TRUSTED_API_RE.test(fromQuery)) return fromQuery;
        // Untrusted or missing — fall back to legacy default. Keeps
        // manual `file://.../index.html` opens working against a dev
        // backend on the historical port; rejects attacker URLs.
        return LEGACY_FALLBACK;
    }

    return '/api';
}
