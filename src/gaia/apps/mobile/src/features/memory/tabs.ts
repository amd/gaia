// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ComponentType } from 'react';

import type { MessageKey } from '@webui/i18n/registry';

import type { MemoryNotice } from './useMemoryNotice';
import { AboutPane } from './panes/AboutPane';
import { AdvancedPane } from './panes/AdvancedPane';
import { GoalsPane } from './panes/GoalsPane';
import { ProfilePane } from './panes/ProfilePane';

export type MemoryTabKey = 'about' | 'goals' | 'profile' | 'advanced';

export interface MemoryPaneProps {
    /** Shows a result in the screen's one notice slot. */
    notify: (notice: MemoryNotice) => void;
    /** Whether memory is on; undefined until the settings have loaded. */
    memoryEnabled: boolean | undefined;
    /** Opens the Advanced tab, where memory is switched on. */
    onOpenAdvanced: () => void;
    /** Opens a chat; absent where the screen can't navigate (the dev gallery). */
    onOpenChat?: (sessionId: string) => void;
}

export interface MemoryTab {
    key: MemoryTabKey;
    label: MessageKey;
    /** The one place a pane is wired: a mem-* task swaps its entry's `Pane` for the real one. Only the open tab is mounted. */
    Pane: ComponentType<MemoryPaneProps>;
}

/** The web's tabs, in its order; the first is the default. */
export const MEMORY_TABS: readonly MemoryTab[] = [
    { key: 'about', label: 'mem.tab.about', Pane: AboutPane },
    { key: 'goals', label: 'mem.tab.goals', Pane: GoalsPane },
    { key: 'profile', label: 'mem.tab.profile', Pane: ProfilePane },
    { key: 'advanced', label: 'mem.tab.advanced', Pane: AdvancedPane },
];
