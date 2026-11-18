// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * On macOS the desktop app has no title bar, so the traffic lights float over
 * the top-left corner (services/window-frame.cjs puts them at x 16). Whatever
 * sits in that corner must move out of their way: the collapsed sidebar rail,
 * which is narrower than the lights, and the floating sidebar button that
 * narrow windows show.
 *
 * jsdom does no layout, so this reads the shipped stylesheet directly.
 */

import { describe, expect, it } from 'vitest';
import indexCss from '../index.css?raw';

/** The traffic lights end near x 68; nothing clickable may start before this. */
const LIGHTS_END = 76;

function decls(selector: string): string {
    const css = indexCss.replace(/\/\*[\s\S]*?\*\//g, '');
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const m = css.match(new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`));
    expect(m, `no rule for ${selector}`).not.toBeNull();
    return m![1];
}

function px(body: string, prop: string): number {
    const m = body.match(new RegExp(`(?:^|[;\\s])${prop}\\s*:\\s*(\\d+)px`));
    expect(m, `no ${prop} in px in: ${body}`).not.toBeNull();
    return Number(m![1]);
}

describe('macOS traffic light clearance', () => {
    it('moves the collapsed rail top row below the lights', () => {
        const body = decls(':root[data-titlebar="mac"] .sb.is-collapsed .sb-top');
        expect(body).toMatch(/padding-top:\s*var\(--titlebar-h\)/);
    });

    it('starts the chat header after the lights when the rail is collapsed', () => {
        const body = decls(':root[data-titlebar="mac"]:not([dir="rtl"]):has(.sb.is-collapsed) .task-header');
        const railWidth = 56;
        expect(railWidth + px(body, 'padding-left')).toBeGreaterThan(LIGHTS_END);
    });

    it('puts the narrow-window sidebar button after the lights, and top rows after it', () => {
        const start = px(decls(':root[data-titlebar="mac"]:not([dir="rtl"]) .sidebar-toggle'), 'inset-inline-start');
        expect(start).toBeGreaterThanOrEqual(LIGHTS_END);
        const width = px(decls('.sidebar-toggle'), 'width');
        const clearance = px(decls(':root[data-titlebar="mac"]:not([dir="rtl"])'), '--sidebar-toggle-clearance');
        expect(clearance).toBeGreaterThan(start + width);
    });
});
