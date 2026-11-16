// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey } from '@webui/i18n/registry';

import { Brain, Clock, FileText, Plug, Settings, Sparkles, type LucideIcon } from '@/ui/icons';

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
    { key: 'memory', labelKey: 'sidebar.memory', icon: Brain, route: 'memory' },
    { key: 'schedules', labelKey: 'sidebar.schedules', icon: Clock, route: 'schedules' },
    { key: 'connectors', labelKey: 'settings.section.connectors', icon: Plug, route: 'connectors' },
    { key: 'documents', labelKey: 'sidebar.documents', icon: FileText, route: 'documents' },
    { key: 'skills', labelKey: 'settings.section.skills', icon: Sparkles, route: 'skills' },
    { key: 'settings', labelKey: 'sidebar.settings', icon: Settings, route: 'settings', footer: true },
];

export function declaredNavEntries(routeNames: readonly string[], entries: readonly NavEntry[] = NAV_ENTRIES): NavEntry[] {
    return entries.filter((e) => routeNames.includes(e.route));
}
