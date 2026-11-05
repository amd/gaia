// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Layout rules for a right-to-left language, checked on the real DOM, and the
 * same rules in English, where an Arabic name must not flip its row.
 *
 * These read geometry, not pixels, so they need no baseline and run on any OS.
 * A pass doesn't replace looking at the screen: see the `reviewing-agent-ui` skill.
 *
 *   npm run test:rtl
 */

import { expect, test, type Page } from '@playwright/test';

import { ANSWER_SESSION, MODEL_LIST, NOW, SYSTEM_STATUS, mockApi, useLocale, useTheme, withConnectors, withDesktopBridges, withMemory, withModelPage, withSettingsPanes, type MemoryState } from './fixtures';
import { NARROW_SCENES, SETUP_SCENES, scene, withSetup } from './setup-scenes';

test.use({ viewport: { width: 1400, height: 900 } });

/** Arabic names in the session panel's Environment list. */
const ARABIC_FOLDER = '/home/you/projects/تقارير';
const ARABIC_FILE = '/home/you/projects/gaia/ملاحظات الاجتماع.md';

async function withArabicEnvironment(page: Page): Promise<void> {
    await page.route(`**/api/sessions/${ANSWER_SESSION}/environment`, (route) =>
        route.fulfill({
            json: {
                session_id: ANSWER_SESSION,
                folders: [
                    { path: '/home/you/projects/gaia', kind: 'folder', source: 'working_folder', removable: false },
                    { path: ARABIC_FOLDER, kind: 'folder', source: 'added', removable: true },
                    { path: ARABIC_FILE, kind: 'file', source: 'approved', removable: true },
                ],
                removed: [], internet: true, enforced: true,
            },
        }),
    );
}

async function open(page: Page, hash = '', locale = 'ar', before?: (page: Page) => Promise<void>, theme: 'light' | 'dark' = 'light'): Promise<void> {
    await page.clock.setFixedTime(NOW);
    await mockApi(page);
    await before?.(page);
    await useTheme(page, theme);
    await useLocale(page, locale);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`/${hash}`);
    await page.evaluate(() => document.fonts.ready);
}

/** Every rule break on the page, as readable lines. */
const audit = (page: Page): Promise<string[]> =>
    page.evaluate(() => {
        const problems: string[] = [];
        const label = (el: Element) =>
            `<${el.tagName.toLowerCase()} class="${String(el.className).slice(0, 40)}"> "${(el.textContent ?? '').trim().slice(0, 40)}"`;
        const shown = (el: Element) => {
            const box = el.getBoundingClientRect();
            return box.width > 0 && box.height > 0 && el.checkVisibility();
        };
        // A row that scrolls sideways by design, like the setup rail on a phone.
        const inSideScroller = (el: Element) => {
            for (let at = el.parentElement; at && at !== document.body; at = at.parentElement) {
                if (['auto', 'scroll'].includes(getComputedStyle(at).overflowX) && at.scrollWidth > at.clientWidth) return true;
            }
            return false;
        };
        const latin = /[A-Za-z]/;
        const arabic = /[\u0600-\u06ff]/;

        if (document.documentElement.scrollWidth > innerWidth) problems.push('the page scrolls sideways');

        for (const el of document.querySelectorAll('body *')) {
            if (!shown(el)) continue;
            const box = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            if ((box.right > innerWidth + 1 || box.left < -1) && style.position !== 'fixed' && !el.closest('svg') && !inSideScroller(el)) {
                problems.push(`off screen: ${label(el)}`);
            }

            const text = el.textContent?.trim() ?? '';
            if (el.querySelector(':scope > :not(i18n-label)') || !text) continue;

            // An English name cut off in rtl keeps its end and loses its start.
            if (style.textOverflow === 'ellipsis' && style.direction === 'rtl' && latin.test(text) && !arabic.test(text)) {
                problems.push(`Latin text truncates from its start: ${label(el)}`);
            }

            // dir="auto" turns a block ltr, and start then means the far edge of the row.
            const block = style.display === 'block' || style.display === 'flex';
            if (block && style.direction === 'ltr' && document.documentElement.dir === 'rtl'
                && (style.textAlign === 'start' || style.textAlign === 'left')) {
                const range = document.createRange();
                range.selectNodeContents(el);
                const gap = box.right - range.getBoundingClientRect().right;
                if (gap > 24 && !el.closest('pre, code, input, textarea, .md-content')) {
                    problems.push(`Latin text sits ${Math.round(gap)}px off the right edge: ${label(el)}`);
                }
            }

            // The mirror in an ltr UI: an Arabic name with dir="auto" turns rtl, and
            // start then means the right edge of a row that reads from the left.
            if (block && style.direction === 'rtl' && document.documentElement.dir !== 'rtl'
                && (style.textAlign === 'start' || style.textAlign === 'right')) {
                const range = document.createRange();
                range.selectNodeContents(el);
                const gap = range.getBoundingClientRect().left - box.left;
                if (gap > 24 && !el.closest('pre, code, input, textarea, .md-content')) {
                    problems.push(`Arabic text sits ${Math.round(gap)}px off the left edge: ${label(el)}`);
                }
            }

            // Bare "<number> MB" inside an rtl run renders as "MB <number>".
            if (style.direction === 'rtl' && /^[\d.,]+ (B|KB|MB|GB)$/.test(text) && el.tagName !== 'BDI') {
                problems.push(`a size needs <bdi>: ${label(el)}`);
            }
        }

        // Arabic letters join: a monospace face or added tracking pulls a word apart into
        // separate letters. Only the stack's Arabic fallback matters; Latin may stay mono.
        const ARABIC_FACE = /Segoe UI|Noto Sans Arabic|Noto Naskh Arabic|Tahoma|Arial|Helvetica|sans-serif|serif|system-ui|-apple-system/i;
        const textWalker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        const seen = new Set<string>();
        for (let node = textWalker.nextNode(); node; node = textWalker.nextNode()) {
            const el = node.parentElement;
            if (!el || !arabic.test(node.textContent ?? '') || !shown(el) || seen.has(label(el))) continue;
            const style = getComputedStyle(el);
            const families = style.fontFamily.split(',').map((f) => f.trim().replace(/^["']|["']$/g, ''));
            const fallback = families.find((f) => f === 'monospace' || ARABIC_FACE.test(f));
            if (fallback === 'monospace') problems.push(`Arabic in a monospace font: ${label(el)}`);
            if (style.letterSpacing !== 'normal' && parseFloat(style.letterSpacing) !== 0) problems.push(`Arabic letter-spaced ${style.letterSpacing}: ${label(el)}`);
            seen.add(label(el));
        }

        // The English of a label is quieter than its translation, and still readable.
        const paint = document.createElement('canvas').getContext('2d', { willReadFrequently: true })!;
        const rgb = (color: string): number[] => {
            paint.clearRect(0, 0, 1, 1);
            paint.fillStyle = color;
            paint.fillRect(0, 0, 1, 1);
            return [...paint.getImageData(0, 0, 1, 1).data];
        };
        const luminance = (c: number[]) => {
            const [r, g, b] = c.slice(0, 3).map((v) => {
                const s = v / 255;
                return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
            });
            return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        };
        const backdrop = (el: Element) => {
            for (let at: Element | null = el; at; at = at.parentElement) {
                const color = rgb(getComputedStyle(at).backgroundColor);
                if (color[3] === 255) return color;
            }
            return [255, 255, 255, 255];
        };
        const contrast = (a: number[], b: number[]) => {
            const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
            return (hi + 0.05) / (lo + 0.05);
        };
        for (const gloss of document.querySelectorAll('.i18n-gloss')) {
            if (!shown(gloss)) continue;
            const style = getComputedStyle(gloss);
            const parent = getComputedStyle(gloss.parentElement!);
            const ratio = contrast(rgb(style.color), backdrop(gloss));
            if (ratio < 4.5) problems.push(`gloss reads at ${ratio.toFixed(2)}:1: ${label(gloss)}`);
            if (parseFloat(style.fontSize) > parseFloat(parent.fontSize)) problems.push(`gloss is larger than its label: ${label(gloss)}`);
            if (parseFloat(style.fontSize) >= parseFloat(parent.fontSize) && parseFloat(parent.fontSize) > 13.75) {
                problems.push(`gloss is as large as its label: ${label(gloss)}`);
            }
            if (Number(style.fontWeight) > 400) problems.push(`gloss is bold: ${label(gloss)}`);
        }

        // A button cut off by the window's edge can't be read or pressed; one in
        // a strip that scrolls sideways is reached by scrolling it.
        const inScroller = (el: Element) => {
            for (let p = el.parentElement; p; p = p.parentElement) {
                const x = getComputedStyle(p).overflowX;
                // A strip: it scrolls sideways and has nothing to scroll down to.
                const strip = p.scrollWidth > p.clientWidth && p.scrollHeight <= p.clientHeight + 1;
                if ((x === 'auto' || x === 'scroll') && strip) return true;
            }
            return false;
        };
        // The part of a button's text a person sees: each text node clipped by every box that hides
        // its overflow up to the button, so an ellipsis-cut label isn't measured at full length.
        const visibleTextBox = (button: Element) => {
            let left = Infinity;
            let right = -Infinity;
            const walker = document.createTreeWalker(button, NodeFilter.SHOW_TEXT);
            for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                if (!node.textContent?.trim()) continue;
                const clips: DOMRect[] = [];
                for (let at = node.parentElement; at; at = at.parentElement) {
                    if (getComputedStyle(at).overflowX !== 'visible') clips.push(at.getBoundingClientRect());
                    if (at === button) break;
                }
                const range = document.createRange();
                range.selectNodeContents(node);
                for (const rect of range.getClientRects()) {
                    const l = Math.max(rect.left, ...clips.map((c) => c.left));
                    const rr = Math.min(rect.right, ...clips.map((c) => c.right));
                    if (rr <= l) continue;
                    left = Math.min(left, l);
                    right = Math.max(right, rr);
                }
            }
            return right > left ? { left, right } : null;
        };
        for (const button of document.querySelectorAll('button')) {
            if (!shown(button) || inScroller(button)) continue;
            const r = button.getBoundingClientRect();
            if (r.left < -1 || r.right > window.innerWidth + 1) problems.push(`button runs off the window: ${label(button)}`);
            if (button.scrollWidth > button.clientWidth + 1) problems.push(`label spills out of its button: ${label(button)}`);
            // A filled button whose words reach its edge reads as text spilling out of a pill.
            if (button.textContent?.trim() && rgb(getComputedStyle(button).backgroundColor)[3] > 0) {
                const text = visibleTextBox(button);
                if (text && Math.min(text.left - r.left, r.right - text.right) < 3) problems.push(`label touches its button's edge: ${label(button)}`);
            }
        }

        // An icon drawn over a field must sit in its padding, not on the text, in either direction.
        for (const input of document.querySelectorAll('input:not([type="checkbox"]):not([type="radio"])')) {
            if (!shown(input)) continue;
            const box = input.getBoundingClientRect();
            const s = getComputedStyle(input);
            const textLeft = box.left + parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft);
            const textRight = box.right - parseFloat(s.borderRightWidth) - parseFloat(s.paddingRight);
            for (const icon of input.parentElement?.querySelectorAll(':scope > svg, :scope > .search-icon') ?? []) {
                const r = icon.getBoundingClientRect();
                if (getComputedStyle(icon).position !== 'absolute' || !shown(icon)) continue;
                if (r.right > textLeft + 1 && r.left < textRight - 1 && r.bottom > box.top && r.top < box.bottom) {
                    problems.push(`icon sits on a field's text: ${label(input)}`);
                }
            }
        }

        // A failed reply's text keeps clear of its red bar, on whichever side the bar sits.
        for (const reply of document.querySelectorAll('.msg-error')) {
            const body = reply.querySelector('.msg-body');
            if (!body || !shown(reply)) continue;
            const box = reply.getBoundingClientRect();
            const inner = body.getBoundingClientRect();
            const rtl = getComputedStyle(reply).direction === 'rtl';
            const gap = rtl ? box.right - inner.right : inner.left - box.left;
            if (gap < 8) problems.push(`failed reply's text touches its bar (${Math.round(gap)}px): ${label(reply)}`);
        }

        // An icon and its label need space between them, in either direction.
        for (const button of document.querySelectorAll('button')) {
            const icon = button.querySelector(':scope > svg');
            if (!icon || !shown(button) || !button.textContent?.trim()) continue;
            const iconBox = icon.getBoundingClientRect();
            const range = document.createRange();
            range.selectNodeContents(button);
            const gaps = [...range.getClientRects()]
                .filter((r) => r.left >= iconBox.right - 1 || r.right <= iconBox.left + 1)
                .map((r) => Math.max(r.left - iconBox.right, iconBox.left - r.right));
            if (gaps.length && Math.min(...gaps) < 3) problems.push(`icon touches its label: ${label(button)}`);
        }
        return [...new Set(problems)];
    });

test('new chat', async ({ page }) => {
    await open(page);
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.locator('.sb-location-name')).toBeVisible();
    expect(await audit(page)).toEqual([]);
});

