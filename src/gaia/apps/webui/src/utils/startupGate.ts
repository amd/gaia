// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Whether the startup placeholder should replace the main UI. */
export function shouldShowStartupScreen(
    setupChecking: boolean,
    initialized: boolean,
    mainUiShown: boolean,
): boolean {
    return setupChecking && !initialized && !mainUiShown;
}
