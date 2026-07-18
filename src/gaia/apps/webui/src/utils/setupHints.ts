// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Setup-hint gating for the Welcome screen (#2119).
 *
 * The "First time? Run `gaia init`…" tip used to be gated only on the
 * ``~/.gaia/chat/initialized`` marker file. That marker can be absent on a
 * perfectly working setup (Lemonade installed out-of-band, marker never
 * written), so the tip showed even when the live status probe reported
 * Lemonade running with a model loaded. These helpers gate the tip on the
 * probe (ground truth), not the marker alone.
 */

import type { SystemStatus } from '../types';

/** Live-probe truth: backend reachable, Lemonade up, and a model is loaded. */
export function isSystemReady(status: SystemStatus | null): boolean {
    return status !== null && status.lemonade_running && !!status.model_loaded;
}

/**
 * Show the first-run "install Lemonade + download model" tip only when the
 * backend is reachable, the initialized marker is absent, AND the system is
 * not already demonstrably ready.
 */
export function shouldShowFirstRunTip(status: SystemStatus | null): boolean {
    return status !== null && !status.initialized && !isSystemReady(status);
}

/**
 * Show the "no model loaded" tip when Lemonade is up but no model is loaded,
 * and we're not already showing the first-run tip.
 */
export function shouldShowNoModelTip(status: SystemStatus | null): boolean {
    if (shouldShowFirstRunTip(status)) return false;
    return status !== null && status.lemonade_running && !status.model_loaded;
}