test('finished answer', async ({ page }) => {
    await open(page, `#${ANSWER_SESSION}`);
    await expect(page.getByText('Action items from Aug 28')).toBeVisible();
    expect(await audit(page)).toEqual([]);
});

/** A question and an answer that each name a Windows path with spaces. */
const SPACED_PATH = 'C:\\Users\\test\\Documents\\Q3 report.txt';
const SPACED_ANSWER_PATH = 'C:\\Program Files (x86)\\GAIA\\Q3 summary.md';
/** A business OneDrive folder; markdown used to eat the `\` before `_drafts`. */
const MULTI_SPACE_PATH = 'C:\\Users\\test\\OneDrive - AMD\\_drafts\\plan.md';

async function withSpacedPaths(page: Page): Promise<void> {
    await page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) =>
        route.fulfill({
            json: {
                messages: [
                    { id: 1, session_id: ANSWER_SESSION, role: 'user', content: `Summarize ${SPACED_PATH} and ${MULTI_SPACE_PATH} for me`, created_at: NOW.toISOString(), rag_sources: null },
                    { id: 2, session_id: ANSWER_SESSION, role: 'assistant', content: `Done. I saved the summary to ${SPACED_ANSWER_PATH}.`, created_at: NOW.toISOString(), rag_sources: null },
                ],
            },
        }),
    );
}

for (const locale of ['ar', 'en']) {
    test(`a path with spaces is one chip in the question and the answer (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withSpacedPaths);
        const chips = page.locator('.file-path-link');
        await expect(chips).toHaveText([SPACED_PATH, MULTI_SPACE_PATH, SPACED_ANSWER_PATH]);
        expect(await audit(page)).toEqual([]);
        await page.screenshot({ path: info.outputPath('spaced-paths.png') });
    });
}

/** POST /api/chat/send answers with one "Reading your request" status, then holds the turn open. */
function holdOnReading(): void {
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (!url.includes('/api/chat/send')) return realFetch(input, init);
        const event = { type: 'status', status: 'working', phase: 'reading', message: 'Reading your request' };
        const body = new ReadableStream<Uint8Array>({
            start(controller) {
                controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
            },
        });
        return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
    };
}

for (const [locale, reading] of [['ar', 'جارٍ قراءة طلبك'], ['en', 'Reading your request']] as const) {
    test(`live status line while the answer is prepared (${locale})`, async ({ page }) => {
        await page.addInitScript(holdOnReading);
        await open(page, `#${ANSWER_SESSION}`, locale);
        const box = page.getByRole('textbox').first();
        await box.fill('Summarize the minutes');
        await box.press('Enter');
        const line = page.locator('.thinking-indicator').last();
        await expect(line.locator('.thinking-indicator-text')).toContainText(reading);
        await page.clock.setFixedTime(NOW.getTime() + 12_000);
        const seconds = line.locator('.thinking-indicator-elapsed bdi');
        await expect(seconds).toHaveText(/^\d+s$/);
        expect(await seconds.evaluate((el) => getComputedStyle(el).direction)).toBe('ltr');
        await shot(page, `live-status-${locale}`);
        expect(await audit(page)).toEqual([]);
    });
}

test('documents', async ({ page }) => {
    await open(page);
    await page.locator('.sb').getByRole('button', { name: /Documents/ }).click();
    await expect(page.getByText('q3-board.pdf')).toBeVisible();
    expect(await audit(page)).toEqual([]);
});

test('document being indexed', async ({ page }) => {
    await open(page);
    await page.route('**/api/documents', (route) =>
        route.fulfill({
            json: {
                documents: [
                    {
                        id: 'd1',
                        filename: 'report.pdf',
                        filepath: 'C:/report.pdf',
                        file_size: 10485760,
                        chunk_count: 0,
                        indexing_status: 'indexing',
                        indexed_at: null,
                    },
                ],
                total: 1,
                total_size_bytes: 10485760,
                total_chunks: 0,
            },
        }),
    );
    await page.locator('.sb').getByRole('button', { name: /Documents/ }).click();
    await expect(page.locator('.btn-cancel')).toBeVisible();
    expect(await audit(page)).toEqual([]);
});

const OLD_FILE = { name: 'q1-plan.pdf', path: 'C:\\Users\\me\\Documents\\q1-plan.pdf', size: 2_048_000, at: '2026-03-14T10:00:00Z' };
const ARABIC_MONTH = /يناير|فبراير|مارس|أبريل|مايو|يونيو|يوليو|أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر/;

/** A document and a file from March, old enough to show a date rather than "2h ago". */
async function withOldFile(page: Page): Promise<void> {
    await page.route('**/api/documents', (route) => route.fulfill({
        json: {
            documents: [{
                id: 'd1', filename: OLD_FILE.name, filepath: OLD_FILE.path, file_size: OLD_FILE.size, chunk_count: 40,
                indexed_at: OLD_FILE.at, last_accessed_at: null, sessions_using: 0, indexing_status: 'complete',
            }],
            total: 1, total_size_bytes: OLD_FILE.size, total_chunks: 40,
        },
    }));
    await page.route('**/api/files/browse**', (route) => route.fulfill({
        json: {
            current_path: 'C:\\Users\\me\\Documents', parent_path: 'C:\\Users\\me', quick_links: [],
            entries: [{ name: OLD_FILE.name, path: OLD_FILE.path, type: 'file', size: OLD_FILE.size, extension: '.pdf', modified: OLD_FILE.at }],
        },
    }));
}

for (const locale of ['ar', 'en']) {
    test(`dates read in the interface language, not the PC's (${locale})`, async ({ page }) => {
        const month = locale === 'ar' ? ARABIC_MONTH : /Mar/;
        await open(page, '', locale, withOldFile);
        await page.locator('.sb').getByRole('button', { name: /Documents/ }).click();
        await expect(page.locator('.doc-timestamp')).toHaveText(month);
        await shot(page, `dates-${locale}-documents`);
        expect(await audit(page)).toEqual([]);

        await page.getByRole('button', { name: /Browse files/ }).click();
        await expect(page.locator('.fb-entry-date')).toHaveText(month);
        await shot(page, `dates-${locale}-files`);
        // No audit yet: the file browser's names and sizes still break the RTL rules, tracked separately.
    });

    test(`an answer's timestamp and outputs read in the interface language (${locale})`, async ({ page }) => {
        const month = locale === 'ar' ? ARABIC_MONTH : /Mar/;
        await open(page, `#${ANSWER_SESSION}`, locale, async (p) => {
            await p.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) => route.fulfill({
                json: {
                    messages: [
                        { id: 1, session_id: ANSWER_SESSION, role: 'user', content: 'Summarize the Q1 plan.', created_at: OLD_FILE.at, rag_sources: null },
                        { id: 2, session_id: ANSWER_SESSION, role: 'assistant', content: 'The plan has three goals.', created_at: OLD_FILE.at, rag_sources: null },
                    ],
                    total: 2,
                },
            }));
            await p.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/outputs`, (route) => route.fulfill({
                json: {
                    session_id: ANSWER_SESSION,
                    items: [{
                        id: 'o1', kind: 'export', name: 'q1-summary.csv', path: 'C:\\Users\\me\\Documents\\q1-summary.csv', detail: null,
                        message_id: 2, created_at: '2026-03-14 10:00:00', change: 'created', state: 'present', openable: true, revealable: true,
                    }],
                },
            }));
        });
        await expect(page.locator('.msg-stats-ts')).toHaveText(month);
        await expect(page.getByText('q1-summary.csv')).toBeVisible();
        await expect(page.locator('.sp-list[aria-label]')).toContainText(month);
        await page.getByText('The plan has three goals.').hover();
        await shot(page, `dates-${locale}-answer`);
        expect(await audit(page)).toEqual([]);
    });
}

test('settings', async ({ page }) => {
    await open(page);
    await page.getByRole('button', { name: /Settings/ }).first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    expect(await audit(page)).toEqual([]);
});

test('settings in the dark theme', async ({ page }) => {
    await open(page, '', 'ar', undefined, 'dark');
    await page.getByRole('button', { name: /Settings/ }).first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    expect(await audit(page)).toEqual([]);
});

/** How far each Arabic item name's text sits from the row's left and right edges. */
const arabicNameGaps = (page: Page) =>
    page.locator('.sp-item-name').evaluateAll((els) =>
        els
            .filter((el) => /[\u0600-\u06ff]/.test(el.textContent ?? ''))
            .map((el) => {
                const box = el.getBoundingClientRect();
                const range = document.createRange();
                range.selectNodeContents(el);
                const text = range.getBoundingClientRect();
                return { name: el.textContent, left: Math.round(text.left - box.left), right: Math.round(box.right - text.right) };
            }),
    );

test('session panel with Arabic names', async ({ page }) => {
    await open(page, `#${ANSWER_SESSION}`, 'ar', withArabicEnvironment);
    await expect(page.locator('.sp-item-name', { hasText: 'تقارير' })).toBeVisible();
    const gaps = await arabicNameGaps(page);
    expect(gaps).toHaveLength(2);
    for (const gap of gaps) expect(gap.right, gap.name ?? '').toBeLessThanOrEqual(1);
    expect(await audit(page)).toEqual([]);
});

test('a long hyphenated folder path wraps as one unit, below its Arabic label', async ({ page }) => {
    const path = `C:\\gtest\\h-r6\\${'a-long-folder-name\\'.repeat(4)}.gaia\\documents`;
    await open(page, `#${ANSWER_SESSION}`, 'ar', async (p) => {
        await p.route(`**/api/sessions/${ANSWER_SESSION}/environment`, (route) =>
            route.fulfill({
                json: {
                    session_id: ANSWER_SESSION,
                    folders: [{ path, kind: 'folder', source: 'gaia_documents', removable: false }],
                    removed: [], internet: true, enforced: true,
                },
            }),
        );
    });
    const el = page.locator('.sp-item-path', { hasText: 'h-r6' });
    await expect(el).toBeVisible();
    const layout = await el.evaluate((node) => {
        const box = node.getBoundingClientRect();
        const text = document.createRange();
        text.selectNodeContents(node);
        const label = document.createRange();
        label.setStart(node.parentElement!, 0);
        label.setEndBefore(node);
        return {
            lines: new Set([...text.getClientRects()].map((r) => Math.round(r.top))).size,
            boxes: node.getClientRects().length,
            labelInside: [...label.getClientRects()].some((r) => r.width > 0 && r.bottom > box.top + 1),
        };
    });
    expect(layout.lines, 'the path should be long enough to wrap').toBeGreaterThan(1);
    expect(layout.boxes, 'the path splits into pieces around the label').toBe(1);
    expect(layout.labelInside, 'the label sits between the halves of the path').toBe(false);
    expect(await audit(page)).toEqual([]);
});

test.describe('English', () => {
    test('new chat', async ({ page }) => {
        await open(page, '', 'en');
        await expect(page.locator('html')).not.toHaveAttribute('dir', 'rtl');
        await expect(page.locator('.sb-location-name')).toBeVisible();
        expect(await audit(page)).toEqual([]);
    });

    test('finished answer', async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, 'en');
        await expect(page.getByText('Action items from Aug 28')).toBeVisible();
        expect(await audit(page)).toEqual([]);
    });

    test('documents', async ({ page }) => {
        await open(page, '', 'en');
        await page.locator('.sb').getByRole('button', { name: /Documents/ }).click();
        await expect(page.getByText('q3-board.pdf')).toBeVisible();
        expect(await audit(page)).toEqual([]);
    });

    test('settings', async ({ page }) => {
        await open(page, '', 'en');
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await expect(page.getByRole('dialog')).toBeVisible();
        expect(await audit(page)).toEqual([]);
    });

    test('an Arabic name in the session panel stays on the left', async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, 'en', withArabicEnvironment);
        await expect(page.locator('.sp-item-name', { hasText: 'تقارير' })).toBeVisible();
        const gaps = await arabicNameGaps(page);
        expect(gaps).toHaveLength(2);
        for (const gap of gaps) expect(gap.left, gap.name ?? '').toBeLessThanOrEqual(1);
        expect(await audit(page)).toEqual([]);
    });
});

