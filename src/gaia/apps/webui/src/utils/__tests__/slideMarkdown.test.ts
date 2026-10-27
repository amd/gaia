// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { parseSlides, plainText } from '../slideMarkdown';

const STAGES = '# Deck\n\n## Personal AI grows\nDiagram: process\n### Assist\n- Summarize email\n### Delegate\n- Plan the day\n### Autonomy\n- Deep research\nAxis: More compute\n';

describe('parseSlides', () => {
    it('draws a one-slide deck as its slide, not its cover', () => {
        const [slide, ...rest] = parseSlides(STAGES);
        expect(rest).toHaveLength(0);
        expect(slide.layout).toBe('process');
        expect(slide.items.map((i) => i.title)).toEqual(['Assist', 'Delegate', 'Autonomy']);
        expect(slide.axis).toBe('More compute');
    });

    it('shows the cover first while the slides are still being written', () => {
        expect(parseSlides('# Deck\nBoard | 2026\n## Pers')[0].layout).toBe('bullets');
        expect(parseSlides('# Deck\nBoard | 2026')[0]).toMatchObject({ layout: 'cover', subtitle: 'Board | 2026' });
    });

    it('reads a diagram written inside a code block, closed or not', () => {
        const fenced = STAGES.replace('Diagram: process\n', '```diagram process\n');
        expect(parseSlides(fenced)[0].layout).toBe('process');
        expect(parseSlides(`${fenced}\`\`\`\n`)[0].items).toHaveLength(3);
    });

    it('undoes line breaks the model escaped twice', () => {
        expect(parseSlides(STAGES.replace(/\n/g, '\\n'))[0].layout).toBe('process');
    });
});

describe('plainText', () => {
    it('turns LaTeX arrows into symbols and leaves prices alone', () => {
        expect(plainText('A $\\rightarrow$ B, $5 and $10')).toBe('A → B, $5 and $10');
    });
});
