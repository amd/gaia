// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SectionId } from './sections';

export interface SettingsHref {
    pathname: '/settings/[section]';
    params: { section: SectionId; target?: string; from?: SectionId };
}

export function settingsHref(section: SectionId, target?: string, from?: SectionId): SettingsHref {
    return {
        pathname: '/settings/[section]',
        params: { section, ...(target ? { target } : null), ...(from ? { from } : null) },
    };
}