const lastColor = (page: Page): Promise<string> =>
    page.evaluate(() => (window as unknown as { __colors: { color: string }[] }).__colors.at(-1)?.color ?? '');

const brightness = (hex: string): number =>
    [1, 3, 5].reduce((sum, at) => sum + parseInt(hex.slice(at, at + 2), 16), 0);

for (const locale of ['ar', 'en']) {
    test(`settings close button stays pinned while the pane scrolls (${locale})`, async ({ page }) => {
        await open(page, '', locale);
        await page.getByRole('button', { name: /Settings/ }).first().click();
        const dialog = page.getByRole('dialog');
        await dialog.locator('[data-section="advanced"]').click();
        const title = dialog.locator('.settings-pane-title');
        await expect(title).toBeVisible();
        // Advanced fits the dialog until its sections are open.
        await dialog.locator('.settings-disclosure:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));

        const close = dialog.locator('.settings-close');
        const before = await close.boundingBox();
        const titleBefore = await title.boundingBox();
        const dialogBox = await dialog.boundingBox();
        if (!before || !titleBefore || !dialogBox) throw new Error('settings dialog did not lay out');

        // Wheel over the pane, the way a person scrolls, so the rule doesn't name the scroller.
        await page.mouse.move(dialogBox.x + dialogBox.width / 2, dialogBox.y + dialogBox.height / 2);
        await page.mouse.wheel(0, 10_000);
        await expect.poll(async () => (await title.boundingBox())?.y ?? 0).toBeLessThan(titleBefore.y - 100);

        const after = await close.boundingBox();
        if (!after) throw new Error('close button left the layout after scrolling');
        expect(Math.abs(after.x - before.x)).toBeLessThanOrEqual(1);
        expect(Math.abs(after.y - before.y)).toBeLessThanOrEqual(1);
        expect(after.y - dialogBox.y).toBeLessThan(40);
        const onTop = await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest('.settings-close'),
            [after.x + after.width / 2, after.y + after.height / 2]);
        expect(onTop, 'the close button is covered by scrolled content').toBe(true);
    });
}

/** Text left in English in the Arabic UI, outside names, numbers and the English gloss. */
const leftoverEnglish = (root: Page) =>
    root.locator('.setup').evaluate((setup) => {
        const left: string[] = [];
        const walker = document.createTreeWalker(setup, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const parent = node.parentElement!;
            const text = node.textContent?.trim() ?? '';
            if (!/[A-Za-z]{3,}/.test(text) || /[؀-ۿ]/.test(text)) continue;
            if (parent.closest('bdi, .i18n-gloss, pre, code, [dir="ltr"], .setup-result-error')) continue;
            if (parent.checkVisibility()) left.push(text);
        }
        return left;
    });


