// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useSyncExternalStore } from 'react';
import { openLayerCount, subscribeLayers } from './useEscapeToClose';

/**
 * True while any app overlay is open: a dialog, menu, popover or the narrow
 * window's session panel. Derived from the escape-layer registry every one of
 * them already joins (useEscapeToClose / useEscapeLayer / Popover), so a new
 * overlay cannot forget it. The browser pane hides its native page while this
 * is true, because that page would otherwise paint over the overlay.
 */
export function useAppOverlayOpen(): boolean {
    return useSyncExternalStore(subscribeLayers, () => openLayerCount() > 0);
}
