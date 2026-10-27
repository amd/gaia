// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Reads the slide-decks skill's Markdown (hub/skills/slide-decks/references/
 * markdown-format.md) into slides the session panel can draw, including
 * Markdown that is still being written.
 */

export interface SlideItem {
    title: string;
    bullets: string[];
}

export type SlideLayout = 'cover' | 'bullets' | 'columns' | 'process' | 'boxes' | 'table' | 'chart';

export interface DraftSlide {
    layout: SlideLayout;
    title: string;
    subtitle?: string;
    bullets: string[];
    items: SlideItem[];
    axis?: string;
    rows?: string[][];
    chart?: string;
    source?: string;
}

const LATEX: Record<string, string> = {
    rightarrow: '→', to: '→', longrightarrow: '→', Rightarrow: '⇒', leftarrow: '←',
    uparrow: '↑', downarrow: '↓', Uparrow: '⇑', Downarrow: '⇓', times: '×',
    le: '≤', leq: '≤', ge: '≥', geq: '≥', approx: '≈', pm: '±', cdot: '·',
};
const LATEX_RE = new RegExp(`\\\\(${Object.keys(LATEX).sort((a, b) => b.length - a.length).join('|')})(?![A-Za-z])`, 'g');

/** Model text as the deck shows it: LaTeX arrows as symbols, no Markdown emphasis. */
export function plainText(text: string): string {
    return text
        .replace(/\$([^$\n]{1,80})\$/g, (m, inner: string) => (inner.includes('\\') ? inner : m))
        .replace(/\\text\{([^{}]*)\}/g, '$1')
        .replace(LATEX_RE, (_m, name: string) => LATEX[name])
        .replace(/\*\*|__/g, '')
        .replace(/\s+/g, ' ')
        .trim();
}

function blank(title: string): DraftSlide {
    return { layout: 'bullets', title: plainText(title), bullets: [], items: [] };
}

function finish(slide: DraftSlide, diagram: string): DraftSlide {
    if (slide.layout === 'table' || slide.layout === 'chart' || slide.layout === 'cover') return slide;
    if (diagram || slide.items.length >= 3) {
        slide.layout = diagram === 'process' ? 'process' : 'boxes';
    } else if (slide.items.length === 2) {
        slide.layout = 'columns';
    }
    return slide;
}

/** Parse *md*; the last slide may be cut off mid-line. */
export function parseSlides(md: string): DraftSlide[] {
    // The model sometimes escapes its line breaks twice; the deck tool undoes it too.
    if (!md.includes('\n') && md.includes('\\n')) md = md.replace(/\\n/g, '\n');
    const slides: DraftSlide[] = [];
    let cur: DraftSlide | null = null;
    let diagram = '';
    let fence: string | null = null;
    let notes = false;
    const close = () => {
        if (cur) slides.push(finish(cur, diagram));
        cur = null;
        diagram = '';
        notes = false;
    };
    for (const raw of md.replace(/\r\n?/g, '\n').split('\n')) {
        const line = raw.trimEnd();
        const s = line.trim();
        if (fence !== null) {
            if (s.startsWith('```')) fence = null;
            continue;
        }
        // A diagram written in Markdown inside a code block reads as the slide's own lines.
        const dm = s.match(/^```\s*diagram\b[\s:]*([\w -]*)$/i);
        if (dm) { diagram = dm[1].trim().toLowerCase() || 'boxes'; continue; }
        if (s === '```' && diagram) continue;
        if (s.startsWith('```')) {
            fence = s;
            if (cur && /^```\s*chart/i.test(s)) {
                cur.layout = 'chart';
                cur.chart = s.replace(/^```\s*chart\s*/i, '') || 'chart';
            }
            continue;
        }
        if (/^#\s/.test(s)) {
            close();
            cur = { ...blank(s.slice(2)), layout: 'cover' };
            continue;
        }
        if (/^##\s/.test(s)) {
            close();
            cur = blank(s.replace(/^##\s+/, ''));
            continue;
        }
        if (!cur || !s || notes) continue;
        if (/^notes\s*:/i.test(s)) { notes = true; continue; }
        if (/^must show\s*:/i.test(s)) continue;
        let m = s.match(/^(?:diagram|layout)\s*:\s*([\w -]+)$/i);
        if (m) { diagram = m[1].trim().toLowerCase(); continue; }
        m = s.match(/^(?:x[\s_-]*)?axis\s*:\s*(.+)$/i);
        if (m) { cur.axis = plainText(m[1]); continue; }
        m = s.match(/^source\s*:\s*(.+)$/i);
        if (m) { cur.source = plainText(m[1]); continue; }
        if (/^###\s/.test(s)) {
            cur.items.push({ title: plainText(s.replace(/^###\s+/, '')), bullets: [] });
            continue;
        }
        if (s.startsWith('|')) {
            if (/^\|[\s:|-]+\|?$/.test(s)) continue;
            cur.layout = 'table';
            (cur.rows ??= []).push(s.replace(/^\||\|$/g, '').split('|').map((c) => plainText(c)));
            continue;
        }
        m = line.match(/^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$/);
        const text = plainText(m ? m[2] : s);
        if (!text) continue;
        if (cur.layout === 'cover') {
            cur.subtitle = cur.subtitle ? `${cur.subtitle} ${text}` : text;
            continue;
        }
        const item = cur.items[cur.items.length - 1];
        if (item) item.bullets.push(text);
        else if (diagram && m && m[1].length < 2) cur.items.push({ title: text, bullets: [] });
        else if (diagram && cur.items.length) cur.items[cur.items.length - 1].bullets.push(text);
        else cur.bullets.push(text);
    }
    close();
    // A one-slide deck is built without its cover; the preview shows what is built.
    const content = slides.filter((s) => s.layout !== 'cover');
    return content.length === 1 ? content : slides;
}