/** Saves what a test sees when GAIA_VISUAL_SHOTS names a folder, so a person can read it. */
async function shot(page: Page, name: string): Promise<void> {
    const dir = process.env.GAIA_VISUAL_SHOTS;
    if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

interface ModelPageOptions {
    models?: unknown[];
    status?: unknown;
    theme?: 'light' | 'dark';
}

async function openModelPage(page: Page, locale: string, { models = MODEL_LIST, status, theme = 'light' }: ModelPageOptions = {}): Promise<void> {
    await open(page, '', locale, (p) => withModelPage(p, models, SETUP_PLAN, status), theme);
    const sidebar = page.getByRole('button', { name: /Open sidebar/ });
    if (await sidebar.isVisible()) await sidebar.click();
    await page.getByRole('button', { name: /Settings/ }).first().click();
    await page.getByRole('dialog').locator('[data-section="model"]').click();
    await expect(page.locator('.model-in-use-name')).toBeVisible();
}

const scrollPane = (page: Page, to: 'top' | 'bottom') =>
    page.locator('.settings-scroll').evaluate((el, at) => { el.scrollTop = at === 'top' ? 0 : el.scrollHeight; }, to);

const groups = (page: Page) => page.locator('.settings-card .settings-disclosure');

for (const locale of ['ar', 'en']) {
    test.describe(`settings model page (${locale})`, () => {
        test('the model in use and its controls sit above the list', async ({ page }) => {
            await openModelPage(page, locale);
            await expect(page.locator('#ctx-size')).toBeVisible();
            const pane = await page.locator('.settings-scroll').boundingBox();
            const context = await page.locator('#ctx-size').boundingBox();
            expect(context!.y + context!.height).toBeLessThan(pane!.y + pane!.height);
            await expect(page.locator('.settings-disclosure', { hasText: /Available to download/ }).locator('.model-row').first()).toBeHidden();
            await shot(page, `model-${locale}-top`);
            expect(await audit(page)).toEqual([]);
        });

        test('long list, every group and detail open', async ({ page }) => {
            await openModelPage(page, locale);
            const closed = await groups(page).evaluateAll((els) => els.filter((el) => !(el as HTMLDetailsElement).open).length);
            expect(closed).toBeGreaterThan(0);
            await page.locator('.settings-disclosure:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
            await page.locator('.settings-more:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
            await expect(page.getByText('needs 180 GB of memory', { exact: false }).first()).toBeVisible();
            // A size inside a sentence stays whole only inside its own bidi isolate.
            if (locale === 'ar') await expect(page.locator('.model-row-meta', { hasText: 'tok/s' }).first()).toContainText('\u206841 tok/s\u2069');
            await shot(page, `model-${locale}-long`);
            await scrollPane(page, 'bottom');
            await shot(page, `model-${locale}-long-end`);
            expect(await audit(page)).toEqual([]);
        });

        test('nothing downloaded yet: the download group starts open', async ({ page }) => {
            const none = MODEL_LIST.map((m) => ({ ...m, downloaded: false, state: 'not_downloaded' }));
            await openModelPage(page, locale, { models: none });
            await expect(page.locator('.model-row').first()).toBeVisible();
            await shot(page, `model-${locale}-none-downloaded`);
            expect(await audit(page)).toEqual([]);
        });

        test('an empty list says so', async ({ page }) => {
            await openModelPage(page, locale, { models: [] });
            await expect(page.locator('.settings-card .settings-field > .settings-hint').first()).toBeVisible();
            await shot(page, `model-${locale}-empty`);
            expect(await audit(page)).toEqual([]);
        });

        test('downloading: the group opens and the row shows progress', async ({ page }) => {
            const running = MODEL_LIST.map((m, i) => (i === 4
                ? { ...m, download_progress: { state: 'downloading', percent: 38, bytes_done: 1.1e9, bytes_total: 3e9 } } : m));
            await openModelPage(page, locale, { models: running });
            await expect(page.locator('.model-row progress')).toBeVisible();
            await page.locator('.model-row progress').scrollIntoViewIfNeeded();
            await shot(page, `model-${locale}-downloading`);
            expect(await audit(page)).toEqual([]);
        });

        test('setup downloads sit above the list, one row failed', async ({ page }) => {
            await openModelPage(page, locale, { status: SETUP_RUNNING });
            const downloads = page.locator('.models-downloads-slot');
            await expect(downloads.locator('.dl-row.is-failed')).toBeVisible();
            const [slot, first] = await Promise.all([downloads.boundingBox(), groups(page).first().boundingBox()]);
            expect(slot!.y).toBeLessThan(first!.y);
            await downloads.scrollIntoViewIfNeeded();
            await shot(page, `model-${locale}-setup-downloads`);
            expect(await audit(page)).toEqual([]);
        });

        test('a failed link check lists each line', async ({ page }) => {
            await openModelPage(page, locale);
            const add = page.locator('.settings-disclosure', { has: page.locator('#custom-model-url') });
            await add.locator('summary').click();
            await page.locator('#custom-model-url').fill('https://huggingface.co/unsloth/flint-GGUF');
            await add.locator('button[type="submit"]').click();
            await expect(add.locator('.custom-model-checks li.is-failed')).toBeVisible();
            await add.locator('.custom-model-checks li.is-failed').scrollIntoViewIfNeeded();
            await shot(page, `model-${locale}-failed-check`);
            expect(await audit(page)).toEqual([]);
        });

        test('cloud tab keeps the local model in use on screen', async ({ page }) => {
            await openModelPage(page, locale);
            await expect(page.locator('.model-in-use .is-device')).toHaveText('GPU');
            await page.getByRole('radio', { name: /Fireworks AI/ }).click();
            await expect(page.locator('.settings-card .model-row').first()).toBeVisible();
            await expect(page.locator('.model-in-use .is-device')).toHaveText('GPU');
            await shot(page, `model-${locale}-cloud`);
            expect(await audit(page)).toEqual([]);
        });

        test('dark theme', async ({ page }) => {
            await openModelPage(page, locale, { theme: 'dark' });
            await shot(page, `model-${locale}-dark`);
            expect(await audit(page)).toEqual([]);
        });

        test('phone width', async ({ page }) => {
            await page.setViewportSize({ width: 390, height: 844 });
            await openModelPage(page, locale);
            await shot(page, `model-${locale}-phone`);
            await scrollPane(page, 'bottom');
            await shot(page, `model-${locale}-phone-end`);
            expect(await audit(page)).toEqual([]);
        });
    });
}

for (const locale of ['ar', 'en']) {
    for (const section of ['general', 'permissions', 'skills', 'memory', 'notifications', 'privacy', 'advanced']) {
        test(`settings ${section} pane (${locale})`, async ({ page }) => {
            await open(page, '', locale, withSettingsPanes);
            await page.getByRole('button', { name: /Settings/ }).first().click();
            await page.getByRole('dialog').locator(`[data-section="${section}"]`).click();
            await expect(page.locator('.settings-pane-title')).toBeVisible();
            await expect(page.locator('.loading-text, .spin')).toHaveCount(0);
            await shot(page, `pane-${section}-${locale}`);
            expect(await audit(page)).toEqual([]);

            await page.locator('.settings-disclosure:not([open]) > summary, .settings-more:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
            await scrollPane(page, 'bottom');
            await shot(page, `pane-${section}-${locale}-open`);
            expect(await audit(page)).toEqual([]);
        });
    }
}

for (const locale of ['ar', 'en']) {
    test.describe(`settings connectors (${locale})`, () => {
        const openConnectors = async (page: Page, rows?: unknown[]) => {
            await open(page, '', locale, (p) => withConnectors(p, rows));
            await page.getByRole('button', { name: /Settings/ }).first().click();
            await page.getByRole('dialog').locator('[data-section="connectors"]').click();
            await expect(page.locator('.settings-pane-title')).toBeVisible();
            await expect(page.locator('.connectors-loading')).toHaveCount(0);
        };

        test('groups: connected open, the rest closed', async ({ page }) => {
            await openConnectors(page);
            await expect(page.locator('.connector-tile').first()).toBeVisible();
            await expect(page.locator('details[open] .connector-tile')).toHaveCount(3);
            await shot(page, `connectors-${locale}-top`);
            expect(await audit(page)).toEqual([]);
        });

        test('every group and a tile open, with no button inside a button', async ({ page }) => {
            await openConnectors(page);
            await page.locator('.settings-disclosure:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
            await page.locator('.connector-tile-header', { hasText: 'Microsoft' }).click();
            await expect(page.locator('.connector-detail')).toBeVisible();
            await page.locator('.connector-detail').scrollIntoViewIfNeeded();
            await shot(page, `connectors-${locale}-open`);
            await page.locator('.connector-tile-header', { hasText: 'Slack' }).click();
            await expect(page.locator('.oauth-setup-form')).toBeVisible();
            await page.locator('.oauth-setup-form').scrollIntoViewIfNeeded();
            await shot(page, `connectors-${locale}-setup`);
            await expect(page.locator('button button')).toHaveCount(0);
            expect(await audit(page)).toEqual([]);
        });

        test('nothing connected: accounts start open', async ({ page }) => {
            await openConnectors(page, [{ id: 'microsoft', display_name: 'Microsoft', type: 'oauth_pkce', description: 'Mail', configured: false, configurable: true, mcp_env_keys: [], oauth_setup_fields: [] }]);
            await expect(page.locator('.connector-tile')).toBeVisible();
            await shot(page, `connectors-${locale}-none`);
            expect(await audit(page)).toEqual([]);
        });

        test('phone width: a long account stays on screen', async ({ page }) => {
            await page.setViewportSize({ width: 390, height: 844 });
            await open(page, '', locale, (p) => withConnectors(p));
            await page.getByRole('button', { name: /Open sidebar/ }).click();
            await page.getByRole('button', { name: /Settings/ }).first().click();
            await page.getByRole('dialog').locator('[data-section="connectors"]').click();
            await expect(page.locator('.connector-tile').first()).toBeVisible();
            await shot(page, `connectors-${locale}-phone`);
            expect(await audit(page)).toEqual([]);
        });
    });
}

const SETTINGS_UPDATE_STATES: Array<[string, Record<string, unknown>]> = [
    ['idle', {}],
    ['available', { status: 'available', version: '0.26.0' }],
    ['downloading', { status: 'downloading', version: '0.26.0', progress: 42 }],
    ['ready', { status: 'downloaded', version: '0.26.0' }],
    ['failed', { status: 'error', error: 'net::ERR_INTERNET_DISCONNECTED' }],
    ['pinned', { pinnedVersion: 'v0.25.3', currentVersion: '0.25.3-rc.2' }],
];

for (const locale of ['ar', 'en']) {
    for (const [state, update] of SETTINGS_UPDATE_STATES) {
        test(`desktop app and updates: ${state} (${locale})`, async ({ page }) => {
            await open(page, '', locale, async (p) => { await withSettingsPanes(p); await withDesktopBridges(p, update); });
            await page.getByRole('button', { name: /Settings/ }).first().click();
            const updates = page.locator('.settings-disclosure', { hasText: /Updates/ });
            await expect(updates).toBeVisible();
            await page.locator('.settings-disclosure:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
            await updates.scrollIntoViewIfNeeded();
            await shot(page, `updates-${locale}-${state}`);
            expect(await audit(page)).toEqual([]);
            if (locale === 'ar') expect(await untranslated(page)).toEqual([]);
        });
    }

    test(`notifications on the desktop app (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => { await withSettingsPanes(p); await withDesktopBridges(p); });
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="notifications"]').click();
        await expect(page.locator('#battery-floor')).toBeVisible();
        await shot(page, `notifications-${locale}-desktop`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page)).toEqual([]);
    });
}

/** Visible text left in English on an Arabic screen; names and paths sit in <bdi>. */
const untranslated = (page: Page, within = '.settings-scroll', userText = 'bdi'): Promise<string[]> =>
    page.evaluate(([selector, skip]) => {
        const root = document.querySelector(selector) as Element;
        const left = new Set<string>();
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
            const text = n.textContent?.trim() ?? '';
            const el = n.parentElement as Element;
            if (!/[A-Za-z]{3}/.test(text) || /[\u0600-\u06ff]/.test(text) || !el.checkVisibility()) continue;
            if (el.closest(`.i18n-gloss, code, bdi, kbd, select, .provider-tab, .skill-row-name, .connector-tile-type, .beta-badge, .status-label, .status-value, ${skip}`)) continue;
            left.add(text.slice(0, 60));
        }
        return [...left];
    }, [within, userText]);

const LOCAL_STATUS = {
    state: 'loaded', active_model: 'Gemma-4-E4B-it-GGUF', active_display_name: 'Gemma 4 E4B', active_remote: false,
    model: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B',
    placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'reported', warning: null },
    ctx_size: 65536, memory: { source: 'AMD iGPU', total_gb: 112, free_gb: 80.5, model_gb: 30.8 }, memory_error: null,
    error: null, base_url: 'http://127.0.0.1:13305', owner: 'gaia', busy: false, restarting: false, restart_error: null,
};

const POPOVER_STATES: Array<[string, Record<string, unknown>]> = [
    ['local', LOCAL_STATUS],
    ['cloud', { ...LOCAL_STATUS, active_model: 'deepseek-v4p1-flash', active_display_name: 'DeepSeek V4.1 Flash', active_remote: true }],
];

for (const locale of ['ar', 'en']) {
    for (const [state, status] of POPOVER_STATES) {
        test(`sidebar model-status popover: ${state} (${locale})`, async ({ page }) => {
            await open(page, '', locale, async (p) => { await p.route('**/api/providers/local/status', (route) => route.fulfill({ json: status })); });
            await page.locator('.sb-location').click();
            const popover = page.locator('.popover .model-status');
            await expect(popover.locator('.model-status-grid')).toBeVisible();
            await shot(page, `model-status-${locale}-${state}`);
            expect(await audit(page)).toEqual([]);
            if (locale === 'ar') expect(await untranslated(page, '.popover .model-status')).toEqual([]);
        });
    }
}

for (const section of ['general', 'permissions', 'skills', 'memory', 'notifications', 'privacy', 'advanced']) {
    test(`settings ${section} pane has no leftover English`, async ({ page }) => {
        await open(page, '', 'ar', withSettingsPanes);
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator(`[data-section="${section}"]`).click();
        await expect(page.locator('.settings-pane-title')).toBeVisible();
        await expect(page.locator('.loading-text, .spin')).toHaveCount(0);
        await page.locator('.settings-disclosure:not([open]) > summary, .settings-more:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
        await page.waitForTimeout(100);
        expect(await untranslated(page)).toEqual([]);
        expect(await barePaths(page)).toEqual([]);
    });
}

/**
 * Paths starting `~/` or `.dir/` on an Arabic screen outside an LTR isolate or `<bdi dir="ltr">`,
 * where bidi moves the leading punctuation to the far end ("gaia/skills./~").
 */
const barePaths = (page: Page): Promise<string[]> =>
    page.evaluate(() => {
        const root = document.querySelector('.settings-scroll') as Element;
        const bare: string[] = [];
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
            const text = n.textContent ?? '';
            const path = /(?:^|[\s(])(?:~\/|\.[A-Za-z][\w-]*\/)/;
            if (!path.test(text) || n.parentElement?.closest('bdi[dir="ltr"]')) continue;
            const stripped = text.replace(/[⁦⁨][^⁦-⁩]*⁩/g, '');
            if (path.test(stripped)) bare.push(text.trim().slice(0, 60));
        }
        return bare;
    });

/** Schedules with a failed run, a chat to open and a run history; and a "when" GAIA can't read. */
async function withSchedules(page: Page, schedules: unknown[] = SCHEDULE_ROWS): Promise<void> {
    await page.route('**/api/schedules', (route) => route.fulfill({ json: { schedules, total: schedules.length } }));
    await page.route('**/api/schedules/*/results*', (route) => route.fulfill({
        json: { results: [
            { id: 'r1', task_id: 's1', executed_at: new Date(NOW.getTime() - 7 * 60_000).toISOString(), result: '3 new items', error: null },
            { id: 'r2', task_id: 's1', executed_at: new Date(NOW.getTime() - 3 * 3_600_000).toISOString(), result: null, error: 'The model server stopped.' },
        ] },
    }));
    await page.route('**/api/schedules/parse', (route) => route.fulfill({
        json: route.request().postDataJSON().input === 'every 2m'
            ? { interval_seconds: 120, time_of_day: null, start_hour: null, end_hour: null, days_of_week: null, description: 'Every 2 minutes', description_parts: { ...NO_PARTS, kind: 'every', count: 2, unit: 'minute' }, next_run_at: new Date(NOW.getTime() + 120_000).toISOString(), valid: true }
            : { interval_seconds: 0, time_of_day: null, start_hour: null, end_hour: null, days_of_week: null, description: "25:00 isn't a time — use 0:00–23:59.", description_parts: null, next_run_at: null, valid: false },
    }));
}

const NO_PARTS = { kind: 'every', count: null, unit: null, days: null, time: null, start_hour: null, end_hour: null };

const SCHEDULE_ROW = {
    id: 's1', name: 'Morning brief', interval_seconds: 86_400, prompt: 'Summarize my unread email and calendar for today',
    status: 'active', created_at: null, last_run_at: new Date(NOW.getTime() - 7 * 60_000).toISOString(),
    next_run_at: new Date(NOW.getTime() + 17 * 60_000).toISOString(), last_result: '3 new items', run_count: 9, error_count: 1,
    session_id: 'chat-1', schedule_config: null, description: 'Weekdays at 8:00 AM', last_error: 'The model server stopped.',
    description_parts: { ...NO_PARTS, kind: 'days', days: [0, 1, 2, 3, 4], time: '08:00' },
};
const SCHEDULE_ROWS = [
    SCHEDULE_ROW,
    {
        ...SCHEDULE_ROW, id: 's2', name: 'Weekly budget check-in', status: 'paused', next_run_at: null, error_count: 0, last_error: null, session_id: null,
        description: 'Every Monday, Wednesday at 9:00 AM', description_parts: { ...NO_PARTS, kind: 'days', days: [0, 2], time: '09:00' },
    },
    {
        ...SCHEDULE_ROW, id: 's3', name: 'Inbox sweep', last_error: null, error_count: 0, session_id: null,
        description: 'Every 2 hours, 8:00 AM - 6:00 PM, weekdays',
        description_parts: { ...NO_PARTS, kind: 'window', count: 2, unit: 'hour', start_hour: 8, end_hour: 18, days: [0, 1, 2, 3, 4] },
    },
];

/** Titles, labels and placeholders in the schedules dialog that carry no Arabic. */
const untranslatedAttributes = (page: Page): Promise<string[]> =>
    page.locator('.schedule-modal').evaluate((root) =>
        [root.closest('[role="dialog"]')!, ...root.querySelectorAll('[title], [aria-label], [placeholder]')].flatMap((el) =>
            ['aria-label', 'placeholder', 'title']
                .map((a) => el.getAttribute(a) ?? '')
                .filter((v) => /[A-Za-z]{3}/.test(v) && !/[؀-ۿ]/.test(v))));

for (const locale of ['ar', 'en']) {
    test.describe(`scheduled tasks (${locale})`, () => {
        const openSchedules = async (page: Page, schedules?: unknown[]) => {
            await open(page, '', locale, (p) => withSchedules(p, schedules));
            await page.locator('.sb').getByRole('button', { name: locale === 'ar' ? /المهام المجدولة/ : /Scheduled tasks/ }).click();
            await expect(page.locator('.schedule-modal')).toBeVisible();
        };
        const check = async (page: Page, name: string) => {
            await shot(page, `schedules-${locale}-${name}`);
            expect(await audit(page)).toEqual([]);
            if (locale === 'ar') {
                expect(await untranslated(page, '.schedule-modal', '[dir="auto"], textarea')).toEqual([]);
                expect(await untranslatedAttributes(page)).toEqual([]);
            }
        };

        test('empty', async ({ page }) => {
            await openSchedules(page, []);
            await expect(page.locator('.schedule-empty')).toBeVisible();
            await check(page, 'empty');
        });

        test('list with a failed run and its history open', async ({ page }) => {
            await openSchedules(page);
            await page.locator('.schedule-card').first().click();
            await expect(page.locator('.schedule-result-item')).toHaveCount(2);
            await check(page, 'list');
        });

        test('new schedule, nothing typed yet', async ({ page }) => {
            await openSchedules(page, []);
            await page.locator('.schedule-modal .btn-primary').click();
            await expect(page.locator('.schedule-nl-input')).toBeVisible();
            await check(page, 'form-empty');
        });

        test('new schedule with a "when" GAIA can read', async ({ page }) => {
            await openSchedules(page, []);
            await page.locator('.schedule-modal .btn-primary').click();
            await page.locator('.schedule-nl-input').fill('every 2m');
            await expect(page.locator('.schedule-parsed-preview.valid')).toBeVisible();
            await check(page, 'form-valid');
        });

        test('new schedule with a "when" GAIA cannot read', async ({ page }) => {
            await openSchedules(page);
            await page.locator('.schedule-modal .btn-primary').click();
            await page.locator('.schedule-nl-input').fill('daily at 25:00');
            await expect(page.locator('.schedule-parsed-hint')).toBeVisible();
            await check(page, 'form-invalid');
        });
    });
}

const RAW_SERVER_ERROR = "The local model server is not reachable at http://127.0.0.1:59987/api/v1 (HTTPConnectionPool(host='127.0.0.1', port=59987): Max retries exceeded)";

for (const locale of ['ar', 'en']) {
    test(`model server down: one plain notice, no raw error on show (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => {
            await withModelPage(p, MODEL_LIST, SETUP_PLAN);
            await p.route(/\/api\/providers\/local\/(models|context)/, (route) =>
                route.fulfill({ status: 503, json: { detail: RAW_SERVER_ERROR } }));
            await p.route(/\/api\/providers(\?|$)/, (route) => route.fulfill({
                json: { providers: [{ id: 'local', name: 'Local', remote: false, privacy_notice: 'Runs on this PC.' }], lemonade_error: RAW_SERVER_ERROR },
            }));
        });
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="model"]').click();
        await expect(page.locator('.settings-notice')).toHaveCount(1);
        await expect(page.getByText('HTTPConnectionPool')).toBeHidden();
        await expect(page.locator('.settings-notice .btn-secondary')).toBeVisible();
        await shot(page, `model-${locale}-server-down`);
        expect(await audit(page)).toEqual([]);
    });
}

const SERVER_DOWN_REPLY = 'The model call failed at step 1, so this request is unfinished. The local model server at 127.0.0.1:13305 '
    + "isn't answering, so the message never reached Fireworks AI.\n\nNothing had run yet.\n\nStart the model server, then send the message again.";

/** The send fails on a stopped model server, and the server's saved copy of the chat then holds that reply. */
async function withServerDown(page: Page): Promise<void> {
    let sent = false;
    await page.route('**/api/chat/send', (route) => {
        sent = true;
        const done = { type: 'done', message_id: 42, content: SERVER_DOWN_REPLY, failed: true, failure: 'model_server_down' };
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body: `data: ${JSON.stringify(done)}\n\n` });
    });
    await page.route(new RegExp(`/api/sessions/${ANSWER_SESSION}/messages(\\?|$)`), (route) => (sent
        ? route.fulfill({
            json: {
                messages: [
                    { id: 41, session_id: ANSWER_SESSION, role: 'user', content: 'Summarize the minutes', created_at: NOW.toISOString(), rag_sources: null },
                    { id: 42, session_id: ANSWER_SESSION, role: 'assistant', content: SERVER_DOWN_REPLY, created_at: NOW.toISOString(), rag_sources: null },
                ],
                total: 2,
            },
        })
        : route.fallback()));
    await page.route('**/api/providers/local/status', (route) => route.fulfill({
        json: {
            state: 'stopped', active_model: 'fireworks.glm-5', active_display_name: 'glm-5', active_remote: true, model: null,
            display_name: null, placement: null, ctx_size: null, memory: null, memory_error: null,
            error: "The model server isn't answering at http://127.0.0.1:13305/api/v1. Restart starts it.",
            base_url: 'http://127.0.0.1:13305/api/v1', owner: 'gaia', busy: false, restarting: false, restart_error: null,
        },
    }));
}

for (const locale of ['ar', 'en']) {
    test(`a turn the stopped model server ended offers to start it (${locale})`, async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withServerDown);
        const box = page.getByRole('textbox').first();
        await box.fill('Summarize the minutes');
        await box.press('Enter');
        const notice = page.locator('.server-down-notice');
        await expect(notice).toHaveCount(1);
        await expect(notice.getByRole('button', { name: locale === 'ar' ? /تشغيل خادم النماذج/ : /Start the model server/ })).toBeVisible();
        await expect(notice).not.toContainText('gaia ');
        await shot(page, `server-down-${locale}`);
        expect(await audit(page)).toEqual([]);
    });
}

