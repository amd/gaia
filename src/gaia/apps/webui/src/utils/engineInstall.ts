// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Whether the desktop app loaded this page to show its backend install.
 * The query is `INSTALL_SCREEN_QUERY` in services/index-query.cjs; the bridge
 * check keeps it out of a browser, whatever the URL says.
 */
export function isInstallScreen(): boolean {
    return !!window.gaiaInstall && new URLSearchParams(window.location.search).get('engine') === 'install';
}
