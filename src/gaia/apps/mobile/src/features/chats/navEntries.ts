// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey } from '@webui/i18n/registry';

import { Clock, Settings, Target, type LucideIcon } from '@/ui/icons';

export interface NavEntry {
    key: string;
    labelKey: MessageKey;
    icon: LucideIcon;
    /** The drawer route it opens; the entry shows only once that route is declared. */
    route: string;
    /** The web puts Settings in the sidebar footer. */
    footer?: boolean;
}

// The web sidebar's order.
export const NAV_ENTRIES: readonly NavEntry[] = [
    { key: 'schedules', labelKey: 'sidebar.schedules', icon: Clock, route: 'schedules' },
    { key: 'goals', labelKey: 'mem.tab.goals', icon: Target, route: 'goals' },
    { key: 'settings', labelKey: 'sidebar.settings', icon: Settings, route: 'settings', footer: true },
];

export function declaredNavEntries(routeNames: readonly string[], entries: readonly NavEntry[] = NAV_ENTRIES): NavEntry[] {
    return entries.filter((e) => routeNames.includes(e.route));
}