/** GAIA answered once that this chat was running, then stopped listening. */
async function withGaiaStoppedMidAnswer(page: Page): Promise<void> {
    let polls = 0;
    await page.route('**/api/chat/active', (route) => (polls++ === 0
        ? route.fulfill({ json: { session_ids: [ANSWER_SESSION], queued: [], awaiting_user: {} } })
        : route.abort('connectionrefused')));
    await page.route(/\/api\/(chat\/attach|sessions\/[^/]+\/(changes|outputs))/, (route) => route.abort('connectionrefused'));
}

for (const locale of ['ar', 'en']) {
    test(`GAIA stopped mid-answer: plain words, a retry, no raw error, not running (${locale})`, async ({ page }) => {
        test.setTimeout(60_000);
        await open(page, `#${ANSWER_SESSION}`, locale, withGaiaStoppedMidAnswer);
        const stopped = locale === 'ar' ? 'توقّف GAIA قبل أن يُكمل هذه الإجابة' : 'GAIA stopped before it finished this answer';
        // Three re-attaches, the last 10 s after the second.
        await expect(page.getByText(stopped)).toBeVisible({ timeout: 30_000 });
        await expect(page.getByText(/Failed to fetch/)).toHaveCount(0);
        await expect(page.getByText(/may still finish/)).toHaveCount(0);
        await expectVisibleTryAgain(page, locale);
        await expect(page.locator('.sb-dot.is-running')).toHaveCount(0);
        await shot(page, `gaia-stopped-mid-answer-${locale}`);
        expect(await audit(page)).toEqual([]);
    });
}

/** The reply's text asks the user to press Try again, so the button must be on screen, labelled, without a hover. */
async function expectVisibleTryAgain(page: Page, locale: string): Promise<void> {
    const retry = page.getByRole('button', { name: /Try again/ });
    await expect(retry).toHaveCount(1);
    await expect(retry).toHaveText(locale === 'ar' ? /حاول مجددًا/ : /Try again/);
    const opacity = await retry.evaluate((el) => {
        let o = 1;
        for (let at: Element | null = el; at; at = at.parentElement) o *= Number(getComputedStyle(at).opacity);
        return o;
    });
    expect(opacity).toBeGreaterThan(0.99);
}

const FAILED_WITH_REPORT =
    "The model call failed at step 1, so this request is unfinished. Couldn't reach the local LLM.\n\n"
    + 'Nothing had run yet.\n\n'
    + 'A problem report for this was saved on this PC as GF-20261104-192233-3f2a; nothing was sent anywhere.';

/** A chat whose last turn failed and named the problem report GAIA saved for it. */
async function withFailureReport(page: Page): Promise<void> {
    await page.route(new RegExp(`/api/sessions/${ANSWER_SESSION}/messages(\\?|$)`), (route) => route.fulfill({
        json: {
            messages: [
                { id: 1, session_id: ANSWER_SESSION, role: 'user', content: 'Summarize the minutes', created_at: '2026-11-04T19:22:00Z', rag_sources: null },
                { id: 2, session_id: ANSWER_SESSION, role: 'assistant', content: FAILED_WITH_REPORT, created_at: '2026-11-04T19:22:33Z', rag_sources: null },
            ],
            total: 2,
        },
    }));
}

for (const locale of ['ar', 'en']) {
    test(`a failed answer offers its troubleshooting report (${locale})`, async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withFailureReport);
        const report = page.locator('.msg-report');
        await expect(report.getByRole('button')).toHaveText(locale === 'ar' ? /حفظ تقرير لاستكشاف الأخطاء/ : /Save a troubleshooting report/);
        await expect(report.locator('.msg-report-hint')).toContainText(locale === 'ar' ? 'لا يُرسَل شيء' : 'nothing is sent');
        await report.scrollIntoViewIfNeeded();
        await shot(page, `failed-answer-report-${locale}`);
        expect(await audit(page)).toEqual([]);
    });
}

const PARTIAL_ANSWER = 'Coral reefs cover less than one percent of the ocean floor.';
const INTERRUPTED_NOTICE = 'This answer was interrupted when GAIA closed. Send again to retry.';

/** What a reopened chat holds after GAIA restarted: the saved part of the answer, closed by the server's notice. */
async function withInterruptedAnswer(page: Page): Promise<void> {
    await page.route(new RegExp(`/api/sessions/${ANSWER_SESSION}/messages(\\?|$)`), (route) => route.fulfill({
        json: {
            messages: [
                { id: 1, session_id: ANSWER_SESSION, role: 'user', content: 'Write a 1500-word essay about coral reefs.', created_at: '2026-06-10T00:00:00Z', rag_sources: null },
                { id: 2, session_id: ANSWER_SESSION, role: 'assistant', content: `${PARTIAL_ANSWER}\n\n${INTERRUPTED_NOTICE}`, created_at: '2026-06-10T00:00:05Z', rag_sources: null },
            ],
            total: 2,
        },
    }));
}

for (const locale of ['ar', 'en']) {
    test(`after GAIA restarts, a cut-off answer says so in plain words and offers Try again (${locale})`, async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withInterruptedAnswer);
        await expect(page.getByText(PARTIAL_ANSWER)).toBeVisible();
        if (locale === 'ar') await expect(page.getByText('انقطعت هذه الإجابة عندما أُغلق GAIA')).toBeVisible();
        await expect(page.getByText(/Send again to retry/)).toHaveCount(0);
        await expectVisibleTryAgain(page, locale);
        await shot(page, `gaia-restarted-interrupted-${locale}`);
        expect(await audit(page)).toEqual([]);
    });
}

for (const locale of ['ar', 'en']) {
    test(`a stopped server's hint wraps inside its row and names no command (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => {
            await withSettingsPanes(p);
            await p.route('**/api/system/status', (route) => route.fulfill({
                json: { ...SYSTEM_STATUS, lemonade_running: false, model_loaded: null, start_command: 'gaia init', start_instruction: 'Lemonade Server is not installed. Run `gaia init` to install it.' },
            }));
        });
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="advanced"]').click();
        const row = page.locator('.status-row--has-hint').first();
        await expect(row).toBeVisible();
        await expect(row).not.toContainText('gaia init');
        const clipped = await page.locator('.status-hint').evaluateAll((els) => els.filter((el) => {
            const box = el.getBoundingClientRect();
            const card = (el.closest('.status-row') as Element).getBoundingClientRect();
            return box.left < card.left || box.right > card.right;
        }).length);
        expect(clipped).toBe(0);
        await shot(page, `advanced-${locale}-stopped`);

        // No server installed is never a dead end: setup reopens, and can be left again.
        await withSetup(page);
        await page.getByRole('dialog').locator('.settings-card .btn-primary').first().click();
        const back = page.locator('.setup-close');
        await expect(back).toBeVisible();
        await shot(page, `setup-${locale}-reopened`);
        expect(await audit(page)).toEqual([]);
        await back.click();
        await expect(page.locator('.setup')).toHaveCount(0);
        const banner = page.locator('.connection-banner');
        await expect(banner.locator('button.connection-banner__retry')).toHaveCount(2);
        await shot(page, `banner-${locale}-not-installed`);
        expect(await audit(page)).toEqual([]);
    });
}

for (const locale of ['ar', 'en']) {
    test(`a server LEMONADE_BASE_URL picks offers GAIA's own and names no command (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => {
            await withSettingsPanes(p);
            await p.route('**/api/lemonade/target', (route) => route.fulfill({
                json: {
                    mode: 'external', saved_mode: null, source: 'env', base_url: 'http://127.0.0.1:59999/api/v1', saved_base_url: null,
                    api_key_stored: false, gaia_installed: true, offer: false, env_override: null, env_url_ignored: null, what_gaia_does: '',
                },
            }));
        });
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="advanced"]').click();
        const section = page.locator('section[aria-label]').filter({ has: page.locator('.model-override-desc[role="status"]') }).last();
        const disclosure = page.locator('.settings-disclosure').filter({ has: section });
        if (!(await disclosure.getAttribute('open'))) await disclosure.locator('> summary').click();
        await expect(section.locator('.setting-actions .btn-secondary')).toBeVisible();
        await expect(section).not.toContainText(/unset|restart GAIA|gaia init|gaia config/i);
        await section.scrollIntoViewIfNeeded();
        await shot(page, `advanced-${locale}-env-server`);
        expect(await audit(page)).toEqual([]);
    });
}

test('a keyboard user can reach and open a section, and sees where focus is'
, async ({ page }) => {
    await open(page, '', 'en', withSettingsPanes);
    await page.getByRole('button', { name: /Settings/ }).first().click();
    await page.getByRole('dialog').locator('[data-section="advanced"]').click();
    const summary = page.locator('.settings-disclosure > summary').first();
    await expect(summary).toBeVisible();
    await summary.focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    expect(await page.evaluate(() => {
        const el = document.activeElement as Element;
        const style = getComputedStyle(el);
        return `${el.tagName} ${el.matches(':focus-visible')} ${style.outlineStyle} ${style.outlineWidth}`;
    })).toMatch(/^SUMMARY true solid [23]px$/);
    await page.keyboard.press('Enter');
    await expect(page.locator('.settings-disclosure').first()).toHaveAttribute('open', '');
    await page.keyboard.press('Space');
    await expect(page.locator('.settings-disclosure').first()).not.toHaveAttribute('open', '');
});

test('every disclosure is a comfortable target', async ({ page }) => {
    await open(page, '', 'en', withSettingsPanes);
    await page.getByRole('button', { name: /Settings/ }).first().click();
    for (const section of ['general', 'permissions', 'skills', 'privacy', 'advanced']) {
        await page.getByRole('dialog').locator(`[data-section="${section}"]`).click();
        await expect(page.locator('.settings-pane-title')).toBeVisible();
        const small = await page.locator('.settings-disclosure > summary, .settings-more > summary').evaluateAll((els) =>
            els.filter((el) => el.checkVisibility() && el.getBoundingClientRect().height < 24).map((el) => el.textContent));
        expect(small).toEqual([]);
    }
});

