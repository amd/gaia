// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A message's hover actions (Resend, Copy, Delete) hang below it, over the next
 * message. The next message comes later in the page and paints on top, so
 * without a lift a click on Resend landed on the following answer instead.
 */

import { describe, it, expect } from 'vitest';
import bubbleCss from '../MessageBubble.css?raw';

const flat = bubbleCss.replace(/\s+/g, ' ');

describe('MessageBubble hover actions stay clickable', () => {
    it('hang below the message (the overlap this guards)', () => {
        expect(flat).toMatch(/\.msg-actions \{[^}]*position: absolute;[^}]*bottom: -\d+px;/);
    });

    it('lift the message being hovered or focused above its neighbours', () => {
        expect(flat).toMatch(/\.msg:hover, \.msg:focus-within \{ z-index: 1; \}/);
    });
});
