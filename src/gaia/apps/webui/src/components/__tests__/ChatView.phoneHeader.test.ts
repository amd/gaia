// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * At phone width the five header icons left the chat title about nine letters.
 * The title now gets its own row there.
 */

import { describe, it, expect } from 'vitest';
import chatCss from '../ChatView.css?raw';

const flat = chatCss.replace(/\s+/g, ' ');

describe('ChatView header on a phone', () => {
    it('wraps so the title is not squeezed beside the icons', () => {
        const phone = flat.match(/@media \(max-width: 480px\) \{ \.task-header \{([^}]*)\}.*?\.task-header-left \{([^}]*)\}/);
        expect(phone).not.toBeNull();
        expect(phone![1]).toMatch(/flex-wrap: wrap;/);
        expect(phone![2]).toMatch(/flex: 1 1 100%;/);
    });
});