test('a long data folder wraps at phone width instead of scrolling sideways', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page, '', 'en', async (p) => {
        await withSettingsPanes(p);
        await p.route('**/api/system/status', (route) => route.fulfill({
            json: { ...SYSTEM_STATUS, data_dir: `C:/Users/you/${'a-very-long-folder-name/'.repeat(5)}.gaia/chat` },
        }));
    });
    await page.getByRole('button', { name: /Open sidebar/ }).click();
    await page.getByRole('button', { name: /Settings/ }).first().click();
    await page.getByRole('dialog').locator('[data-section="privacy"]').click();
    await expect(page.locator('.setting-path').first()).toBeVisible();
    const overflow = await page.locator('.settings-scroll').evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    expect(await audit(page)).toEqual([]);
});

/** A Strix Halo on first run: Qwen won't fit 16 GB, an FLM model is installed, the disk is tight. */
const SETUP_ENTRY = (over: Record<string, unknown>) => ({
    roles: ['chat'], purpose: '', if_skipped: null, recipe: 'llamacpp', size_gb: 1, full_size_gb: 1, partial: false,
    partial_gb: 0, downloaded: false, in_lemonade: true, recommended: false, planned: false, gaia_model: true,
    selected: false, skipped: false, chat_choice: true, fits: true, need_gb: 2, capacity_gb: 16, fit_reason: null, ...over,
});
const SETUP_CATALOG = [
    SETUP_ENTRY({ id: 'Gemma-4-E4B-it-GGUF', name: 'Gemma 4 E4B (Multimodal)', roles: ['chat', 'vision'], size_gb: 5.97, full_size_gb: 5.97, recommended: true, planned: true, selected: true, need_gb: 8.3 }),
    SETUP_ENTRY({ id: 'user.embeddinggemma-300m-GGUF', name: 'EmbeddingGemma 300M', roles: ['embedding'], size_gb: 0.33, full_size_gb: 0.33, recommended: true, planned: true, selected: true, chat_choice: false, need_gb: 1.3 }),
    SETUP_ENTRY({ id: 'Qwen3.6-35B-A3B-GGUF', name: 'Qwen3.6 35B A3B (Multimodal)', size_gb: 23.3, full_size_gb: 23.3, fits: false, need_gb: 26.8 }),
    SETUP_ENTRY({ id: 'gemma4-it-e2b-FLM', name: 'Gemma 4 E2B (NPU)', recipe: 'flm', size_gb: 0, downloaded: true, gaia_model: false, fits: null, need_gb: null, capacity_gb: null }),
];
const SETUP_PLAN = {
    profile: 'gaia', lemonade: { mode: 'gaia', url: 'http://127.0.0.1:13305/api/v1', reachable: true },
    chat_model: { id: 'Gemma-4-E4B-it-GGUF', user_set: false, size_gb: 5.97, downloaded: false },
    recommended_model: 'Gemma-4-E4B-it-GGUF', decided_after_server: false, memory_gb: 16, memory_source: 'AMD iGPU',
    capacity_gb: 16, disk: { path: 'C:\\Users\\you\\.cache\\huggingface\\hub', free_gb: 25.3, source: 'lemonade' },
    download_gb: 6.3, disk_warning: null, skipped_models: [], options: [], models: [], catalog: SETUP_CATALOG,
};
const SETUP_GPU = {
    applies: true, show_in_setup: true, hidden_reason: null, os: 'windows', method: 'guided',
    method_reason: 'On Windows this is set in AMD Software.', total_gb: 128, gpu_gb: 16, cpu_gb: 112,
    setting: { name: 'Variable Graphics Memory', current_gb: 16, min_gb: 4, max_gb: 96 }, reserve_gb: 16,
    needs_admin: true, needs_restart: true,
    presets: [{ setting_gb: 32, gpu_gb: 32, unlocks: ['Qwen3.6-35B-A3B-GGUF'] }, { setting_gb: 48, gpu_gb: 48, unlocks: ['Qwen3.6-35B-A3B-GGUF'] }],
    recommended_setting_gb: 32, models: [], pending: null, revert: { available: false, previous_setting_gb: null },
    guided_steps: ['Open AMD Software.', 'Set Variable Graphics Memory to 30 GiB.'],
};
const SETUP_ROW = (id: string, roles: string[], state: string, done: number, total: number, extra: Record<string, unknown> = {}) => ({
    id, roles, state, bytes_done: done, bytes_total: total, speed_bps: 0, eta_s: null, error: null, error_kind: null,
    can_retry: state === 'failed', retrying_until: null, downloaded_before_gb: null, ...extra,
});
const SETUP_RUNNING = {
    state: 'running', text: 'Downloading the models', chat_model: 'Gemma-4-E4B-it-GGUF', skip_chat_model: false,
    steps: [], log_tail: [], command: 'gaia init --profile gaia',
    models: [
        SETUP_ROW('user.embeddinggemma-300m-GGUF', ['embedding'], 'verifying', 330e6, 330e6),
        SETUP_ROW('Gemma-4-E4B-it-GGUF', ['chat', 'vision'], 'failed', 2e9, 5.97e9, { error: 'Connection reset by peer', error_kind: 'network' }),
        SETUP_ROW('Qwen3.6-35B-A3B-GGUF', ['chat'], 'downloading', 7.6e9, 23.3e9),
    ],
    overall: { bytes_done: 9.93e9, bytes_total: 29.6e9, speed_bps: 0, eta_s: null, percent: 33, failed: 1 },
};

for (const locale of ['ar', 'en']) {
    test.describe(`first-run setup (${locale})`, () => {
        for (const s of SETUP_SCENES) {
            test(s.name, async ({ page }) => {
                await open(page, '', locale, (p) => withSetup(p, s.answers));
                await s.reach(page);
                await shot(page, `setup-${locale}-${s.name}`);
                expect(await audit(page)).toEqual([]);
            });
        }

        for (const name of NARROW_SCENES) {
            test(`${name} at phone width`, async ({ page }) => {
                await page.setViewportSize({ width: 390, height: 844 });
                const s = scene(name);
                await open(page, '', locale, (p) => withSetup(p, s.answers));
                await s.reach(page);
                expect(await audit(page)).toEqual([]);
            });
        }

        test('narrow window: no sideways scroll', async ({ page }) => {
            await page.setViewportSize({ width: 320, height: 720 });
            const s = scene('models');
            await open(page, '', locale, (p) => withSetup(p, s.answers));
            await s.reach(page);
            expect(await audit(page)).toEqual([]);
        });
    });
}

for (const locale of ['ar', 'en']) {
    test(`a long model name stays inside the Test step's failure card (${locale})`, async ({ page }) => {
        const s = scene('test-long');
        await open(page, '', locale, (p) => withSetup(p, s.answers));
        await s.reach(page);
        const clipped = await page.locator('.setup-failure').evaluate((card) => {
            const box = card.getBoundingClientRect();
            return [...card.querySelectorAll('button')].filter((b) => {
                const r = b.getBoundingClientRect();
                return r.right > box.right + 1 || r.left < box.left - 1;
            }).map((b) => b.textContent);
        });
        expect(clipped).toEqual([]);
    });
}

test.describe('first-run setup in the dark theme', () => {
    for (const s of SETUP_SCENES) {
        test(s.name, async ({ page }) => {
            await open(page, '', 'ar', (p) => withSetup(p, s.answers), 'dark');
            await s.reach(page);
            expect(await audit(page)).toEqual([]);
        });
    }
});

test.describe('first-run setup leaves no English in Arabic', () => {
    for (const s of SETUP_SCENES) {
        test(s.name, async ({ page }) => {
            await open(page, '', 'ar', (p) => withSetup(p, s.answers));
            await s.reach(page);
            expect(await leftoverEnglish(page)).toEqual([]);
        });
    }
});

/** The desktop app's install bridge, stuck on one status. */
async function withInstall(page: Page, status: object): Promise<void> {
    await page.addInitScript((initial) => {
        Object.assign(window, {
            gaiaInstall: {
                onProgress: () => () => {},
                status: async () => initial,
                retry: async () => true,
                openLogFile: async () => ({ ok: true, message: null }),
            },
        });
    }, status);
}

const INSTALL_RUNNING = { phase: 'running', stage: 'install-package', percent: 42, message: '', error: null };
const INSTALL_REFRESHING = { ...INSTALL_RUNNING, stage: 'agent-refresh', percent: null };
const INSTALL_FAILED = {
    ...INSTALL_RUNNING,
    phase: 'failed',
    error: {
        stage: 'install-package',
        reason: 'network',
        message: 'Failed to install amd-gaia[ui]==0.25.4 (pip exit 1).',
        logTail: ['$ uv pip install amd-gaia[ui]==0.25.4', '  [pip] error: Failed to fetch https://pypi.org/simple/numpy/', '  exit code: 1'],
    },
};
const INSTALL_LOCKED = {
    ...INSTALL_FAILED,
    error: {
        ...INSTALL_FAILED.error,
        reason: 'locked',
        message: "GAIA couldn't replace its own files because another program has them open (gaia.exe).",
        lockedBy: [{
            pid: 32852, name: 'gaia.exe', isGaia: true,
            path: 'C:\\Users\\kalin.ovtcharov\\Work\\demo-projects\\customer-showcase-home\\.gaia\\venv\\Scripts\\gaia.exe',
            folder: 'C:\\Users\\kalin.ovtcharov\\Work\\demo-projects\\customer-showcase-home',
        }],
    },
};
const INSTALL_LOCKED_OTHER = {
    ...INSTALL_LOCKED,
    error: {
        ...INSTALL_LOCKED.error,
        lockedBy: [{
            pid: 4100, name: 'Code.exe', isGaia: false,
            path: 'C:\\Users\\kalin.ovtcharov\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe',
            folder: 'C:\\Users\\kalin.ovtcharov\\AppData\\Local\\Programs\\Microsoft VS Code',
        }],
    },
};
const INSTALL_DAEMON = {
    ...INSTALL_FAILED,
    error: {
        ...INSTALL_FAILED.error,
        stage: 'create-venv',
        reason: 'daemon',
        message: "GAIA's background service (the GAIA daemon) did not stop within 120 seconds, so its files can't be updated.",
    },
};

for (const locale of ['ar', 'en']) {
    test.describe(`engine install (${locale})`, () => {
        for (const theme of ['light', 'dark'] as const) {
            test(`running, ${theme}`, async ({ page }) => {
                await open(page, '?engine=install', locale, (p) => withInstall(p, INSTALL_RUNNING), theme);
                await expect(page.locator('progress')).toHaveAttribute('value', '42');
                expect(await audit(page)).toEqual([]);
            });

            test(`agent refresh, ${theme}`, async ({ page }) => {
                await open(page, '?engine=install', locale, (p) => withInstall(p, INSTALL_REFRESHING), theme);
                await expect(page.locator('progress')).not.toHaveAttribute('value', /./);
                expect(await audit(page)).toEqual([]);
            });

            test(`failed with Details open, ${theme}`, async ({ page }) => {
                await open(page, '?engine=install', locale, (p) => withInstall(p, INSTALL_FAILED), theme);
                await page.locator('.engine-details summary').click();
                await expect(page.locator('.engine-log')).toBeVisible();
                expect(await audit(page)).toEqual([]);
            });
        }

        test('failed on a file another copy of GAIA holds open', async ({ page }) => {
            await open(page, '?engine=install', locale, (p) => withInstall(p, INSTALL_LOCKED));
            await expect(page.locator('.engine-retry')).toHaveCount(2);
            expect(await audit(page)).toEqual([]);
            await shot(page, `engine-locked-gaia-${locale}`);
        });

        test('failed on a file another program holds open', async ({ page }) => {
            await open(page, '?engine=install', locale, (p) => withInstall(p, INSTALL_LOCKED_OTHER));
            await expect(page.locator('.engine-retry')).toHaveCount(1);
            expect(await audit(page)).toEqual([]);
            await shot(page, `engine-locked-other-${locale}`);
        });

        test('failed because the GAIA daemon would not stop', async ({ page }) => {
            await open(page, '?engine=install', locale, (p) => withInstall(p, INSTALL_DAEMON));
            await expect(page.locator('.engine-retry')).toHaveCount(1);
            expect(await audit(page)).toEqual([]);
            await shot(page, `engine-daemon-${locale}`);
        });

        test('narrow window: no sideways scroll', async ({ page }) => {
            await page.setViewportSize({ width: 320, height: 720 });
            await open(page, '?engine=install', locale, (p) => withInstall(p, INSTALL_FAILED));
            await page.locator('.engine-details summary').click();
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
            expect(await audit(page)).toEqual([]);
        });
    });
}

