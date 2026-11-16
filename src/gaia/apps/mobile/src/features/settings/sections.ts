// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ComponentType } from 'react';

import type { LucideIcon } from '@/ui/icons';
import { Bell, Brain, Clock, Cpu, Info, ListChecks, Lock, MessageSquare, Plug, ShieldQuestion, SlidersHorizontal, Sparkles, Volume2, Wrench } from '@/ui/icons';

import About from './panes/About';
import Advanced from './panes/Advanced';
import Automations from './panes/Automations';
import Connectors from './panes/Connectors';
import General from './panes/General';
import Memory from './panes/Memory';
import Messaging from './panes/Messaging';
import Model from './panes/Model';
import Notifications from './panes/Notifications';
import Permissions from './panes/Permissions';
import Privacy from './panes/Privacy';
import ReadAloud from './panes/ReadAloud';
import Setup from './panes/Setup';
import Skills from './panes/Skills';

/** The web's settings sections, in its nav order; `voice` is "Read aloud". */
export type SectionId =
    | 'general'
    | 'setup'
    | 'model'
    | 'permissions'
    | 'skills'
    | 'connectors'
    | 'messaging'
    | 'automations'
    | 'memory'
    | 'notifications'
    | 'voice'
    | 'privacy'
    | 'advanced'
    | 'about';

export interface SettingsSectionEntry {
    titleKey: `settings.section.${SectionId}`;
    icon: LucideIcon;
    /** The one place a pane is wired: a set-* task swaps in its pane's import. */
    pane: ComponentType<{ provider?: string }>;
}

export const SETTINGS_SECTIONS: Record<SectionId, SettingsSectionEntry> = {
    general: { titleKey: 'settings.section.general', icon: SlidersHorizontal, pane: General },
    setup: { titleKey: 'settings.section.setup', icon: ListChecks, pane: Setup },
    model: { titleKey: 'settings.section.model', icon: Cpu, pane: Model },
    permissions: { titleKey: 'settings.section.permissions', icon: ShieldQuestion, pane: Permissions },
    skills: { titleKey: 'settings.section.skills', icon: Sparkles, pane: Skills },
    connectors: { titleKey: 'settings.section.connectors', icon: Plug, pane: Connectors },
    messaging: { titleKey: 'settings.section.messaging', icon: MessageSquare, pane: Messaging },
    automations: { titleKey: 'settings.section.automations', icon: Clock, pane: Automations },
    memory: { titleKey: 'settings.section.memory', icon: Brain, pane: Memory },
    notifications: { titleKey: 'settings.section.notifications', icon: Bell, pane: Notifications },
    voice: { titleKey: 'settings.section.voice', icon: Volume2, pane: ReadAloud },
    privacy: { titleKey: 'settings.section.privacy', icon: Lock, pane: Privacy },
    advanced: { titleKey: 'settings.section.advanced', icon: Wrench, pane: Advanced },
    about: { titleKey: 'settings.section.about', icon: Info, pane: About },
};

export const SECTION_ORDER = Object.keys(SETTINGS_SECTIONS) as SectionId[];

export function isSectionId(value: unknown): value is SectionId {
    return typeof value === 'string' && Object.prototype.hasOwnProperty.call(SETTINGS_SECTIONS, value);
}
