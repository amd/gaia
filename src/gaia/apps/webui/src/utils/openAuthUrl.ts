// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Open a sign-in URL in the user's real browser: the system browser through the
 * Electron bridge, else a new tab (the dev server in a plain browser).
 */
export function openAuthUrl(url: string): void {
    const bridge = (window as unknown as { gaia?: { openExternal?: (url: string) => void } }).gaia;
    if (bridge?.openExternal) bridge.openExternal(url);
    else window.open(url, '_blank', 'noopener');
}