test('a browser never shows the install screen, even when the URL asks', async ({ page }) => {
    await open(page, '?engine=install');
    await expect(page.locator('.engine')).toHaveCount(0);
    await expect(page.locator('.sb')).toBeVisible();
});

/** The Windows desktop frame: the window controls are drawn over the app's top-right corner. */
async function withWindowsControls(page: Page): Promise<void> {
    await page.addInitScript(() => {
        const calls: unknown[] = [];
        Object.assign(window, {
            __colors: calls,
            gaiaWindow: {
                titleBar: 'overlay',
                setColors: async (colors: unknown) => {
                    calls.push(colors);
                    return true;
                },
            },
        });
    });
}

for (const [locale, settings, beneath] of [['ar', /الإعدادات/, '--bg-sidebar'], ['en', /^Settings$/, '--bg-primary']] as const) {
    test(`window controls take the color beneath them and dim with a modal (${locale})`, async ({ page }) => {
        await open(page, '', locale, withWindowsControls);
        const behind = await page.evaluate((token) => getComputedStyle(document.documentElement).getPropertyValue(token).trim(), beneath);
        await expect.poll(() => lastColor(page)).toBe(behind);

        await page.locator('.sb').getByRole('button', { name: settings }).first().click();
        await expect(page.locator('.settings-overlay')).toBeVisible();
        await expect.poll(async () => brightness(await lastColor(page))).toBeLessThan(brightness(behind));

        await page.keyboard.press('Escape');
        await expect(page.locator('.settings-overlay')).toHaveCount(0);
        await expect.poll(() => lastColor(page)).toBe(behind);
        expect(await refusedColors(page)).toEqual([]);
    });
}

test('window controls dim under the missing-model dialog too', async ({ page }) => {
    await open(page, '', 'en', async (p) => {
        await withWindowsControls(p);
        await withMissingModel(p);
    });
    const canvas = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bg-primary').trim());
    await page.locator('textarea').first().fill('hello');
    await page.keyboard.press('Enter');
    await expect(page.locator('.model-needed')).toBeVisible();
    await expect.poll(async () => brightness(await lastColor(page))).toBeLessThan(brightness(canvas));
    expect(await refusedColors(page)).toEqual([]);
});

/** Colors the desktop window would refuse: main.cjs takes only #rrggbb. */
const refusedColors = (page: Page): Promise<string[]> =>
    page.evaluate(() =>
        (window as unknown as { __colors: { color: string; symbolColor: string }[] }).__colors
            .flatMap((c) => [c.color, c.symbolColor])
            .filter((c) => !/^#[\da-f]{6}$/i.test(c)),
    );

/** The width Windows 11 gives minimize, maximize and close; a browser reports no controls. */
const WINDOW_CONTROLS_W = 138;

for (const locale of ['ar', 'en']) {
    test(`the top notice keeps its action clear of the window controls (${locale})`, async ({ page }) => {
        await open(page, '', locale, withWindowsControls);
        await page.evaluate((w) => document.documentElement.style.setProperty('--titlebar-controls-w', `${w}px`), WINDOW_CONTROLS_W);
        const notice = page.locator('.app-notice').first();
        await expect(notice.getByRole('button')).toBeVisible();
        const controls = { left: 1400 - WINDOW_CONTROLS_W, bottom: 48 };
        const boxes = await notice.evaluate((el) =>
            [el, ...el.querySelectorAll('button, a')].map((n) => {
                const box = n.getBoundingClientRect();
                return { right: box.right, top: box.top };
            }),
        );
        for (const box of boxes) {
            expect(box.right > controls.left && box.top < controls.bottom, JSON.stringify(box)).toBe(false);
        }
        expect(await audit(page)).toEqual([]);
    });
}

test('the agent notice and the memory chip are in Arabic', async ({ page }) => {
    await open(page);
    await expect(page.locator('.app-notice')).toContainText('غير مثبّت');
    await expect(page.getByRole('button', { name: /تفعيل المحادثة الخاصة/ })).toContainText(/\p{Script=Arabic}/u);
});

/** POST /api/chat/send streams `events`, then stays open, as a turn waiting on the user does. */
function streamThenWait(events: unknown[]): void {
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (!url.includes('/api/chat/send')) return realFetch(input, init);
        const encoder = new TextEncoder();
        const body = new ReadableStream<Uint8Array>({
            start(controller) {
                for (const event of events) controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
            },
        });
        return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
    };
}

const PYTHON_PROMPT = [{
    type: 'permission_request', tool: 'run_python', tool_label: 'running python', confirm_id: 'c-rtl',
    args: { code: 'p = r"C:\\Users\\you\\notes.txt"\nt = open(p, encoding="utf-8").read()\nprint(len(t))' },
}];

for (const locale of ['ar', 'en']) {
    test(`a permission prompt's code reads left to right (${locale})`, async ({ page }) => {
        await page.addInitScript(streamThenWait, PYTHON_PROMPT);
        await open(page, `#${ANSWER_SESSION}`, locale);
        await page.locator('textarea').first().fill('count the characters in my notes');
        await page.keyboard.press('Enter');
        const code = page.getByTestId('perm-code');
        await expect(code).toBeVisible();
        const style = await code.evaluate((el) => {
            const s = getComputedStyle(el);
            return { direction: s.direction, textAlign: s.textAlign };
        });
        expect(style.direction).toBe('ltr');
        expect(['left', 'start']).toContain(style.textAlign);
        // The first line's text starts at the block's left edge, not its right.
        const gap = await code.evaluate((el) => {
            const range = document.createRange();
            range.selectNodeContents(el.firstChild!);
            return range.getClientRects()[0].left - el.getBoundingClientRect().left;
        });
        expect(gap).toBeLessThan(16);
        const title = page.locator('.perm-card-title');
        if (locale === 'ar') await expect(title).toContainText('هل تسمح بـ');
        else await expect(title).toHaveText('Allow running python?');
        await expect(title.locator('bdi[dir="ltr"]').first()).toHaveText('running python');
        await expect(page.getByRole('button', { name: /Allow once/ })).toContainText(locale === 'ar' ? 'السماح مرة واحدة' : 'Allow once');
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.perm-card', 'bdi, pre')).toEqual([]);
        await shot(page, `permission-python-${locale}`);
    });
}

const PATH_PROMPT = [{
    type: 'permission_request', tool: 'allow_path_access', tool_label: 'allowing path access', confirm_id: 'c-path',
    args: { path: 'C:\\Users\\you\\Documents\\sales_data_2025.csv', kind: 'file' },
}];

for (const locale of ['ar', 'en']) {
    test(`a path-access prompt asks in the UI language, the path left to right (${locale})`, async ({ page }) => {
        await page.addInitScript(streamThenWait, PATH_PROMPT);
        await open(page, `#${ANSWER_SESSION}`, locale);
        await page.locator('textarea').first().fill('summarize my sales data');
        await page.keyboard.press('Enter');
        const title = page.locator('.perm-card-title');
        await expect(title).toBeVisible();
        const question = page.locator('.perm-card-question');
        if (locale === 'ar') {
            await expect(title).toContainText('هل تسمح لـ GAIA باستخدام هذا الملف؟');
            await expect(question).toContainText('يريد GAIA استخدام الملف');
        } else {
            await expect(title).toHaveText('Let GAIA use this file?');
            await expect(question).toContainText("GAIA wants to use the file");
        }
        await expect(question.locator('bdi[dir="ltr"]').first()).toHaveText('C:\\Users\\you\\Documents\\sales_data_2025.csv');
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.perm-card', 'bdi, pre')).toEqual([]);
        await shot(page, `permission-path-${locale}`);
    });
}

const REPEATED_PROMPTS = ['c1', 'c2', 'c3'].map((id) => ({
    ...PYTHON_PROMPT[0], confirm_id: id, always_scope: 'Python snippets (run_python)', risk: 'execute',
}));

for (const locale of ['ar', 'en']) {
    test(`the third identical prompt counts two earlier allows (${locale})`, async ({ page }) => {
        await page.addInitScript(streamThenWait, REPEATED_PROMPTS);
        await open(page, `#${ANSWER_SESSION}`, locale, (p) =>
            p.route('**/api/chat/confirm-tool', (route) => route.fulfill({ json: { status: 'ok', approved: true, granted: null } })));
        await page.locator('textarea').first().fill('count the characters in my notes');
        await page.keyboard.press('Enter');
        for (const answered of [1, 2]) {
            await page.getByRole('button', { name: /Allow once/ }).click();
            await expect(page.locator('.perm-card-hint')).toBeVisible();
            if (answered === 1) await expect(page.locator('.perm-card-hint')).toContainText(locale === 'ar' ? 'مرة واحدة' : 'once already');
        }
        await expect(page.locator('.perm-card-hint')).toContainText(locale === 'ar' ? 'من قبل: 2.' : 'allowed this 2 times');
        expect(await audit(page)).toEqual([]);
        await shot(page, `permission-repeated-${locale}`);
    });
}

/** A first message whose chat model isn't downloaded, on a disk too small for it. */
async function withMissingModel(page: Page): Promise<void> {
    await page.route('**/api/setup/needs/chat', (route) =>
        route.fulfill({
            json: {
                job: 'chat',
                needed: true,
                model: { id: 'Gemma-4-E4B-it-GGUF', name: 'Gemma 4 E4B (Multimodal)', size_gb: 5.97, source: 'huggingface.co/unsloth/gemma-4-E4B-it-GGUF' },
                disk_free_gb: 2.4,
            },
        }),
    );
}

for (const locale of ['ar', 'en']) {
    test(`the missing-model prompt warns about disk space (${locale})`, async ({ page }) => {
        await open(page, '', locale, withMissingModel);
        await page.locator('textarea').first().fill('hello');
        await page.keyboard.press('Enter');
        const dialog = page.locator('.model-needed');
        await expect(dialog).toBeVisible();
        await expect(dialog.getByRole('alert')).toBeVisible();
        expect(await audit(page)).toEqual([]);
        await expect(dialog.getByRole('alert')).toContainText(locale === 'ar' ? 'حرّر' : 'Free 3.6 GB first');
    });
}

/** The notice a chat shows when reading images is off because chat runs on the NPU. */
async function withImagesOffOnNpu(page: Page): Promise<void> {
    await page.route('**/api/setup/needs/chat', (route) =>
        route.fulfill({ json: { job: 'chat', needed: false, model: null, disk_free_gb: null, off: 'npu' } }),
    );
}

for (const locale of ['ar', 'en']) {
    test(`the NPU image notice says why and offers no download (${locale})`, async ({ page }) => {
        await open(page, '', locale, withImagesOffOnNpu);
        await page.locator('textarea').first().fill('hello');
        await page.keyboard.press('Enter');
        const dialog = page.locator('.model-needed');
        await expect(dialog).toBeVisible();
        await expect(dialog.getByRole('button')).toHaveCount(2);
        expect(await audit(page)).toEqual([]);
        await shot(page, `npu-image-notice-${locale}`);
    });
}

/** A measured context two turns from overflowing, so the meter, its popover and the warning all show. */
const MEASURED_CONTEXT = {
    available: true, window: 65536, reply_reserve: 4096, system: 3900, tools: 2600, documents: 4000, history: 11350,
    query: 0, used: 21850, history_budget: 20000, history_used: 11350, turns_in_view: 4, turns_hidden: 2,
    at_risk: ['لخّص تقرير الربع الثالث', 'من تأخر من الموردين؟'], at_risk_tokens: 3000,
    tokenizer: { exact: true, model: 'Gemma-4-E4B-it-GGUF' }, model: 'Gemma-4-E4B-it-GGUF',
};

async function withMeasuredContext(page: Page): Promise<void> {
    await page.route(`**/api/chat/context/${ANSWER_SESSION}`, (route) => route.fulfill({ json: MEASURED_CONTEXT }));
}

for (const locale of ['ar', 'en']) {
    test(`the context meter, its popover and its warning (${locale})`, async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withMeasuredContext);
        const meter = page.locator('button.context-meter');
        await expect(meter).toBeVisible();
        await expect(page.locator('.context-warning')).toBeVisible();
        await meter.click();
        await expect(page.locator('.context-meter-panel')).toBeVisible();
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') {
            expect(await untranslated(page, '.context-meter-panel')).toEqual([]);
            expect(await untranslated(page, '.context-warning')).toEqual([]);
        }
    });
}

