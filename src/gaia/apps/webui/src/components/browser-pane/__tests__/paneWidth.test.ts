// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it } from 'vitest';
import { CHAT_MIN_WIDTH, PANE_MIN_WIDTH, clampPaneWidth } from '../../../stores/browserPaneStore';
import indexCss from '../../../styles/index.css?raw';

function layout(windowWidth: number, sidebarWidth: number) {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: windowWidth });
    const main = document.createElement('div');
    main.className = 'main-content';
    main.getBoundingClientRect = () => ({ left: sidebarWidth } as DOMRect);
    document.body.appendChild(main);
}

describe('clampPaneWidth', () => {
    afterEach(() => {
        document.body.innerHTML = '';
    });

    it('leaves the chat its minimum beside an open sidebar', () => {
        layout(1400, 260);
        expect(clampPaneWidth(900)).toBe(1400 - 260 - CHAT_MIN_WIDTH);
    });

    it('gives the pane more room when the sidebar is collapsed', () => {
        layout(1400, 56);
        expect(clampPaneWidth(900)).toBe(1400 - 56 - CHAT_MIN_WIDTH);
    });

    it('never goes below the pane minimum in a small window', () => {
        layout(800, 260);
        expect(clampPaneWidth(900)).toBe(PANE_MIN_WIDTH);
    });

    it('keeps a width that already fits', () => {
        layout(1600, 260);
        expect(clampPaneWidth(500)).toBe(500);
    });

    it('matches the minimums the stylesheet lays the columns out with', () => {
        expect(indexCss).toContain(`--chat-min-w: ${CHAT_MIN_WIDTH}px;`);
        expect(indexCss).toContain(`--pane-min-w: ${PANE_MIN_WIDTH}px;`);
    });
});
