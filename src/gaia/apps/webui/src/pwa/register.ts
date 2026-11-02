// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

interface RegisterEnv {
    isProduction: boolean;
    protocol: string;
    serviceWorker?: Pick<ServiceWorkerContainer, 'register'>;
}

/**
 * Register the service worker that makes the Agent UI installable from a
 * browser (mobile access). Skipped in the dev server, and in the desktop app,
 * which loads from file:// where service workers do not exist.
 */
export async function registerServiceWorker(env: RegisterEnv): Promise<boolean> {
    if (!env.isProduction) return false;
    if (env.protocol !== 'https:' && env.protocol !== 'http:') return false;
    // Browsers expose no service workers outside a secure context (plain http to a LAN IP).
    if (!env.serviceWorker) return false;
    try {
        await env.serviceWorker.register('./sw.js', { scope: './' });
        return true;
    } catch (err) {
        console.error('[pwa] Service worker registration failed, so the UI is not cached for an installed app:', err);
        return false;
    }
}
