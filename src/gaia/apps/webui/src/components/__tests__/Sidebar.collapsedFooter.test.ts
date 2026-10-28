// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The chat list is what fills the sidebar's height and pushes the footer down.
 * The collapsed sidebar hides that list, so the footer must pin itself to the
 * bottom or Notifications, Mobile access and Settings float up mid-sidebar.
 */

import { describe, it, expect } from 'vitest';
import sidebarCss from '../Sidebar.css?raw';

const rule = (selector: string) => {
    const i = sidebarCss.indexOf(`${selector} {`);
    expect(i, `${selector} rule missing`).toBeGreaterThanOrEqual(0);
    return sidebarCss.slice(i, sidebarCss.indexOf('}', i));
};

describe('collapsed sidebar footer', () => {
    it('stays pinned to the bottom without the chat list', () => {
        expect(rule('.sb.is-collapsed .sb-bottom')).toMatch(/margin-top:\s*auto/);
    });
});
