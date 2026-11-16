// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { router } from 'expo-router';

import type { SectionId } from './sections';
import { settingsHref, type SettingsHref } from './settingsHref';

/**
 * Opens a settings section, optionally scrolled to a named anchor (see `useSettingsTarget`).
 * `provider` pre-selects a provider tab in the Model pane. `from` is the section the jump came from, which shows "Back to {section}". Pushes, so back returns to the caller.
 */
export function openSettings(
    section: SectionId,
    target?: string,
    from?: SectionId,
    nav: { push(href: SettingsHref): void } = router,
    provider?: string,
): void {
    nav.push(settingsHref(section, target, from, provider));
}
