// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * On macOS the desktop app draws its red, yellow and green buttons at the top
 * left (trafficLightPosition {x: 16, y: 16} in services/window-frame.cjs, about
 * 54px wide and 14px tall). With the sidebar collapsed nothing else may sit
 * under them: not the rail's icons, the floating toggle, or the chat title.
 *
 * jsdom does no layout, so this reads the shipped stylesheet directly.
 */

import { describe, expect, it } from 'vitest';
import indexCss from '../index.css?raw';
import sidebarRaw from '../../components/Sidebar.css?raw';

const LIGHTS_RIGHT = 16 + 54;
const LIGHTS_BOTTOM = 16 + 14;

const css = indexCss.replace(/\/\*[\s\S]*?\*\//g, '');
const sidebarCss = sidebarRaw.replace(/\/\*[\s\S]*?\*\//g, '');

function decls(selector: string, within = css): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const m = within.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
    if (!m) throw new Error(`no rule for ${selector}`);
    return m[1];
}

function px(text: string, prop: string): number[] {
    const m = text.match(new RegExp(`(?:^|[;\\s])${prop}\\s*:\\s*([^;]+)`));
    if (!m) throw new Error(`no ${prop} in: ${text}`);
    return m[1].trim().split(/\s+/).map((v) => parseFloat(v));
}

function narrowMac(): string {
    const at = css.search(/@media \(max-width: 768px\)\s*\{\s*:root\[data-titlebar="mac"\]/);
    expect(at, 'no narrow-window rules for the macOS title bar').toBeGreaterThan(-1);
    return css.slice(at, css.indexOf('}\n}', at) + 3);
}

describe('macOS window buttons with the sidebar collapsed', () => {
    it('the collapsed rail starts its icons below the buttons', () => {
        const [top] = px(decls(':root[data-titlebar="mac"] .sb.is-collapsed .sb-top'), 'padding');
        expect(top).toBeGreaterThan(LIGHTS_BOTTOM);
    });

    it('the chat title starts right of the buttons beside the collapsed rail', () => {
        const [rail] = px(decls('.sb.is-collapsed', sidebarCss), 'width');
        const [left] = px(decls(':root[data-titlebar="mac"] .app:has(.sb.is-collapsed) .task-header'), 'padding-left');
        expect(rail + left).toBeGreaterThan(LIGHTS_RIGHT + 12);
    });

    it('the floating toggle starts right of the buttons', () => {
        const [left] = px(decls(':root[data-titlebar="mac"] .sidebar-toggle', narrowMac()), 'left');
        expect(left).toBeGreaterThan(LIGHTS_RIGHT);
    });

    it('top rows, the chat title among them, start right of the toggle', () => {
        const block = narrowMac();
        const [left] = px(decls(':root[data-titlebar="mac"] .sidebar-toggle', block), 'left');
        const [width] = px(decls('.sidebar-toggle'), 'width');
        const [clearance] = px(decls(':root[data-titlebar="mac"]', block), '--sidebar-toggle-clearance');
        expect(clearance).toBeGreaterThan(left + width);
    });
});