for (const locale of ['ar', 'en']) {
    test(`the context meter names GAIA's window on a cloud model (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale, async (p) => {
            await p.route(`**/api/chat/context/${ANSWER_SESSION}`, (route) => route.fulfill({ json: { ...MEASURED_CONTEXT, model_window: 1048576 } }));
        });
        await page.locator('button.context-meter').click();
        const cap = page.locator('.context-meter-cap');
        await expect(cap).toBeVisible();
        await expect(cap).toContainText(locale === 'ar' ? 'من أصل نافذة النموذج البالغة' : "of the model's");
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.context-meter-panel')).toEqual([]);
        await page.screenshot({ path: info.outputPath('context-meter-cloud.png') });
    });
}

test('composer chips stay whole at phone width', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 780 });
    await open(page);
    const row = page.locator('.composer-chip').first();
    await expect(row).toBeVisible();
    const boxes = await page.locator('.composer-chip, .composer-send').evaluateAll((els) =>
        els.map((el) => {
            const { left, right, width } = el.getBoundingClientRect();
            return { left, right, width };
        }).filter((box) => box.width > 0),
    );
    expect(boxes.filter((box) => box.width < 24)).toEqual([]);
    const sorted = [...boxes].sort((x, y) => x.left - y.left);
    expect(sorted.filter((box, i) => i > 0 && box.left < sorted[i - 1].right - 1)).toEqual([]);
});

test('the library path row fits a phone', async ({ page }) => {
    await open(page);
    await page.locator('.sb').getByRole('button', { name: /المستندات/ }).click();
    await page.setViewportSize({ width: 390, height: 780 });
    const form = page.locator('.path-input-form');
    await expect(form).toBeVisible();
    const clipped = await form.locator('button, input').evaluateAll((els) =>
        els.filter((el) => {
            const box = el.getBoundingClientRect();
            return box.left < 0 || box.right > innerWidth;
        }).length,
    );
    expect(clipped).toBe(0);
});

/** The desktop app's updater in a given state; the browser preview has none. */
function withUpdater(update: Record<string, unknown>) {
    return async (page: Page) => {
        await page.addInitScript((patch) => {
            const state = {
                status: 'idle', version: null, progress: 0, releaseNotes: null, error: null,
                currentVersion: '0.25.4', pinnedVersion: null, autoUpdate: false, includePrereleases: false,
                previousVersion: null, startFailed: false, installFailed: null, lastCheckError: null,
                ...patch,
            };
            const resolved = async () => state;
            // A refusal as Electron's IPC delivers it: the code inside the message.
            const refuse = async () => {
                throw new Error(
                    "Error invoking remote method 'gaia:update:restart': Error: gaia-update-error:" +
                        JSON.stringify({ code: 'diskSpace', params: { need: '1 GB', free: '412 MB' }, message: 'GAIA needs about 1 GB free' }),
                );
            };
            Object.defineProperty(window, 'gaiaUpdater', {
                value: {
                    getStatus: resolved, check: resolved, listReleases: async () => [],
                    restart: refuse, onStatusChange: () => () => undefined,
                },
            });
        }, update);
    };
}

const UPDATE_STATES: Record<string, Record<string, unknown>> = {
    available: { status: 'available', version: '0.25.5-rc.3' },
    downloading: { status: 'downloading', version: '0.25.5-rc.3', progress: 42 },
    preparing: { status: 'downloading', version: '0.25.5-rc.3', progress: 100, preparing: true },
    ready: { status: 'downloaded', version: '0.25.5-rc.3' },
    'install failed': { installFailed: { target: '0.25.5-rc.3', from: '0.25.4' } },
    'did not start': { startFailed: true, previousVersion: '0.25.3' },
    'check failed': { status: 'error', error: 'net::ERR_INTERNET_DISCONNECTED' },
};

for (const locale of ['ar', 'en']) {
    test.describe(`updates (${locale})`, () => {
        for (const [name, update] of Object.entries(UPDATE_STATES)) {
            test(`chip: ${name}`, async ({ page }, info) => {
                await open(page, '', locale, withUpdater(update));
                await expect(page.locator('.update-chip')).toBeVisible();
                expect(await audit(page)).toEqual([]);
                await page.screenshot({ path: info.outputPath('chip.png') });
            });
        }

        test('settings, with the way back on offer', async ({ page }, info) => {
            await open(page, '', locale, withUpdater({
                previousVersion: '0.25.3',
                installFailed: { target: '0.25.5-rc.3', from: '0.25.4' },
                lastCheckError: 'net::ERR_INTERNET_DISCONNECTED',
            }));
            await page.getByRole('button', { name: /Settings/ }).first().click();
            await page.locator('#updates-label').scrollIntoViewIfNeeded();
            await expect(page.locator('#updates-back-hint')).toBeVisible();
            expect(await audit(page)).toEqual([]);
            await page.screenshot({ path: info.outputPath('settings.png') });
        });

        test('settings, after a restart refused for disk space', async ({ page }, info) => {
            await open(page, '', locale, withUpdater({ status: 'downloaded', version: '0.25.5-rc.3' }));
            await page.getByRole('button', { name: /Settings/ }).first().click();
            await page.locator('#updates-label').scrollIntoViewIfNeeded();
            await page.getByRole('button', { name: /Restart to install/ }).click();
            const error = page.locator('.settings-error').filter({ hasText: '412' });
            await expect(error).toBeVisible();
            await expect(error).toContainText(locale === 'ar' ? 'يحتاج GAIA إلى نحو' : 'needs about');
            // Isolated, a size reads "412 MB" in a right-to-left line, not "MB 412".
            if (locale === 'ar') await expect(error).toContainText('⁨412 MB⁩');
            await expect(error).not.toContainText('gaia-update-error');
            expect(await audit(page)).toEqual([]);
            await page.screenshot({ path: info.outputPath('settings-refused.png') });
        });
    });
}

/** Visible text on the Memory dashboard left in English; memories, names and codes sit in <bdi>. */
const memoryLeftovers = (page: Page): Promise<string[]> =>
    page.locator('.memory-dashboard-page').evaluate((root) => {
        const left = new Set<string>();
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
            const text = n.textContent?.trim() ?? '';
            const el = n.parentElement as Element;
            if (!/[A-Za-z]{3}/.test(text) || /[\u0600-\u06ff]/.test(text) || !el.checkVisibility()) continue;
            if (el.closest('.i18n-gloss, bdi, code, select, textarea')) continue;
            left.add(text.slice(0, 60));
        }
        for (const el of root.querySelectorAll('input[placeholder], textarea[placeholder]')) {
            const hint = el.getAttribute('placeholder') ?? '';
            // "global" is the default context's literal value, offered as the example to type.
            if (hint !== 'global' && /[A-Za-z]{3}/.test(hint) && !/[\u0600-\u06ff]/.test(hint)) left.add(`placeholder: ${hint}`);
        }
        return [...left];
    });

async function openMemory(page: Page, locale: string, state: MemoryState = {}, theme: 'light' | 'dark' = 'light'): Promise<void> {
    await open(page, '', locale, (p) => withMemory(p, state), theme);
    const sidebar = page.getByRole('button', { name: /Open sidebar/ });
    if (await sidebar.isVisible()) await sidebar.click();
    await page.locator('.sb').getByRole('button', { name: /Memory/ }).first().click();
    await expect(page.locator('.memory-dashboard-page')).toBeVisible();
}

const memoryTab = (page: Page, name: RegExp) => page.getByRole('tab', { name }).click();

/** The audit, plus no English left on an Arabic screen. */
async function checkMemory(page: Page, locale: string, name: string): Promise<void> {
    await shot(page, `memory-${locale}-${name}`);
    expect(await audit(page)).toEqual([]);
    if (locale === 'ar') expect(await memoryLeftovers(page)).toEqual([]);
}

for (const locale of ['ar', 'en']) {
    test.describe(`memory dashboard (${locale})`, () => {
        test('about you, with memories', async ({ page }) => {
            await openMemory(page, locale);
            await expect(page.getByText('Lives in Calgary and works at a hospital')).toBeVisible();
            await checkMemory(page, locale, 'about');
        });

        test('about you, editing a memory', async ({ page }) => {
            await openMemory(page, locale);
            const item = page.locator('.mem-about-item', { hasText: 'Lives in Calgary' });
            await item.locator('.mem-about-btn').first().click();
            await expect(item.locator('textarea')).toBeVisible();
            await checkMemory(page, locale, 'about-editing');
        });

        test('about you, empty', async ({ page }) => {
            await openMemory(page, locale, { empty: true });
            await expect(page.locator('.mem-about-empty')).toBeVisible();
            await checkMemory(page, locale, 'about-empty');
        });

        test('about you, memory off', async ({ page }) => {
            await openMemory(page, locale, { enabled: false });
            await expect(page.locator('.mem-about-empty .btn-secondary')).toBeVisible();
            await checkMemory(page, locale, 'about-off');
        });

        test('about you, failed to load', async ({ page }) => {
            await openMemory(page, locale, { failed: true });
            await expect(page.getByText('Memory store is locked', { exact: false })).toBeVisible();
            await checkMemory(page, locale, 'about-failed');
        });

        test('goals, suggested and one open with its tasks', async ({ page }) => {
            await openMemory(page, locale);
            await memoryTab(page, /Goals/);
            await page.locator('.mem-goal-header', { hasText: 'Ship the Q3 report' }).click();
            await expect(page.locator('.mem-task-row')).toHaveCount(2);
            await page.locator('.mem-goals-toolbar .btn-primary').click();
            await expect(page.locator('.mem-goals-add-form')).toBeVisible();
            await checkMemory(page, locale, 'goals');
        });

        test('goals, empty', async ({ page }) => {
            await openMemory(page, locale, { empty: true });
            await memoryTab(page, /Goals/);
            await expect(page.locator('.mem-goals-tab .mem-empty')).toBeVisible();
            await checkMemory(page, locale, 'goals-empty');
        });

        test('profile setup, before and after a discovery run', async ({ page }) => {
            await openMemory(page, locale);
            await memoryTab(page, /Profile setup/);
            await expect(page.locator('.mem-profile-card')).toHaveCount(2);
            await checkMemory(page, locale, 'profile');
            await page.locator('.mem-profile-run-btn').first().click();
            await expect(page.locator('.mem-review-item')).toHaveCount(2);
            await checkMemory(page, locale, 'profile-review');
        });

        test('advanced, with stats, tables and settings', async ({ page }) => {
            await openMemory(page, locale);
            await memoryTab(page, /Advanced/);
            await expect(page.getByText('run_shell')).toBeVisible();
            // Unisolated, a percentage reads "%100" in a right-to-left line.
            if (locale === 'ar') await expect(page.locator('.mem-embedding-label')).toContainText('\u2068100%\u2069');
            await checkMemory(page, locale, 'advanced');
            await page.locator('.memory-dashboard-body').evaluate((el) => { el.scrollTop = el.scrollHeight; });
            await checkMemory(page, locale, 'advanced-end');
        });

        test('advanced, a row open and the add form', async ({ page }) => {
            await openMemory(page, locale);
            await memoryTab(page, /Advanced/);
            await page.locator('.mem-knowledge-table tbody tr', { hasText: 'Lives in Calgary' }).locator('td').first().click();
            await expect(page.locator('.mem-detail-panel')).toBeVisible();
            await page.locator('.mem-section-add-btn').click();
            await expect(page.locator('.mem-inline-form')).toBeVisible();
            await checkMemory(page, locale, 'advanced-open');
        });

        test('advanced, empty', async ({ page }) => {
            await openMemory(page, locale, { empty: true });
            await memoryTab(page, /Advanced/);
            await expect(page.locator('.mem-stat-cards')).toBeVisible();
            await checkMemory(page, locale, 'advanced-empty');
        });

        test('advanced, memory off with the enable dialog open', async ({ page }) => {
            await openMemory(page, locale, { enabled: false });
            await memoryTab(page, /Advanced/);
            await page.locator('.mem-toggle').first().click();
            await expect(page.getByRole('dialog')).toBeVisible();
            await checkMemory(page, locale, 'advanced-dialog');
        });

        test('advanced in the dark theme', async ({ page }) => {
            await openMemory(page, locale, {}, 'dark');
            await memoryTab(page, /Advanced/);
            await expect(page.getByText('run_shell')).toBeVisible();
            await checkMemory(page, locale, 'advanced-dark');
        });

        test('phone width', async ({ page }) => {
            await page.setViewportSize({ width: 390, height: 844 });
            await openMemory(page, locale);
            // The closed drawer waits off screen by design; this test is about the dashboard.
            await page.addStyleTag({ content: '.sb:not(.is-open) { display: none; }' });
            await expect(page.getByText('Lives in Calgary and works at a hospital')).toBeVisible();
            await checkMemory(page, locale, 'about-phone');
            await memoryTab(page, /Advanced/);
            await expect(page.getByText('run_shell')).toBeVisible();
            await checkMemory(page, locale, 'advanced-phone');
        });
    });
}
