// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { linkifyPaths } from '../AgentActivity';

vi.mock('../../services/api');

function renderText(text: string) {
    const { container } = render(<div>{linkifyPaths(text)}</div>);
    const links = [...container.querySelectorAll('.tool-result-path')];
    return { container, links: links.map((l) => l.textContent) };
}

describe('linkifyPaths', () => {
    it('keeps a quoted Windows path in its sentence, quotes outside the link', () => {
        const text = "error: Access denied: 'C:\\Users\\Kalin\\gaia-rc-sbx\\ui_notes.txt' is not in allowed paths.";
        const { container, links } = renderText(text);

        expect(links).toEqual(['C:\\Users\\Kalin\\gaia-rc-sbx\\ui_notes.txt']);
        expect(container.textContent).toBe(text);
        expect(container.querySelector('.tool-result-path')?.getAttribute('title'))
            .toBe('Open: C:\\Users\\Kalin\\gaia-rc-sbx\\ui_notes.txt');
    });

    it('links a quoted path with spaces as one path', () => {
        const text = 'Saved to "C:\\Users\\me\\My Documents\\report final.docx".';
        const { container, links } = renderText(text);

        expect(links).toEqual(['C:\\Users\\me\\My Documents\\report final.docx']);
        expect(container.textContent).toBe(text);
    });

    it('still links unquoted paths and trims trailing brackets', () => {
        const text = 'Indexed (C:\\Users\\me\\notes.txt) and C:\\data\\b.csv done';
        const { container, links } = renderText(text);

        expect(links).toEqual(['C:\\Users\\me\\notes.txt', 'C:\\data\\b.csv']);
        expect(container.textContent).toBe(text);
    });

    it('leaves text without paths alone', () => {
        expect(linkifyPaths('no paths here')).toBe('no paths here');
    });
});
