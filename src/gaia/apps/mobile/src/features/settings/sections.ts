// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ComponentType } from 'react';

import type { LucideIcon } from '@/ui/icons';
import { Bell, Info, SlidersHorizontal } from '@/ui/icons';

import About from './panes/About';
import General from './panes/General';
import Notifications from './panes/Notifications';

/** The web's settings sections the phone shows, in its nav order. */
export type SectionId = 'general' | 'notifications' | 'about';

export interface SettingsSectionEntry {
    titleKey: `settings.section.${SectionId}`;
    icon: LucideIcon;
    pane: ComponentType;
}

export const SETTINGS_SECTIONS: Record<SectionId, SettingsSectionEntry> = {
    general: { titleKey: 'settings.section.general', icon: SlidersHorizontal, pane: General },
    notifications: { titleKey: 'settings.section.notifications', icon: Bell, pane: Notifications },
    about: { titleKey: 'settings.section.about', icon: Info, pane: About },
};

export const SECTION_ORDER = Object.keys(SETTINGS_SECTIONS) as SectionId[];

export function isSectionId(value: unknown): value is SectionId {
    return typeof value === 'string' && Object.prototype.hasOwnProperty.call(SETTINGS_SECTIONS, value);
}
