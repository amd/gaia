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

import { ANSWER_SESSION, AUTOMATIONS, DISCORD_STATES, MESSAGES, MODEL_LIST, NOW, RESOURCES, SESSIONS, SYSTEM_STATUS, TOOL_STATUS, holdOnToolStatus, mockApi, shownText, useLocale, useTheme, withAutomations, withConnectors, withCrashedBackend, withDesktopBridges, withDiscord, withMemory, withModelPage, withSettingsPanes, withoutFlagship, type MemoryState } from './fixtures';
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

let unhandled: string[] = [];

/** A 501 from mockApi that reached the screen; the screen shows a fixture gap, not the app. */
async function expectNoFixtureGap(page: Page): Promise<void> {
    await expect(page.locator('body'), 'a screen shows a missing fixture').not.toContainText('no fixture for');
}

test.afterEach(async ({ page }) => {
    const seen = unhandled;
    unhandled = [];
    expect(seen, 'add a fixture to visual/fixtures.ts ROUTES for each call').toEqual([]);
    await expectNoFixtureGap(page);
});

async function open(page: Page, hash = '', locale = 'ar', before?: (page: Page) => Promise<void>, theme: 'light' | 'dark' = 'light'): Promise<void> {
    await page.clock.setFixedTime(NOW);
    unhandled = await mockApi(page);
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
            // Announced-only setup guidance is intentionally clipped from the visual layout.
            if (el.closest('.setup-a11y-alert')) return false;
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

        // An <option> holds plain text, so the English hint in brackets can't be
        // styled quietly there and its brackets scramble in rtl; use t.bare.
        // Language pickers (options tagged with lang) name each language in English on purpose.
        for (const option of document.querySelectorAll('option:not([lang])')) {
            const text = option.textContent ?? '';
            if (arabic.test(text) && /\([^)]*[A-Za-z]{3,}[^)]*\)/.test(text)) {
                problems.push(`an <option> carries the English hint: "${text.trim().slice(0, 50)}"`);
            }
        }

        for (const el of document.querySelectorAll('body *')) {
            if (!shown(el)) continue;
            const box = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            // Screen-reader-only text is clipped to 1px, so its unwrapped overflow is never seen.
            const srOnly = el.closest('.visually-hidden');
            if ((box.right > innerWidth + 1 || box.left < -1) && style.position !== 'fixed' && !el.closest('svg') && !srOnly && !inSideScroller(el)) {
                problems.push(`off screen: ${label(el)}`);
            }

            // A flex row can squeeze an icon to a speck when its neighbours are long.
            if (el.tagName.toLowerCase() === 'svg' && box.width < 8 && el.closest('.composer-attachment')) {
                problems.push(`icon squeezed to ${box.width.toFixed(1)}px: ${label(el.parentElement!)}`);
            }

            const text = el.textContent?.trim() ?? '';
            if (el.querySelector(':scope > :not(i18n-label)') || !text) continue;

            // A long name that shrinks to nothing tells the reader nothing.
            if (style.textOverflow === 'ellipsis' && box.width < 40 && el.scrollWidth > box.width + 1) {
                problems.push(`text squeezed to ${Math.round(box.width)}px: ${label(el)}`);
            }

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

        // An amount with its unit or sign reads as one token; split over two lines it reads as two.
        for (const amount of document.querySelectorAll('.popover bdi[dir="ltr"]')) {
            const text = amount.textContent?.trim() ?? '';
            if (!shown(amount) || text.length > 16 || !/\d/.test(text)) continue;
            const lines = new Set([...amount.getClientRects()].map((r) => Math.round(r.top)));
            if (lines.size > 1) problems.push(`an amount wraps across lines: ${label(amount)}`);
        }

        // A popover that opens scrolled down hides its first lines behind the fold.
        for (const popover of document.querySelectorAll('.popover')) {
            if (shown(popover) && popover.scrollTop > 0) problems.push(`popover opens scrolled past its top: ${label(popover)}`);
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

// A reply that never ends, so the card stays up for the screenshot.
const RECORDING_EVENTS = [
    { type: 'status', status: 'working', message: 'x', phase: 'recording', stage: 0, state: 1 },
    { type: 'status', status: 'working', message: 'x', phase: 'recording', stage: 2, state: 0, percent: 42, eta_seconds: 330 },
];

for (const [title, locale] of [
    ['recording in progress, Arabic', 'ar'],
    ['recording in progress, English', 'en'],
] as const) {
    test(title, async ({ page }, info) => {
        await page.addInitScript((events) => {
            const real = window.fetch;
            window.fetch = (input, init) => {
                if (!String(input).includes('/chat/send')) return real(input, init);
                const body = new ReadableStream({
                    start(controller) {
                        for (const event of events) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
                    },
                });
                return Promise.resolve(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
            };
        }, RECORDING_EVENTS);
        await open(page, `#${ANSWER_SESSION}`, locale);
        await expect(page.getByText('Action items from Aug 28')).toBeVisible();
        await page.locator('textarea').first().fill('Transcribe this');
        await page.keyboard.press('Enter');
        await expect(page.locator('.recording-progress-track')).toBeVisible();
        expect(await audit(page)).toEqual([]);
        const fill = page.locator('.recording-progress-fill');
        const track = page.locator('.recording-progress-track');
        const [fillBox, trackBox] = [await fill.boundingBox(), await track.boundingBox()];
        expect(fillBox!.width / trackBox!.width).toBeCloseTo(0.42, 1);
        const startsAtStart = locale === 'ar'
            ? Math.abs(fillBox!.x + fillBox!.width - (trackBox!.x + trackBox!.width))
            : Math.abs(fillBox!.x - trackBox!.x);
        expect(startsAtStart).toBeLessThan(1);
        await page.screenshot({ path: info.outputPath(`recording-progress-${locale}.png`) });
    });
}

// Sections read so far while the rest are analyzed, one in each script.
const FOUND_EVENTS = [
    { type: 'status', status: 'working', message: 'x', phase: 'recording', stage: 5, state: 0, percent: 67, eta_seconds: 40 },
    {
        type: 'status', status: 'working', message: 'x', phase: 'recording_found',
        section: 1, sections: 3, start: '00:00', end: '03:31',
        summary: 'The speaker introduces the talk and begins his first story about dropping out of college.',
        items: [{ title: 'Introduction and three stories', start: '00:21', seconds: 21 }, { title: 'Story 1: connecting the dots', start: '00:55', seconds: 55 }],
    },
    {
        type: 'status', status: 'working', message: 'x', phase: 'recording_found',
        section: 2, sections: 3, start: '02:06', end: '1:08:54',
        summary: 'يتحدث عن تركه الجامعة وعن دروس الخط العربي.',
        items: [{ title: 'ترك الجامعة واتباع الفضول', start: '02:06', seconds: 126 }],
    },
];

for (const [title, locale] of [
    ['recording found so far, Arabic', 'ar'],
    ['recording found so far, English', 'en'],
] as const) {
    test(title, async ({ page }, info) => {
        await page.addInitScript((events) => {
            const real = window.fetch;
            window.fetch = (input, init) => {
                if (!String(input).includes('/chat/send')) return real(input, init);
                const body = new ReadableStream({
                    start(controller) {
                        for (const event of events) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
                    },
                });
                return Promise.resolve(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
            };
        }, FOUND_EVENTS);
        await open(page, `#${ANSWER_SESSION}`, locale);
        await expect(page.getByText('Action items from Aug 28')).toBeVisible();
        await page.locator('textarea').first().fill('Transcribe this');
        await page.keyboard.press('Enter');
        const found = page.locator('.recording-found');
        await expect(found.locator('.recording-found-section')).toHaveCount(2);
        expect(await audit(page)).toEqual([]);
        // Times read left to right in both directions, and sit at the start edge.
        const span = found.locator('.recording-found-span .recording-found-time').nth(1);
        await expect(span).toHaveText('02:06–1:08:54');
        await expect(span).toHaveAttribute('dir', 'ltr');
        const row = found.locator('.recording-found-items li').first();
        const [time, rowBox] = [await row.locator('.recording-found-time').boundingBox(), await row.boundingBox()];
        const atStart = locale === 'ar'
            ? Math.abs(time!.x + time!.width - (rowBox!.x + rowBox!.width))
            : Math.abs(time!.x - rowBox!.x);
        expect(atStart).toBeLessThan(1);
        await found.screenshot({ path: info.outputPath(`recording-found-${locale}.png`) });
    });
}

test('a rejected attachment keeps its message whole and leaves the other chips alone', async ({ page }) => {
    await open(page, '', 'en');
    await page.route((url) => url.pathname === '/api/files/upload', async (route) => {
        const body = route.request().postData() ?? '';
        if (body.includes('notes.xyz')) {
            await route.fulfill({ status: 400, json: { detail: "File type '.xyz' is not allowed. Supported types: images (png, jpg, jpeg, gif, webp, bmp, svg) and documents (pdf, pptx, docx, txt, md, csv, json, xlsx, html, code files, etc.)." } });
        } else {
            await route.fulfill({ json: { filename: 'q.txt', original_name: 'Quarterly notes.txt', url: '/api/files/uploads/q.txt', size: 5, content_type: 'text/plain', is_image: false } });
        }
    });
    await page.locator('input[type="file"]').first().setInputFiles([
        { name: 'Quarterly notes.txt', mimeType: 'text/plain', buffer: Buffer.from('notes') },
        { name: 'notes.xyz', mimeType: 'application/octet-stream', buffer: Buffer.from('x') },
    ]);
    const bad = page.locator('.composer-attachment.has-error');
    await expect(bad).toHaveCount(1);
    const message = bad.locator('.composer-attachment-state');
    expect(await message.evaluate((el) => el.scrollHeight <= el.clientHeight + 1)).toBe(true);
    const good = page.locator('.composer-attachment:not(.has-error)').first();
    expect((await good.boundingBox())!.height).toBeLessThan(40);
    await shot(page, 'attachment-rejected');
});

for (const [title, locale, name] of [
    ['recording ready to send', 'ar', 'Quarterly business review recording with a very long file name 2026.mp4'],
    ['recording with an Arabic name, in English', 'en', 'تسجيل مراجعة الأعمال الفصلية.m4a'],
] as const) {
    test(title, async ({ page }, info) => {
        await open(page, '', locale);
        await page.route((url) => url.pathname === '/api/files/upload-media', (route) =>
            route.fulfill({
                json: { filename: 'r.mp4', original_name: name, url: '/api/files/uploads/r.mp4', size: 5, content_type: 'video/mp4', is_image: false },
            }),
        );
        await page.locator('input[type="file"]').first().setInputFiles({ name, mimeType: 'video/mp4', buffer: Buffer.from('media') });
        await expect(page.locator('.composer-attachment-state:not([role="alert"])')).toBeVisible();
        await expect(page.locator('.composer-attachment.has-error')).toHaveCount(0);
        // A long name is cut in the middle, so its extension stays in view.
        await expect(page.locator('.composer-attachment-name')).toHaveText(/\.(mp4|m4a)$/);
        await shot(page, `attachment-name-${locale}`);
        expect(await audit(page)).toEqual([]);
        await page.screenshot({ path: info.outputPath('recording.png') });
    });
}

test('new chat', async ({ page }) => {
    await open(page);
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.locator('.sb-location-name')).toBeVisible();
    expect(await audit(page)).toEqual([]);
});

for (const locale of ['en', 'ar']) {
    test(`the new-chat message box stays put while typing (${locale})`, async ({ page }) => {
        await open(page, '', locale);
        const box = page.locator('.new-chat textarea').first();
        await expect(box).toBeVisible();
        const before = (await box.boundingBox())!;
        await box.fill('h');
        await shot(page, `new-chat-typed-${locale}`);
        const after = (await box.boundingBox())!;
        expect(Math.abs(after.y - before.y), 'the box jumped on the first keystroke').toBeLessThan(2);
        await expect(page.locator('.new-chat-greeting')).toBeVisible();
    });
}

for (const locale of ['en', 'ar']) {
    test(`the folder control says what it does, not GAIA's own folder name (${locale})`, async ({ page }) => {
        await open(page, '', locale, (p) => p.route('**/api/onboarding/start', (route) => route.fulfill({
            json: { cards: [], working_folder: { path: '/home/you/.gaia/documents', name: 'documents', gaia_folder: true, choosable: true } },
        })));
        const add = page.locator('.new-chat-meta button.new-chat-folder');
        await expect(add).toBeVisible();
        await expect(page.locator('.new-chat-meta')).not.toContainText('documents');
        await shot(page, `new-chat-add-folder-${locale}`);
        expect(await audit(page)).toEqual([]);
    });
}

for (const locale of ['en', 'ar']) {
    test(`chat titles in the sidebar use the whole row (${locale})`, async ({ page }) => {
        await open(page, '', locale);
        const row = page.locator('.sb-row').filter({ hasText: 'Compare two lease PDFs' });
        await expect(row).toBeVisible();
        const rowBox = (await row.boundingBox())!;
        const titleBox = (await row.locator('.sb-row-title').boundingBox())!;
        // Rename, pin and delete appear over the row on hover, not in space kept beside the title.
        expect(titleBox.width + 30, 'title cut short to leave room for hidden buttons').toBeGreaterThan(rowBox.width * 0.85);
        await row.hover();
        await expect(row.locator('.sb-row-delete')).toBeVisible();
        await shot(page, `sidebar-row-hover-${locale}`);
        expect(await audit(page)).toEqual([]);
        // While renaming, pin and delete sit beside the name box, not over the end of what is typed.
        await row.locator('.sb-row-rename').click();
        // The title gives way to the name box, so find the row by it now.
        const edit = page.locator('.sb-row-edit');
        await expect(edit).toBeFocused();
        const editBox = (await edit.boundingBox())!;
        const actionsBox = (await page.locator('.sb-row:has(.sb-row-edit) .sb-row-actions').boundingBox())!;
        const overlap = Math.min(editBox.x + editBox.width, actionsBox.x + actionsBox.width) - Math.max(editBox.x, actionsBox.x);
        expect(overlap, 'pin and delete cover the name being typed').toBeLessThanOrEqual(0);
    });
}

for (const locale of ['ar', 'en']) {
    for (const width of [1440, 390]) {
        test(`Try chips fit in ${locale} at ${width}px`, async ({ page }) => {
            await page.setViewportSize({ width, height: 900 });
            await open(page, '', locale);
            const chips = page.locator('.try-chip');
            await expect(chips).toHaveCount(7);
            await expect(chips.first()).toBeVisible();
            if (locale === 'ar') await expect(chips.first()).toContainText('وصفة');
            await shot(page, `try-chips-${locale}-${width}`);
            // The closed sidebar drawer waits off screen at phone width; judge the chips.
            const problems = (await audit(page)).filter((p) => p.includes('try-chip') || p.includes('scrolls sideways'));
            expect(problems).toEqual([]);
        });
    }
}

test('finished answer', async ({ page }) => {
    await open(page, `#${ANSWER_SESSION}`);
    await expect(page.getByText('Action items from Aug 28')).toBeVisible();
    expect(await audit(page)).toEqual([]);
});

const RECIPE_CARD = {
    kind: 'guide', guideType: 'recipe', title: 'خبز الموز', description: 'رطب وسهل التحضير.',
    notes: 'يحفظ ثلاثة أيام.', servings: 8,
    items: ['2 cups flour', '3 ripe bananas', '1/2 cup sugar', '1 teaspoon baking soda'],
    steps: ['Mix: اخلط المكونات الجافة', 'Bake 60 minutes at 350°F'],
};
const LOAN_CARD = {
    kind: 'calculator', calculator: 'loan', title: '', currency: 'EUR',
    inputs: { amount: 300_000, annualRatePercent: 6.5, years: 30, downPayment: 60_000 },
};

async function withGuideCards(page: Page): Promise<void> {
    await page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) =>
        route.fulfill({
            json: {
                messages: [
                    { id: 1, session_id: ANSWER_SESSION, role: 'user', content: 'وصفة خبز الموز وقسط قرض', created_at: NOW.toISOString(), rag_sources: null },
                    {
                        id: 2, session_id: ANSWER_SESSION, role: 'assistant', content: 'بالهناء والشفاء.', created_at: NOW.toISOString(), rag_sources: null,
                        agent_steps: [
                            { id: 1, type: 'tool', tool: 'guides/show_recipe', active: false, success: true, render: 'guide', data: RECIPE_CARD },
                            { id: 2, type: 'tool', tool: 'calculators/show_loan_calculator', active: false, success: true, render: 'calculator', data: LOAN_CARD },
                        ],
                    },
                ],
                total: 2,
            },
        }),
    );
}

for (const locale of ['ar', 'en']) {
    test(`recipe and loan cards lay out in the interface direction (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withGuideCards);
        await expect(page.locator('.render-guide__title', { hasText: 'خبز الموز' })).toBeVisible();
        await expect(page.locator('.render-calc')).toBeVisible();
        expect(await audit(page)).toEqual([]);
        await page.locator('.render-guide').first().screenshot({ path: info.outputPath(`guide-card-${locale}.png`) });
        await page.locator('.render-calc').screenshot({ path: info.outputPath(`loan-card-${locale}.png`) });
    });
}

/** A new chat whose empty history is still loading when its first message goes out. */
async function withSlowNewChat(page: Page): Promise<void> {
    const created = { ...SESSIONS[0], title: 'New chat', message_count: 0 };
    await page.route((url) => url.pathname === '/api/sessions', (route) =>
        route.request().method() === 'POST' ? route.fulfill({ json: created }) : route.fallback());
    await page.route((url) => url.pathname === `/api/chat/permissions/${ANSWER_SESSION}`, (route) =>
        route.request().method() === 'PUT' || route.request().method() === 'POST'
            ? route.fulfill({ json: { session_id: ANSWER_SESSION, mode: 'ask', grants: [] } })
            : route.fallback());
    await page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, () => { /* never answers */ });
}

for (const locale of ['ar', 'en']) {
    test(`a new chat's first message shows at once, with no skeleton (${locale})`, async ({ page }, info) => {
        await open(page, '', locale, withSlowNewChat);
        await page.locator('textarea').first().fill('Give me three tips for better sleep.');
        await page.keyboard.press('Enter');
        await expect(page.locator('.msg-user')).toHaveText('Give me three tips for better sleep.');
        await expect(page.locator('.skeleton-messages')).toHaveCount(0);
        await expect(page.locator('.streaming-bubble .thinking-indicator')).toBeVisible();
        await expect(page.locator('.task-title')).toHaveText(locale === 'en' ? 'New chat' : /محادثة جديدة/);
        expect(await audit(page)).toEqual([]);
        await page.screenshot({ path: info.outputPath(`first-send-${locale}.png`) });
    });
}

async function withFailedAnswer(page: Page): Promise<void> {
    await page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) =>
        route.fulfill({
            json: {
                messages: [
                    { id: 1, session_id: ANSWER_SESSION, role: 'user', content: 'Give me three tips for better sleep.', created_at: NOW.toISOString(), rag_sources: null },
                    {
                        id: 2, session_id: ANSWER_SESSION, role: 'assistant', created_at: NOW.toISOString(), rag_sources: null,
                        content: 'Error: `fireworks.no-such-model` isn\'t available on Fireworks AI (HTTP 404).',
                        inference_stats: { seconds: 2, place: 'via Fireworks AI' },
                    },
                ],
                total: 2,
            },
        }),
    );
}

for (const locale of ['ar', 'en']) {
    test(`a failed answer is footed as failed (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withFailedAnswer);
        const footer = page.locator('.msg-assistant .msg-stats-summary');
        await expect(footer).toContainText(locale === 'en' ? 'Failed after' : 'فشلت بعد');
        expect(await audit(page)).toEqual([]);
        await page.locator('.msg-assistant').hover();
        await page.locator('.msg-assistant').screenshot({ path: info.outputPath(`failed-answer-${locale}.png`) });
    });
}

for (const locale of ['ar', 'en']) {
    test(`the latest answer's actions sit under its text, at the start edge (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale);
        const answer = page.locator('.msg-assistant').last();
        await expect(answer).toContainText('Action items from Aug 28');
        const actions = answer.locator('.msg-actions');
        await expect(actions).toHaveCSS('opacity', '1');
        const [row, body] = [await actions.boundingBox(), await answer.locator('.msg-body').boundingBox()];
        expect(row!.y).toBeGreaterThanOrEqual(body!.y + body!.height - 1);
        const startGap = locale === 'ar'
            ? Math.abs(row!.x + row!.width - (body!.x + body!.width))
            : Math.abs(row!.x - body!.x);
        expect(startGap).toBeLessThan(8);
        for (const button of await actions.locator('button').all()) {
            const box = await button.boundingBox();
            expect(Math.min(box!.width, box!.height)).toBeGreaterThanOrEqual(28);
        }
        expect(await audit(page)).toEqual([]);
        await answer.screenshot({ path: info.outputPath(`answer-actions-${locale}.png`) });
    });
}

for (const locale of ['ar', 'en']) {
    test(`a sent message opened for editing (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale);
        await expect(page.getByText('Action items from Aug 28')).toBeVisible();
        await page.locator('textarea').first().focus();
        await page.keyboard.press('ArrowUp');
        const editor = page.locator('.msg-edit-input');
        await expect(editor).toBeFocused();
        await editor.press('End');
        await page.keyboard.type(' and the owners');
        expect(await audit(page)).toEqual([]);
        const [box, bubble] = [await editor.boundingBox(), await page.locator('.msg-user:has(.msg-edit) .msg-inner').boundingBox()];
        expect(box!.width).toBeGreaterThan(bubble!.width * 0.8);
        await page.screenshot({ path: info.outputPath(`edit-message-${locale}.png`) });
        await page.locator('.msg-assistant').last().hover();
        await page.screenshot({ path: info.outputPath(`answer-actions-${locale}.png`) });
    });
}

/** A turn GAIA re-checked: the model wrote its answer, read the file back, then wrote it again. */
const CHECKED_ANSWER = [
    'Done — except the last step, which you declined.',
    '',
    '| Month | Total |',
    '|---|---|',
    '| October 2026 | 47.70 |',
    '| November 2026 | 36.75 |',
    '| **Grand total** | **84.45** |',
    '',
    '**Renamed:** `draft_old.txt` → `draft_archive.txt`',
].join('\n');

async function withCheckedAnswer(page: Page): Promise<void> {
    const step = (id: number, extra: Record<string, unknown>) => ({ id, active: false, timestamp: id, ...extra });
    await page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) =>
        route.fulfill({
            json: {
                messages: [
                    { id: 1, session_id: ANSWER_SESSION, role: 'user', content: 'Total both expense files into summary.md and rename draft_old.txt', created_at: NOW.toISOString(), rag_sources: null },
                    {
                        id: 2, session_id: ANSWER_SESSION, role: 'assistant', content: `${CHECKED_ANSWER}\n\nSay the word and I'll add the line.`, created_at: NOW.toISOString(), rag_sources: null,
                        agent_steps: [
                            step(1, { type: 'tool', label: 'Read file', tool: 'read_file', success: true }),
                            step(2, { type: 'tool', label: 'Write file', tool: 'write_file', success: true }),
                            step(3, { type: 'commentary', label: 'Commentary', detail: CHECKED_ANSWER }),
                            step(4, { type: 'tool', label: 'Read file', tool: 'read_file', success: true }),
                            step(5, { type: 'commentary', label: 'Commentary', detail: CHECKED_ANSWER.replace('declined.', 'declined (read back to confirm).') }),
                            step(6, { type: 'tool', label: 'Read file', tool: 'read_file', success: true }),
                        ],
                    },
                ],
                total: 2,
            },
        }),
    );
}

for (const locale of ['ar', 'en']) {
    test(`a re-checked answer shows once (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withCheckedAnswer);
        await expect(page.locator('.md-table')).toHaveCount(1);
        await expect(page.locator('.msg-assistant')).not.toContainText('read back to confirm');
        expect(await audit(page)).toEqual([]);
        await page.screenshot({ path: info.outputPath(`checked-answer-${locale}.png`) });
    });
}

async function withStoppedAnswer(page: Page): Promise<void> {
    await page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) =>
        route.fulfill({
            json: {
                messages: [
                    { id: 1, session_id: ANSWER_SESSION, role: 'user', content: 'Write about the history of canals', created_at: NOW.toISOString(), rag_sources: null },
                    { id: 2, session_id: ANSWER_SESSION, role: 'assistant', content: 'The earliest canals carried water to fields, not boats. In Mesopotamia', created_at: NOW.toISOString(), rag_sources: null, stopped: true },
                ],
                total: 2,
            },
        }),
    );
}

for (const locale of ['ar', 'en']) {
    test(`a stopped answer offers Continue and Try again (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withStoppedAnswer);
        const actions = page.locator('.msg-assistant .msg-try-again');
        await expect(actions).toHaveCount(2);
        expect(await audit(page)).toEqual([]);
        await page.locator('.msg-assistant').screenshot({ path: info.outputPath(`stopped-answer-${locale}.png`) });
    });
}

/** The Connect card a mail tool sends when no account is usable. */
const CONNECT_CARD = { kind: 'connect_account', providers: ['google', 'microsoft'], agent_id: 'installed:gaia', success: false, status: 'error', display_message: 'Needs a connected account' };

async function withConnectCard(page: Page): Promise<void> {
    await page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) =>
        route.fulfill({
            json: {
                messages: [
                    { id: 1, session_id: ANSWER_SESSION, role: 'user', content: 'Did my landlord email me about the lease?', created_at: NOW.toISOString(), rag_sources: null },
                    {
                        id: 2, session_id: ANSWER_SESSION, role: 'assistant', created_at: NOW.toISOString(), rag_sources: null,
                        // No marker and no command: the button comes from the tool result alone.
                        content: "I can't check yet because no mail account is connected. Use the Connect button above, then ask me again.",
                        agent_steps: [
                            { id: 1, type: 'tool', tool: 'list_inbox', active: false, success: false, render: 'connect_account', data: CONNECT_CARD },
                        ],
                    },
                ],
                total: 2,
            },
        }),
    );
}

for (const locale of ['ar', 'en']) {
    test(`a tool with no account shows the Connect card (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withConnectCard);
        const card = page.locator('.render-connect');
        await expect(card.locator('.render-connect__button')).toHaveCount(2);
        await expect(card.locator('.render-connect__button').first()).toContainText('Google');
        await expect(card).not.toContainText('gaia connectors');
        expect(await audit(page)).toEqual([]);
        await card.screenshot({ path: info.outputPath(`connect-card-${locale}.png`) });
        await shot(page, `connect-card-${locale}`);
    });

    test(`the Connect card says which account is already connected (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale, async (p) => { await withConnectCard(p); await withConnectors(p); });
        const card = page.locator('.render-connect');
        await expect(card.locator('.render-connect__connected')).toHaveCount(1);
        await expect(card.locator('.render-connect__button--quiet')).toHaveCount(1);
        // Returning with the account connected offers to run the question again.
        await expect(page.locator('.connector-retry-banner')).toBeVisible();
        expect(await audit(page)).toEqual([]);
        await card.screenshot({ path: info.outputPath(`connect-card-connected-${locale}.png`) });
        await shot(page, `connect-card-connected-${locale}`);
    });
}

test('the Connect card fits a phone', async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page, `#${ANSWER_SESSION}`, 'ar', withConnectCard);
    await expect(page.locator('.render-connect__button')).toHaveCount(2);
    // The closed sidebar drawer waits off screen at this width; judge the card.
    const problems = (await audit(page)).filter((p) => p.includes('render-connect') || p.includes('scrolls sideways'));
    expect(problems).toEqual([]);
    await page.locator('.render-connect').screenshot({ path: info.outputPath('connect-card-phone.png') });
    await shot(page, 'connect-card-phone');
});

async function withLongCode(page: Page): Promise<void> {
    const code = Array.from({ length: 24 }, (_, i) => `    total += row[${i}]  # add column ${i}`).join('\n');
    await page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) =>
        route.fulfill({
            json: {
                messages: [
                    { id: 1, session_id: ANSWER_SESSION, role: 'user', content: 'Sum the columns in Python', created_at: NOW.toISOString(), rag_sources: null },
                    {
                        id: 2, session_id: ANSWER_SESSION, role: 'assistant', created_at: NOW.toISOString(), rag_sources: null,
                        content: `Here it is:\n\n\`\`\`python\ndef total(row):\n    total = 0\n${code}\n    return total\n\`\`\`\n\nRun it on each row.`,
                    },
                ],
                total: 2,
            },
        }),
    );
}

for (const locale of ['ar', 'en']) {
    test(`a long code block keeps Copy in view while it scrolls (${locale})`, async ({ page }, info) => {
        await page.setViewportSize({ width: 1400, height: 560 });
        await open(page, `#${ANSWER_SESSION}`, locale, withLongCode);
        const block = page.locator('.code-block');
        await expect(block).toBeVisible();
        const scroller = page.locator('.messages-scroll');
        const [top, blockBox] = [await scroller.boundingBox(), await block.boundingBox()];
        await scroller.evaluate((el, by) => { el.scrollTop += by; }, blockBox!.y - top!.y + 120);
        const header = await page.locator('.code-header').boundingBox();
        const after = await block.boundingBox();
        expect(after!.y).toBeLessThan(top!.y);
        // Unstuck, the header would be 120 px above the transcript's top edge.
        expect(header!.y).toBeGreaterThanOrEqual(top!.y - 1);
        expect(header!.y - top!.y).toBeLessThan(16);
        await expect(page.locator('.code-copy')).toBeInViewport();
        expect(await audit(page)).toEqual([]);
        await page.screenshot({ path: info.outputPath(`sticky-code-${locale}.png`) });
    });
}

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

/** A quoted path with " - " and double spaces; the chip and the prompt must show it as typed. */
const QUOTED_PATH = 'C:\\gtest\\run-rc11\\fx\\Team Docs  -  AMD Shared\\budget.txt';

async function withQuotedPath(page: Page): Promise<void> {
    await page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) =>
        route.fulfill({
            json: {
                messages: [
                    { id: 1, session_id: ANSWER_SESSION, role: 'user', content: `Please index "${QUOTED_PATH}" into my document library`, created_at: NOW.toISOString(), rag_sources: null },
                ],
            },
        }),
    );
}

for (const locale of ['ar', 'en']) {
    test(`a quoted path with double spaces is one chip, spaces kept (${locale})`, async ({ page }, info) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withQuotedPath);
        const chips = page.locator('.file-path-link');
        await expect(chips).toHaveCount(1);
        // innerText follows white-space, so collapsed spaces would show here; toHaveText would hide them.
        expect(await chips.evaluateAll((els) => els.map((el) => (el as HTMLElement).innerText))).toEqual([QUOTED_PATH]);
        expect(await audit(page)).toEqual([]);
        await page.screenshot({ path: info.outputPath('quoted-path.png') });
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

const QUESTION_EVENTS = [
    {
        type: 'needs_input', request_id: 'q-1', question: 'Which colour do you prefer?',
        options: [
            { value: 'red', label: 'Red', description: '' },
            { value: 'green', label: 'Green', description: '' },
            { value: 'blue', label: 'Blue', description: '' },
        ],
        allow_free_text: true, sensitive: false, timeout_seconds: 300,
    },
];

for (const locale of ['en', 'ar']) {
    test(`a waiting question is answered from the message box and kept as a record (${locale})`, async ({ page }) => {
        await page.addInitScript((events) => {
            const real = window.fetch;
            window.fetch = (input, init) => {
                if (!String(input).includes('/chat/send')) return real(input, init);
                const body = new ReadableStream({
                    start(controller) {
                        for (const event of events) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
                    },
                });
                return Promise.resolve(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
            };
        }, QUESTION_EVENTS);
        await open(page, `#${ANSWER_SESSION}`, locale);
        await page.route('**/api/chat/user-input', (route) => route.fulfill({ json: { status: 'ok', request_id: 'q-1' } }));
        const box = page.getByRole('textbox').first();
        await box.fill('Ask me a colour');
        await box.press('Enter');
        await expect(page.locator('.render-needs-input__option')).toHaveCount(3);
        await expect(box).toBeFocused();
        await expect(page.locator('.render-needs-input__hint').last(), 'the question is hidden under the message box').toBeInViewport({ ratio: 1 });
        await shot(page, `question-waiting-${locale}`);
        expect(await audit(page)).toEqual([]);
        await box.fill('2');
        await box.press('Enter');
        await expect(page.locator('.render-needs-input__answered-note')).toBeVisible();
        await expect(page.locator('.queued-message')).toHaveCount(0);
        await shot(page, `question-answered-${locale}`);
        expect(await audit(page)).toEqual([]);
    });
}

/** A multi-step turn that reasons before every call, then keeps thinking. */
function holdOnThoughtfulSteps(): void {
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (!url.includes('/api/chat/send')) return realFetch(input, init);
        const files = ['expenses_oct.csv', 'expenses_nov.csv', 'notes.txt', 'draft_old.txt'];
        const events = files.flatMap((f) => [
            { type: 'thinking', content: `I need to read ${f} next.` },
            { type: 'tool_start', tool: 'read_file', detail: `file_path: ${f}`, narration: `Reading file: ${f}` },
            { type: 'tool_end', success: true, result: 'Read 2 KB' },
        ]).concat([{ type: 'thinking', content: 'Now add up the months.' }]);
        const body = new ReadableStream<Uint8Array>({
            start(controller) {
                for (const e of events) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(e)}\n\n`));
            },
        });
        return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
    };
}

for (const locale of ['ar', 'en'] as const) {
    test(`a running turn lists its steps, not a thought between each (${locale})`, async ({ page }) => {
        await page.addInitScript(holdOnThoughtfulSteps);
        await open(page, `#${ANSWER_SESSION}`, locale);
        const box = page.getByRole('textbox').first();
        await box.fill('Read the four files');
        await box.press('Enter');
        const live = page.locator('.flow-steps.live');
        await expect(live.locator('.flow-tool')).toHaveCount(4);
        await expect(live.locator('.flow-tool-thought-mark')).toHaveCount(4);
        await expect(live.locator('.flow-reasoning')).toHaveCount(1);
        await expect(live.locator('.flow-thought-hacker')).toHaveCount(0);
        await shot(page, `thoughtful-steps-${locale}`);
        await live.locator('.flow-tool-header').first().click();
        await expect(live.locator('.flow-tool-thought')).toContainText('expenses_oct.csv');
        await shot(page, `thoughtful-steps-open-${locale}`);
        expect(await audit(page)).toEqual([]);
    });
}

/** Every tile and the close button sit beside the docked session panel, not under it. */
async function expectClearOfPanel(page: Page): Promise<void> {
    const panel = (await page.locator('.session-panel.is-docked').boundingBox())!;
    for (const el of await page.locator('.resource-strip li, .resource-strip button').all()) {
        await expect(el).toBeVisible();
        const box = (await el.boundingBox())!;
        expect(box.x + box.width <= panel.x || box.x >= panel.x + panel.width).toBe(true);
    }
}

test('resource usage strip', async ({ page }) => {
    await open(page, `#${ANSWER_SESSION}`);
    const strip = page.getByRole('region', { name: 'استخدام الموارد على هذا الجهاز' });
    await expect(strip.locator('li')).toHaveCount(5);
    await expect(strip.getByText('45.0/63.6 GB')).toBeVisible();
    await expect(strip.locator('li').last()).toContainText(/↑.*0.*↓.*8\.0.*Kbps/);
    // The close button sits at the row's end, which is the left in rtl.
    const closeBox = (await strip.getByRole('button').boundingBox())!;
    const firstTile = (await strip.locator('li').first().boundingBox())!;
    expect(closeBox.x).toBeLessThan(firstTile.x);
    await expectClearOfPanel(page);
    expect(await audit(page)).toEqual([]);
    await page.screenshot({ path: 'visual/test-results/resource-strip-ar.png' });
});

test('resource usage strip with no GPU counters keeps CPU and memory', async ({ page }) => {
    await open(page, `#${ANSWER_SESSION}`, 'ar', async (p) => {
        await p.route('**/api/system/resources', (route) =>
            route.fulfill({
                json: { ...RESOURCES, devices: [], networks: [], devices_error: "function 'D3DKMTEnumAdapters3' not found", networks_error: null },
            }));
    });
    const strip = page.getByRole('region', { name: 'استخدام الموارد على هذا الجهاز' });
    await expect(strip.locator('li')).toHaveCount(3);
    await expect(strip.getByText('45.0/63.6 GB')).toBeVisible();
    await expect(strip.locator('li').last()).toContainText('غير متاح');
    await expectClearOfPanel(page);
    expect(await audit(page)).toEqual([]);
    await page.screenshot({ path: 'visual/test-results/resource-strip-ar-no-gpu.png' });
});

test('resource usage strip at phone width', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page, `#${ANSWER_SESSION}`);
    const strip = page.getByRole('region', { name: 'استخدام الموارد على هذا الجهاز' });
    await expect(strip.locator('li')).toHaveCount(5);
    await expect(strip.locator('.resource-spark').first()).toBeHidden();
    // The closed sidebar drawer waits off screen at this width; judge the strip.
    const problems = (await audit(page)).filter((p) => p.includes('resource') || p.includes('scrolls sideways'));
    expect(problems).toEqual([]);
    await page.screenshot({ path: 'visual/test-results/resource-strip-ar-phone.png' });
});

for (const locale of ['ar', 'en'] as const) {
    for (const status of TOOL_STATUS) {
        test(`a running tool's row says what its model calls are doing: ${status.name} (${locale})`, async ({ page }) => {
            await open(page, `#${ANSWER_SESSION}`, locale, (p) => holdOnToolStatus(p, status));
            const box = page.getByRole('textbox').first();
            await box.fill('List every action item in the minutes');
            await box.press('Enter');
            const row = page.locator('.flow-tool.active').last();
            await expect.poll(() => shownText(row.locator('.flow-tool-label'))).toContain(status[locale]);
            expect(await row.evaluate((el) => getComputedStyle(el).direction)).toBe(locale === 'ar' ? 'rtl' : 'ltr');
            await shot(page, `tool-status-${status.name}-${locale}`);
            expect(await audit(page)).toEqual([]);
        });
    }
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

for (const locale of ['en', 'ar']) {
    // A file GAIA couldn't read used to be counted with the readable ones.
    test(`a document library with a file that could not be read (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => {
            await p.route('**/api/documents', (route) => route.fulfill({ json: {
                documents: [
                    { id: 'd1', filename: 'broken.pdf', filepath: 'C:/broken.pdf', file_size: 1024, chunk_count: 0, indexing_status: 'failed', error_kind: 'corrupt', last_error: 'Could not read PDF: broken.pdf', indexed_at: NOW.toISOString(), last_accessed_at: null, sessions_using: 0 },
                    { id: 'd2', filename: 'garden-notes.txt', filepath: 'C:/garden-notes.txt', file_size: 7270, chunk_count: 7, indexing_status: 'complete', indexed_at: NOW.toISOString(), last_accessed_at: null, sessions_using: 1 },
                ],
                total: 2, total_size_bytes: 8294, total_chunks: 7,
            } }));
        });
        await page.locator('.sb').getByRole('button', { name: /Documents/ }).click();
        await expect(page.locator('.doc-stats-failed')).toBeVisible();
        await shot(page, `documents-${locale}-unreadable`);
        expect(await audit(page)).toEqual([]);
    });
}

for (const locale of ['ar', 'en']) {
    // A library that couldn't be listed used to look empty, as if every document were gone.
    test(`a document library that could not be listed, then a remove that failed (${locale})`, async ({ page }) => {
        // The app lists documents at startup too, so the list fails until Try again.
        let listable = false;
        await open(page, '', locale, async (p) => {
            await p.route('**/api/documents', (route) => {
                if (listable) return route.fulfill({ json: { documents: [{ id: 'd1', filename: 'q3-board.pdf', filepath: 'C:/q3-board.pdf', file_size: 2_048_000, chunk_count: 40, indexing_status: 'complete', indexed_at: NOW.toISOString(), last_accessed_at: null, sessions_using: 0 }], total: 1, total_size_bytes: 2_048_000, total_chunks: 40 } });
                return route.fulfill({ status: 500, json: { detail: 'database is locked' } });
            });
            await p.route('**/api/documents/d1', (route) => route.fulfill({ status: 500, json: { detail: 'database is locked' } }));
        });
        await page.locator('.sb').getByRole('button', { name: /Documents/ }).click();
        await expect(page.locator('.doc-load-error')).toBeVisible();
        await shot(page, `documents-${locale}-load-failed`);
        expect(await audit(page)).toEqual([]);

        listable = true;
        await page.locator('.doc-load-error button').click();
        // Row actions used to be invisible until hover, even with keyboard focus on them.
        const actions = page.locator('.doc-row-actions').first();
        expect(Number(await actions.evaluate((el) => getComputedStyle(el).opacity))).toBeGreaterThanOrEqual(0.5);
        await page.locator('.doc-delete').focus();
        await expect(actions).toHaveCSS('opacity', '1');
        await page.locator('.doc-delete').click();
        await expect(page.locator('.doc-removed-notice')).toBeVisible();
        await shot(page, `documents-${locale}-removed-undo`);
        expect(await audit(page)).toEqual([]);
        await expect(page.locator('.doc-action-error')).toBeVisible({ timeout: 10_000 });
        await shot(page, `documents-${locale}-remove-failed`);
        expect(await audit(page)).toEqual([]);
    });

    // Over a list it already shows, the load error is a notice, not the empty-library error.
    test(`a document library that could not reload over documents it lists (${locale})`, async ({ page }) => {
        let listable = true;
        let listed = 0;
        await open(page, '', locale, async (p) => {
            await p.route('**/api/documents', (route) => (listable && ++listed
                ? route.fulfill({ json: { documents: [{ id: 'd1', filename: 'q3-board.pdf', filepath: 'C:/q3-board.pdf', file_size: 2_048_000, chunk_count: 40, indexing_status: 'complete', indexed_at: NOW.toISOString(), last_accessed_at: null, sessions_using: 0 }], total: 1, total_size_bytes: 2_048_000, total_chunks: 40 } })
                : route.fulfill({ status: 500, json: { detail: 'database is locked' } })));
        });
        // The app's startup listing fills the store; the library's own load then fails.
        await expect.poll(() => listed).toBeGreaterThan(0);
        listable = false;
        await page.locator('.sb').getByRole('button', { name: /Documents/ }).click();
        await expect(page.locator('.doc-action-error')).toBeVisible();
        await expect(page.locator('.doc-load-error')).toHaveCount(0);
        await shot(page, `documents-${locale}-reload-failed`);
        expect(await audit(page)).toEqual([]);
    });

    // A rename the server refused used to fail with only a console line.
    test(`a chat rename the server refuses says why above the composer (${locale})`, async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, locale, async (p) => {
            await p.route(`**/api/sessions/${ANSWER_SESSION}`, (route) => (route.request().method() === 'PUT'
                ? route.fulfill({ status: 500, json: { detail: 'database is locked' } })
                : route.fallback()));
        });
        await page.locator('.task-title').click();
        await page.locator('.title-edit').fill(locale === 'ar' ? 'خطة الربع الثالث' : 'Q3 plan');
        await page.locator('.title-edit').press('Enter');
        await expect(page.locator('.input-area .export-error-bar')).toBeVisible();
        await shot(page, `chat-${locale}-rename-failed`);
        expect(await audit(page)).toEqual([]);
    });
}

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

    // At 200% zoom the action buttons sat below the window and the wheel couldn't reach them.
    test(`the file browser's buttons can be scrolled to at 200% zoom (${locale})`, async ({ page }) => {
        await open(page, '', locale, withOldFile);
        await page.locator('.sb').getByRole('button', { name: /Documents/ }).click();
        await page.getByRole('button', { name: /Browse files/ }).click();
        await page.locator('.fb-entry').first().click();
        await page.setViewportSize({ width: 640, height: 400 });
        const ask = page.locator('.fb-action-btn.primary');
        await expect(ask).toBeEnabled();
        await page.locator('.fb-search-bar').hover();
        for (let i = 0; i < 10; i++) await page.mouse.wheel(0, 200);
        await expect(ask).toBeInViewport({ ratio: 1 });
        await shot(page, `files-${locale}-zoomed`);
    });

    test(`an answer's timestamp and outputs read in the interface language (${locale})`, async ({ page }) => {
        const month = locale === 'ar' ? ARABIC_MONTH : /Mar/;
        await open(page, `#${ANSWER_SESSION}`, locale, async (p) => {
            await p.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) => route.fulfill({
                json: {
                    messages: [
                        { id: 1, session_id: ANSWER_SESSION, role: 'user', content: 'Summarize the Q1 plan.', created_at: OLD_FILE.at, rag_sources: null },
                        {
                            id: 2,
                            session_id: ANSWER_SESSION,
                            role: 'assistant',
                            content: 'The plan has three goals.',
                            created_at: OLD_FILE.at,
                            rag_sources: null,
                            library_notices: [
                                'schedule-fall.xlsx was kept in your documents library for this chat.',
                                'run.exe couldn\'t be added to your documents (.exe files are not supported).',
                            ],
                        },
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
        await expect(page.locator('.library-notices')).toContainText('schedule-fall.xlsx');
        await expect(page.locator('.library-notices')).toContainText('run.exe');
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

for (const [locale, theme] of [['ar', 'light'], ['ar', 'dark'], ['en', 'light']] as const) {
    test(`read-aloud settings (${locale}, ${theme})`, async ({ page }) => {
        await open(page, '', locale, undefined, theme);
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="voice"]').click();
        await expect(page.locator('#read-aloud-voice')).toHaveValue('bf_emma');
        await expect(page.locator('#read-aloud-speed')).toHaveValue('125');
        expect(await audit(page)).toEqual([]);
    });

    test(`a reply that could not be read aloud (${locale}, ${theme})`, async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, locale, async (p) => {
            await p.route('**/api/speech', (route) =>
                route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Lemonade Server is not reachable at http://127.0.0.1:13305' }) }),
            );
        }, theme);
        const reply = page.locator('.msg-assistant').last();
        await reply.hover();
        await reply.locator('.msg-read-aloud').click();
        await expect(reply.locator('.msg-read-aloud-error')).toBeVisible();
        expect(await audit(page)).toEqual([]);
    });
}

test('the dynamic tools setting', async ({ page }) => {
    await open(page);
    await page.getByRole('button', { name: /Settings/ }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('tab', { name: 'متقدم' }).or(dialog.getByRole('button', { name: 'متقدم' })).first().click();
    const title = dialog.locator('summary', { hasText: 'الأدوات الديناميكية' });
    await title.scrollIntoViewIfNeeded();
    await title.click();
    await expect(dialog.getByText('يبدأ كل محادثة بأكثر أدوات GAIA استخدامًا', { exact: false })).toBeVisible();
    await page.screenshot({ path: test.info().outputPath('dynamic-tools-setting.png') });
});

/** Holds the chat stream open on one status event, so the live line stays up. */
const holdStreamOn = (page: Page, event: object) =>
    page.addInitScript((payload) => {
        const real = window.fetch;
        window.fetch = (input, init) => {
            if (!String(input).includes('/chat/send')) return real(input, init);
            const body = new ReadableStream({
                start: (controller) => controller.enqueue(new TextEncoder().encode(`data: ${payload}\n\n`)),
            });
            return Promise.resolve(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
        };
    }, JSON.stringify(event));

for (const [locale, wording] of [['ar', 'جارٍ تحميل المزيد من الأدوات'], ['en', 'Loading more tools']]) {
    test(`a tool re-read is worded in ${locale}`, async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, locale, (p) =>
            holdStreamOn(p, { type: 'status', status: 'working', phase: 'loading_tools', message: 'backend words' }));
        const box = page.locator('textarea').first();
        await box.fill('hello');
        await box.press('Enter');
        await expect(page.getByText(wording).first()).toBeVisible();
        await page.screenshot({ path: test.info().outputPath('loading-tools.png') });
        await expect(page.getByText('backend words')).toHaveCount(0);
    });
}

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

    test('resource usage strip', async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, 'en');
        const strip = page.getByRole('region', { name: 'Resource usage on this PC' });
        await expect(strip.getByText('S: 0 R: 8.0 Kbps')).toBeVisible();
        await expect(strip.locator('li')).toHaveCount(5);
        const closeBox = (await strip.getByRole('button', { name: 'Hide resource usage' }).boundingBox())!;
        const firstTile = (await strip.locator('li').first().boundingBox())!;
        expect(closeBox.x).toBeGreaterThan(firstTile.x);
        await expectClearOfPanel(page);
        expect(await audit(page)).toEqual([]);
        await page.screenshot({ path: 'visual/test-results/resource-strip-en.png' });
    });

    test('resource usage strip on a wide window is one row', async ({ page }) => {
        await page.setViewportSize({ width: 1920, height: 1000 });
        await open(page, `#${ANSWER_SESSION}`, 'en');
        const tiles = page.getByRole('region', { name: 'Resource usage on this PC' }).locator('li');
        await expect(tiles).toHaveCount(5);
        const tops = await tiles.evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().top)));
        expect(new Set(tops).size).toBe(1);
        await expectClearOfPanel(page);
        expect(await audit(page)).toEqual([]);
        await page.screenshot({ path: 'visual/test-results/resource-strip-en-wide.png' });
    });

    test('resource usage strip in the dark theme', async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, 'en', undefined, 'dark');
        await expect(page.getByRole('region', { name: 'Resource usage on this PC' }).locator('li')).toHaveCount(5);
        expect(await audit(page)).toEqual([]);
        await page.screenshot({ path: 'visual/test-results/resource-strip-en-dark.png' });
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

/** Starts with digits, like a real Hugging Face org: unisolated, an Arabic line moves them to its end. */
const UNLISTED_ID = '01-ai/no-such-model-violet-otter';
const CUSTOM_TITLE = { ar: 'معرّف نموذج مخصص', en: 'Custom model ID' };
const CUSTOM_NOTE = {
    ar: { unlisted: 'لا يُدرج', unchecked: 'تعذّر على GAIA التحقق' },
    en: { unlisted: "doesn't list", unchecked: "couldn't check this ID" },
};

for (const locale of ['ar', 'en'] as const) {
    for (const checked of [true, false]) {
        const state = checked ? 'unlisted' : 'unchecked';
        test(`a saved custom model the server ${checked ? 'does not list' : 'could not check'} gets a note (${locale})`, async ({ page }) => {
            await open(page, '', locale, (p) => p.route((url) => url.pathname === '/api/settings', (route) => {
                if (route.request().method() !== 'PUT') return route.fallback();
                return route.fulfill({ json: {
                    custom_model: UNLISTED_ID, context_size: null, agent_mode: 'goal_driven', dynamic_tools: false, dynamic_tools_locked: false,
                    model_status: { found: false, downloaded: false, loaded: false, checked },
                } });
            }));
            await page.getByRole('button', { name: /Settings/ }).first().click();
            const dialog = page.getByRole('dialog');
            await dialog.locator('[data-section="advanced"]').click();
            const section = dialog.locator('details.settings-disclosure', { has: page.locator('summary', { hasText: CUSTOM_TITLE[locale] }) });
            await section.locator('summary').first().scrollIntoViewIfNeeded();
            await section.locator('summary').first().click();
            const input = section.locator('input.model-input');
            await input.fill(UNLISTED_ID);
            await input.press('Enter');

            const note = section.locator('.model-warning[role="status"]');
            await expect(note).toContainText(CUSTOM_NOTE[locale][state]);
            await note.scrollIntoViewIfNeeded();
            await shot(page, `custom-model-${state}-${locale}`);
            if (checked) {
                // The ID is its own bidi island in the Arabic sentence, under the section title and in the box.
                await expect(note).toContainText(UNLISTED_ID);
                if (locale === 'ar') expect(await note.textContent()).toContain(`⁨${UNLISTED_ID}⁩`);
                expect(await section.locator('.settings-disclosure-hint').textContent()).toBe(`⁦${UNLISTED_ID}⁩`);
                expect(await input.evaluate((el) => getComputedStyle(el).direction)).toBe('ltr');
            } else {
                await expect(note).not.toContainText(UNLISTED_ID);
            }
            expect(await audit(page)).toEqual([]);
            if (locale === 'ar') expect(await untranslated(page, '.model-warning')).toEqual([]);
        });
    }
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
    await expectNoFixtureGap(page);
    const dir = process.env.GAIA_VISUAL_SHOTS;
    if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

interface ModelPageOptions {
    models?: unknown[];
    status?: unknown;
    theme?: 'light' | 'dark';
    fireworks?: Record<string, unknown>;
    localOnly?: boolean;
}

async function openModelPage(page: Page, locale: string, { models = MODEL_LIST, status, theme = 'light', fireworks, localOnly = false }: ModelPageOptions = {}): Promise<void> {
    await open(page, '', locale, (p) => withModelPage(p, models, SETUP_PLAN, status, fireworks, localOnly), theme);
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

        test('an environment key that is not connected says why there are no models', async ({ page }) => {
            await openModelPage(page, locale, { fireworks: { key_source: 'environment', key_from_gaia_env: true, key_hint: null, models_discovered: false } });
            await page.getByRole('radio', { name: /Fireworks AI/ }).click();
            await expect(page.locator('.settings-key-masked')).toBeVisible();
            await expect(page.locator('.settings-key-status.settings-error')).toBeVisible();
            await shot(page, `model-${locale}-cloud-env-key-not-connected`);
            expect(await audit(page)).toEqual([]);
        });

        test('a closed key store prompt reads as locked, with Try again', async ({ page }) => {
            const message = 'The OS credential store is locked: its unlock prompt was closed (Prompt dismissed).';
            await openModelPage(page, locale, { fireworks: { key_source: null, key_hint: null, models_discovered: false, key_state: 'store_locked', key_message: message } });
            await page.getByRole('radio', { name: /Fireworks AI/ }).click();
            await expect(page.getByRole('button', { name: locale === 'ar' ? 'حاول مرة أخرى' : 'Try again' })).toBeVisible();
            await expect(page.getByText(message)).toBeHidden();
            await shot(page, `model-${locale}-cloud-key-store-locked`);
            expect(await audit(page)).toEqual([]);
            if (locale === 'ar') expect(await untranslated(page, '.settings-hint[role="status"]')).toEqual([]);
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
    test(`Local only privacy control (${locale})`, async ({ page }) => {
        await open(page, '', locale, (p) => withSettingsPanes(p, {
            networkPolicy: 'cloud_allowed',
            policyNotices: [{ code: 'users_server_keeps_keys', severity: 'info', url: 'http://127.0.0.1:13305' }],
        }));
        await page.getByRole('button', { name: /Settings/ }).first().click();
        const dialog = page.getByRole('dialog');
        await dialog.locator('[data-section="privacy"]').click();
        const toggle = page.getByRole('switch');
        await expect(toggle).not.toBeChecked();
        // Drawn as a slider like every other Settings switch, not the bare word "Off" (#7404).
        const track = page.locator('.toggle-switch', { has: toggle }).locator('.toggle-track');
        const box = await track.boundingBox();
        expect(box && box.width >= 24 && box.height >= 14).toBe(true);
        await shot(page, `privacy-local-only-${locale}-off`);
        await track.click();
        await expect(toggle).toBeChecked();
        await expect(page.locator('.settings-ok[role="status"]')).toBeVisible();
        await shot(page, `privacy-local-only-${locale}-on-notice`);
        expect(await audit(page)).toEqual([]);
    });

    test(`a skills search that matches nothing says so (${locale})`, async ({ page }) => {
        await open(page, '', locale, withSettingsPanes);
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="skills"]').click();
        const search = page.locator('.settings-pane input[type="search"]');
        await search.fill('zzzz');
        // Not a blank pane (#7410).
        await expect(page.locator('.settings-pane [role="status"]')).toBeVisible();
        await expect(page.locator('.settings-pane .link-btn')).toBeVisible();
        await shot(page, `skills-search-none-${locale}`);
        expect(await audit(page)).toEqual([]);
    });

    test(`Mobile access without ngrok (${locale})`, async ({ page }) => {
        const missing = { active: false, url: null, token: null, startedAt: null, publicIp: null, reason: 'ngrok_not_installed', installCommands: ['choco install ngrok'], error: 'ngrok is not installed. Install it from https://ngrok.com/download or run:\n    choco install ngrok' };
        await open(page, '', locale, (p) => p.route('**/api/tunnel/start', (route) => route.fulfill({ json: missing })));
        await page.locator('.sb-icon-btn').filter({ has: page.locator('.lucide-smartphone') }).click();
        const dialog = page.locator('.mobile-modal');
        await expect(dialog.locator('.tunnel-setup')).toBeVisible();
        // Desktop text names what is needed and where to get it, never a command to run (#7415).
        await expect(dialog).not.toContainText('choco');
        // The status dot stays a dot; the setup screen's `.setup` class once stretched it into a bar.
        const dot = await dialog.locator('.status-dot').boundingBox();
        expect(dot && dot.width <= 10 && dot.height <= 10).toBe(true);
        await shot(page, `mobile-access-ngrok-missing-${locale}`);
        expect(await audit(page)).toEqual([]);
    });

    test(`a Setup row lands on its setting, with a way back (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => {
            await withModelPage(p, MODEL_LIST, SETUP_PLAN);
            await p.route('**/api/onboarding/preflight', (route) => route.fulfill({ json: { gpu_name: 'Radeon 8060S', npu_detected: true, ram_gb: 128 } }));
        });
        await page.getByRole('button', { name: /Settings/ }).first().click();
        const dialog = page.getByRole('dialog');
        await dialog.locator('[data-section="setup"]').click();
        await dialog.locator('.settings-setup-row').filter({ hasText: /Context window|نافذة السياق/ }).click();
        // The setting itself, not the top of the Model tab (#7403).
        await expect(page.locator('#ctx-size')).toBeFocused();
        await expect(page.locator('#ctx-size')).toBeInViewport();
        const back = dialog.locator('.settings-back');
        await expect(back).toBeVisible();
        await shot(page, `setup-row-jump-${locale}`);
        expect(await audit(page)).toEqual([]);
        await back.click();
        await expect(dialog.locator('[data-section="setup"]')).toHaveAttribute('aria-current', 'page');
        await expect(back).toHaveCount(0);
    });

    test(`Local only blocks cloud model picker (${locale})`, async ({ page }) => {
        await openModelPage(page, locale, { localOnly: true });
        const cloud = page.getByRole('radio', { name: /Fireworks AI/ });
        await expect(cloud).toBeDisabled();
        await shot(page, `model-${locale}-local-only-blocked`);
        expect(await audit(page)).toEqual([]);
    });

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

for (const locale of ['en', 'ar']) {
    test(`the model menu leads with the provider in use and asks before a download (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => {
            await withModelPage(p, MODEL_LIST, null);
            await p.route('**/api/providers/active', (route) => route.fulfill({
                json: {
                    provider: 'fireworks', model: 'fireworks.glm-5p3-flash', label: 'glm-5p3-flash', remote: true,
                    provider_name: 'Fireworks AI', is_default: false, restore: { status: 'none', message: null },
                },
            }));
        });
        await page.locator('.composer-chip').filter({ hasText: 'glm-5p3-flash' }).click();
        const menu = page.getByRole('dialog');
        await expect(menu.locator('.popover-heading').first()).toHaveText('Fireworks AI');
        await expect(menu.locator('.popover-subheading')).toBeVisible();
        await shot(page, `model-menu-${locale}`);
        expect(await audit(page)).toEqual([]);
        await menu.getByRole('option', { name: /gpt-oss 20B/ }).click();
        await expect(menu.locator('.model-download-confirm')).toContainText('3 GB');
        await shot(page, `model-menu-confirm-${locale}`);
        // The question scrolls into view under its row, so the list is scrolled on purpose here.
        expect((await audit(page)).filter((p) => !p.startsWith('popover opens scrolled'))).toEqual([]);
    });
}

for (const locale of ['ar', 'en']) {
    test.describe(`settings connectors (${locale})`, () => {
        const openConnectors = async (page: Page, rows?: unknown[], extra?: Record<string, unknown>) => {
            await open(page, '', locale, (p) => withConnectors(p, rows, extra));
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

        test('a connector GAIA could not read shows as unavailable, and search keeps working', async ({ page }) => {
            const server = (name: string) => ({
                id: name.toLowerCase(), display_name: name, type: 'mcp_server', description: `${name} tools`,
                configured: false, configurable: true, enabled: true, mcp_env_keys: [], oauth_setup_fields: [],
            });
            await openConnectors(page, [
                { id: 'microsoft', display_name: 'Microsoft', error: 'unavailable' },
                ...['Git', 'Notion', 'Postgres', 'Memory', 'Filesystem', 'Fetch'].map(server),
            ]);
            const broken = page.locator('details[open] .connector-tile', { hasText: 'Microsoft' });
            await expect(broken).toBeVisible();
            await shot(page, `connectors-${locale}-unavailable`);
            expect(await audit(page)).toEqual([]);
            await page.locator('.connectors-section input[type=search]').fill('o');
            await expect(page.locator('.connector-tile', { hasText: 'Notion' })).toBeVisible();
            await expect(broken).toBeVisible();
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

        test('a connected tile offers Personalize, and connecting a server offers it in a banner', async ({ page }) => {
            const brave = { id: 'brave-search', display_name: 'Brave Search', type: 'mcp_server', icon: null, category: 'general',
                tier: 'core', description: 'Lets GAIA work with Brave Search on your behalf.', configurable: true, enabled: true,
                account_id: null, default_scopes: [], available_scopes: [], mcp_env_keys: ['BRAVE_API_KEY'], oauth_setup_fields: [] };
            await openConnectors(page, undefined, {
                'POST /connectors/brave-search/configure': {},
                'POST /connectors/brave-search/test': { ok: true, detail: '' },
                'GET /connectors/brave-search': { ...brave, configured: true },
            });
            await page.locator('.connector-tile-header', { hasText: 'Google' }).click();
            const row = page.locator('.connector-detail .connector-personalize');
            await expect(row).toBeVisible();
            await row.scrollIntoViewIfNeeded();
            await shot(page, `connectors-${locale}-personalize-tile`);
            expect(await audit(page)).toEqual([]);

            await page.locator('.connector-tile-header', { hasText: 'Google' }).click();
            await page.locator('.settings-disclosure:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
            await page.locator('.connector-tile-header', { hasText: 'Brave Search' }).click();
            await page.locator('.mcp-key-input').fill('BSA-not-a-real-key');
            await page.locator('.configure-actions .btn-primary').click();
            const offer = page.locator('.connector-personalize--offer');
            await expect(offer).toBeVisible();
            await offer.scrollIntoViewIfNeeded();
            await shot(page, `connectors-${locale}-personalize-offer`);
            expect(await audit(page)).toEqual([]);
        });

        test('a keyed server shows its label, help and setup link', async ({ page }) => {
            await openConnectors(page, [{
                id: 'mcp-slack', display_name: 'Slack', type: 'mcp_server', description: 'Read channels, threads, and DMs in your Slack workspace.',
                configured: false, configurable: true, enabled: true, mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'], oauth_setup_fields: [],
                setup_url: 'https://api.slack.com/apps?new_app=1&manifest_json=%7B%7D',
                config_fields: [{
                    key: 'SLACK_MCP_XOXB_TOKEN', label: 'Slack bot token', kind: 'secret', required: true, placeholder: 'xoxb-…',
                    help_md: 'Click **Create Slack app**, pick your workspace, then **Install to Workspace**. Then type `/invite @GAIA` in each channel GAIA should read.',
                }],
            }]);
            await page.locator('.settings-disclosure:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
            await page.locator('.connector-tile-header', { hasText: 'Slack' }).click();
            await expect(page.locator('.connector-setup-link')).toBeVisible();
            await expect(page.locator('.mcp-key-help code')).toBeVisible();
            await expect(page.locator('.mcp-key-input')).toHaveAttribute('dir', 'ltr');
            await expect(page.locator('.mcp-key-help')).toHaveAttribute('dir', 'auto');
            await page.locator('.mcp-key-help').scrollIntoViewIfNeeded();
            await shot(page, `connectors-${locale}-mcp-setup`);
            expect(await audit(page)).toEqual([]);
        });

        test('with only GAIA, a connected server has one switch that names the service', async ({ page }) => {
            await openConnectors(page, [{
                id: 'mcp-slack', display_name: 'Slack', type: 'mcp_server', description: 'Read channels, threads, and DMs in your Slack workspace.',
                configured: true, configurable: true, enabled: true, mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'], oauth_setup_fields: [],
            }], {
                'GET /agents': { agents: [{ id: 'gaia', name: 'GAIA', namespaced_agent_id: 'installed:gaia', consumes_mcp_servers: true }] },
                'GET /connectors/mcp-slack/activations': { activations: { 'installed:gaia': true } },
            });
            await page.locator('.settings-disclosure:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
            await page.locator('.connector-tile-header', { hasText: 'Slack' }).click();
            const tile = page.locator('.connector-tile', { hasText: 'Slack' });
            const row = tile.locator('.connection-grants .connector-toggle-row');
            await expect(row).toContainText('GAIA');
            await expect(row).toContainText('Slack');
            await expect(tile.locator('input[type=checkbox]')).toHaveCount(1);
            await expect(tile.locator('.grant-agent-card, .grants-empty')).toHaveCount(0);
            await row.scrollIntoViewIfNeeded();
            await shot(page, `connectors-${locale}-mcp-switch`);
            expect(await audit(page)).toEqual([]);
        });

        test('nothing connected: accounts start open', async ({ page }) => {
            await openConnectors(page, [{ id: 'microsoft', display_name: 'Microsoft', type: 'oauth_pkce', description: 'Mail', configured: false, configurable: true, mcp_env_keys: [], oauth_setup_fields: [] }]);
            await expect(page.locator('.connector-tile')).toBeVisible();
            await shot(page, `connectors-${locale}-none`);
            expect(await audit(page)).toEqual([]);
        });

        test('a connected server that will not start says so, with the reason', async ({ page }) => {
            const slack = {
                id: 'mcp-slack', display_name: 'Slack', type: 'mcp_server', icon: null, category: 'general', tier: 'core',
                description: 'Lets GAIA read and post in Slack.', product_url: 'https://example.com', docs_url: null,
                configured: true, configurable: true, enabled: true, account_id: null, default_scopes: [], available_scopes: [],
                mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'], oauth_setup_fields: [], supports_device_code: false,
            };
            await openConnectors(page, [slack]);
            await page.route('**/api/connectors/mcp-slack/test', (route) => route.fulfill({
                json: { ok: false, detail: 'Authentication failed - check your Slack tokens: invalid_auth (token revoked or expired; xoxb token for workspace T0123456789)' },
            }));
            await page.locator('.connector-tile-header', { hasText: 'Slack' }).click();
            await page.locator('.connector-detail .configure-actions .btn-secondary').first().click();
            await expect(page.locator('.connector-check--failed')).toBeVisible();
            await page.locator('.connector-check--failed summary').click();
            await expect(page.locator('.connector-check--failed pre')).toBeVisible();
            await page.locator('.connector-check--failed').scrollIntoViewIfNeeded();
            await shot(page, `connectors-${locale}-check-failed`);
            expect(await audit(page)).toEqual([]);
            if (locale === 'ar') expect(await untranslated(page, '.settings-scroll', 'bdi, pre, .mcp-key-label')).toEqual([]);
        });

        test('a server that was only slow to start is not blamed on the token', async ({ page }) => {
            const slack = {
                id: 'mcp-slack', display_name: 'Slack', type: 'mcp_server', icon: null, category: 'general', tier: 'core',
                description: 'Lets GAIA read and post in Slack.', product_url: 'https://example.com', docs_url: null,
                configured: true, configurable: true, enabled: true, account_id: null, default_scopes: [], available_scopes: [],
                mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'], oauth_setup_fields: [], supports_device_code: false,
            };
            await openConnectors(page, [slack]);
            await page.route('**/api/connectors/mcp-slack/test', (route) => route.fulfill({
                json: { ok: false, reason: 'timeout', detail: 'did not start within 10 seconds' },
            }));
            await page.locator('.connector-tile-header', { hasText: 'Slack' }).click();
            await page.locator('.connector-detail .configure-actions .btn-secondary').first().click();
            await expect(page.locator('.connector-check--failed')).toBeVisible();
            await page.locator('.connector-check--failed summary').click();
            await expect(page.locator('.connector-check--failed pre')).toBeVisible();
            await page.locator('.connector-check--failed').scrollIntoViewIfNeeded();
            await shot(page, `connectors-${locale}-check-slow`);
            expect(await audit(page)).toEqual([]);
            if (locale === 'ar') expect(await untranslated(page, '.settings-scroll', 'bdi, pre, .mcp-key-label')).toEqual([]);
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

for (const locale of ['ar', 'en']) {
    for (const [state, status] of Object.entries(DISCORD_STATES)) {
        test(`settings messaging: discord ${state} (${locale})`, async ({ page }) => {
            await open(page, '', locale, (p) => withDiscord(p, status));
            await page.getByRole('button', { name: /Settings/ }).first().click();
            await page.getByRole('dialog').locator('[data-section="messaging"]').click();
            await expect(page.locator('.messaging-card')).toBeVisible();
            await expect(page.locator('.settings-pane > .settings-hint .spin')).toHaveCount(0);
            await shot(page, `messaging-${locale}-${state}`);
            await scrollPane(page, 'bottom');
            await shot(page, `messaging-${locale}-${state}-end`);
            expect(await audit(page)).toEqual([]);
            if (locale === 'ar') expect(await untranslated(page)).toEqual([]);
        });
    }

    test(`settings messaging: disconnect confirmation (${locale})`, async ({ page }) => {
        await open(page, '', locale, (p) => withDiscord(p, DISCORD_STATES.connected));
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="messaging"]').click();
        await page.locator('.messaging-footer .btn-danger').click();
        await expect(page.getByRole('alertdialog')).toBeVisible();
        await shot(page, `messaging-${locale}-disconnect`);
        expect(await audit(page)).toEqual([]);
    });

    test(`settings messaging: phone width while pairing (${locale})`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await open(page, '', locale, (p) => withDiscord(p, DISCORD_STATES.pairing));
        await page.getByRole('button', { name: /Open sidebar/ }).click();
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="messaging"]').click();
        await page.locator('.messaging-code').scrollIntoViewIfNeeded();
        await shot(page, `messaging-${locale}-phone`);
        expect(await audit(page)).toEqual([]);
    });
}

const openAutomations = async (page: Page) => {
    await page.getByRole('button', { name: /Settings/ }).first().click();
    await page.getByRole('dialog').locator('[data-section="automations"]').click();
};

for (const locale of ['ar', 'en']) {
    test(`settings automations: list (${locale})`, async ({ page }) => {
        await open(page, '', locale, (p) => withAutomations(p, AUTOMATIONS));
        await openAutomations(page);
        await expect(page.locator('.automation-card')).toHaveCount(AUTOMATIONS.length);
        await shot(page, `automations-${locale}-list`);
        await scrollPane(page, 'bottom');
        await shot(page, `automations-${locale}-list-end`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page)).toEqual([]);
    });

    test(`settings automations: nothing delivers them (${locale})`, async ({ page }) => {
        await open(page, '', locale, (p) => withAutomations(p, AUTOMATIONS.slice(0, 2), false));
        await openAutomations(page);
        await expect(page.locator('.settings-warning')).toBeVisible();
        await shot(page, `automations-${locale}-no-delivery`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page)).toEqual([]);
    });

    test(`settings automations: delivery not checked (${locale})`, async ({ page }) => {
        await open(page, '', locale, (p) => withAutomations(p, AUTOMATIONS.slice(0, 1), null));
        await openAutomations(page);
        await expect(page.locator('.automation-unchecked')).toBeVisible();
        await shot(page, `automations-${locale}-unchecked`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page)).toEqual([]);
    });

    test(`settings automations: empty (${locale})`, async ({ page }) => {
        await open(page, '', locale, (p) => withAutomations(p, []));
        await openAutomations(page);
        await expect(page.locator('.automation-empty')).toBeVisible();
        await shot(page, `automations-${locale}-empty`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page)).toEqual([]);
    });

    test(`settings automations: load failed (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => { await p.route('**/api/automations', (route) => route.fulfill({ status: 500, json: { detail: 'automations.json is not usable: unexpected keyword. Delete it to clear your automations.' } })); });
        await openAutomations(page);
        await expect(page.locator('.settings-pane .settings-error')).toBeVisible();
        await shot(page, `automations-${locale}-failed`);
        expect(await audit(page)).toEqual([]);
    });

    test(`settings automations: remove confirmation (${locale})`, async ({ page }) => {
        await open(page, '', locale, (p) => withAutomations(p, AUTOMATIONS));
        await openAutomations(page);
        await page.locator('.automation-card').first().locator('.btn-danger').click();
        await expect(page.getByRole('alertdialog')).toBeVisible();
        await shot(page, `automations-${locale}-remove`);
        expect(await audit(page)).toEqual([]);
    });

    test(`settings automations: phone width (${locale})`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await open(page, '', locale, (p) => withAutomations(p, AUTOMATIONS));
        await page.getByRole('button', { name: /Open sidebar/ }).click();
        await openAutomations(page);
        await expect(page.locator('.automation-card').first()).toBeVisible();
        await shot(page, `automations-${locale}-phone`);
        expect(await audit(page)).toEqual([]);
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

    test(`desktop app and updates: status unreadable (${locale})`, async ({ page }) => {
        const failure = "Error invoking remote method 'gaia-updater:get-status': Error: No handler registered for 'gaia-updater:get-status'";
        await open(page, '', locale, async (p) => { await withSettingsPanes(p); await withDesktopBridges(p, {}, undefined, failure); });
        await page.getByRole('button', { name: /Settings/ }).first().click();
        const updates = page.locator('.settings-disclosure', { hasText: /Updates/ });
        await expect(updates).toHaveAttribute('open', '');
        await expect(updates).toContainText(locale === 'ar' ? 'تعذّرت قراءة حالة التحديث' : "Couldn't read the update status");
        await expect(updates).not.toContainText(/up to date|محدّث/);
        await expect(updates).not.toContainText(/handler|gaia-updater/i);
        await expect(updates.getByRole('button', { name: locale === 'ar' ? 'إعادة المحاولة' : 'Try again' })).toBeVisible();
        await updates.scrollIntoViewIfNeeded();
        await shot(page, `updates-${locale}-unreadable`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page)).toEqual([]);
    });

    test(`desktop app on a desktop with no tray (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => {
            await withSettingsPanes(p);
            await withDesktopBridges(p, {}, { available: false, reason: 'no-host' });
        });
        await page.getByRole('button', { name: /Settings/ }).first().click();
        const desktop = page.locator('.settings-disclosure', { hasText: /Desktop app|تطبيق سطح المكتب/ });
        await expect(desktop).toBeVisible();
        await page.locator('.settings-disclosure:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
        await desktop.scrollIntoViewIfNeeded();
        await expect(desktop.locator('#desktop-minimizeToTray-hint')).not.toContainText('AppIndicator');
        await expect(desktop.locator('#desktop-minimizeToTray-hint')).toContainText(locale === 'ar' ? 'شريط التطبيقات' : 'dock');
        await shot(page, `desktop-${locale}-no-tray`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page)).toEqual([]);
    });

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

const OPENCODE = 'opencode.big-model';

for (const locale of ['ar', 'en']) {
    test(`a cloud model learned from Lemonade's list reads as cloud (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => {
            await withModelPage(p, MODEL_LIST, SETUP_PLAN);
            // No chats yet, so the chip goes by the picked model alone.
            await p.route((url) => url.pathname === '/api/sessions', (route) => route.fulfill({ json: { sessions: [], total: 0 } }));
            await p.route('**/api/providers/active', (route) => route.fulfill({
                json: {
                    provider: 'local', model: OPENCODE, display_name: 'big-model', label: 'big-model · opencode', remote: true,
                    provider_name: 'opencode', downloaded: null, inference_remote: true, inference_description: null,
                    is_default: false, restore: { status: 'none', message: null },
                },
            }));
        });
        await expect(page.getByText(/downloaded yet/)).toHaveCount(0);
        await expect(page.getByText('big-model · opencode')).toBeVisible();
        await shot(page, `chip-${locale}-cloud-from-lemonade-list`);
        const sidebar = page.getByRole('button', { name: /Open sidebar/ });
        if (await sidebar.isVisible()) await sidebar.click();
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="model"]').click();
        await expect(page.locator('.model-in-use-name')).toBeVisible();
        await expect(page.getByText(/downloaded yet/)).toHaveCount(0);
        await shot(page, `model-${locale}-cloud-from-lemonade-list`);
        expect(await audit(page)).toEqual([]);
    });
}

const FLASH = 'fireworks.deepseek-v4p1-flash';

/** The answer chat on DeepSeek on Fireworks, with three priced answers and a stopped one. */
async function withCloudSpending(page: Page): Promise<void> {
    const sessions = SESSIONS.map((s) => (s.id === ANSWER_SESSION
        ? { ...s, model: FLASH, effective_model: FLASH, inference_remote: true, inference_provider: 'fireworks', inference_provider_name: 'Fireworks AI' }
        : s));
    await page.route((url) => url.pathname === '/api/sessions', (route) => route.fulfill({ json: { sessions, total: sessions.length } }));
    await page.route('**/api/providers/active', (route) => route.fulfill({
        json: { provider: 'fireworks', model: FLASH, label: 'DeepSeek V4.1 Flash', remote: true, is_default: false, restore: { status: 'none', message: null } },
    }));
    const at = (minutesAgo: number) => new Date(NOW.getTime() - minutesAgo * 60_000).toISOString();
    await page.route((url) => url.pathname === `/api/spending/chat/${ANSWER_SESSION}`, (route) => route.fulfill({
        json: {
            paid: true, model: FLASH, price_source: 'Fireworks AI list price',
            chat: {
                usd: 0.0431, answers: 4, unpriced_answers: 1,
                items: [
                    { message_id: 2, created_at: at(40), model: 'DeepSeek V4.1 Flash', kind: 'estimate', usd: 0.0214 },
                    { message_id: 4, created_at: at(25), model: 'DeepSeek V4.1 Flash', kind: 'estimate', usd: 0.0008 },
                    { message_id: 6, created_at: at(12), model: 'DeepSeek V4.1 Flash', kind: 'unreported', usd: null },
                    { message_id: 8, created_at: at(2), model: 'DeepSeek V4.1 Flash', kind: 'estimate', usd: 0.0209 },
                ],
            },
            today: { usd: 0.3127, answers: 19, unpriced_answers: 1, chats: 5, since: NOW.toISOString() },
        },
    }));
}

for (const locale of ['ar', 'en']) {
    test(`cloud spending chip and breakdown (${locale})`, async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, locale, withCloudSpending);
        const chip = page.locator('.spend-meter');
        await expect(chip).toContainText('$0.04');
        await expect(chip).toContainText('$0.31');
        await shot(page, `spend-chip-${locale}`);
        expect(await audit(page)).toEqual([]);
        await chip.click();
        await expect(page.locator('.spend-meter-answers .context-meter-row')).toHaveCount(4);
        await shot(page, `spend-popover-${locale}`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.spend-meter-panel')).toEqual([]);
    });
}

const MONTH_SPENDING = {
    month: '2026-10', total_usd: 3.42, turns: 37, unpriced_turns: 4, unreported_turns: 5,
    limit_usd: 20, state: 'ok', by_model: [], price_source: 'list price',
};

for (const locale of ['ar', 'en']) {
    test(`monthly spending headline leaves out unpriced and unreported turns (${locale})`, async ({ page }) => {
        await openModelPage(page, locale);
        await page.route((url) => url.pathname === '/api/spending', (route) => route.fulfill({ json: MONTH_SPENDING }));
        await page.getByRole('radio', { name: /Fireworks AI/ }).click();
        const spending = page.locator('section.spending');
        await expect(spending.locator('.spending-total')).toContainText('$3.42');
        await expect(spending.locator('.spending-total')).toContainText('$20');
        await expect(spending.locator('.spending-total')).toContainText(locale === 'ar' ? 'أدوار المحادثة السحابية: 28' : /28 cloud chat turns/);
        await expect(spending.locator('p.settings-hint')).toHaveCount(2);
        await expect(spending.locator('p.settings-hint').nth(0)).toContainText(locale === 'ar' ? '(4)' : 'Plus 4 turns on models with an unknown price');
        await expect(spending.locator('p.settings-hint').nth(1)).toContainText(locale === 'ar' ? '(5)' : 'Plus 5 turns whose usage wasn’t reported');
        await spending.scrollIntoViewIfNeeded();
        await scrollPane(page, 'bottom');
        await shot(page, `spending-month-${locale}`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, 'section.spending')).toEqual([]);
    });
}

test('no spending chip on a model on this PC', async ({ page }) => {
    await open(page, `#${ANSWER_SESSION}`, 'en');
    await expect(page.getByText('Action items from Aug 28')).toBeVisible();
    await expect(page.locator('.spend-meter')).toHaveCount(0);
});

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

        test('new schedule with a "when" typed in Arabic', async ({ page }) => {
            await openSchedules(page, []);
            await page.locator('.schedule-modal .btn-primary').click();
            await page.locator('.schedule-nl-input').fill('كل يوم الساعة 8 صباحا');
            await expect(page.locator('.schedule-parsed-preview.invalid')).toBeVisible();
            await expect(page.locator('.schedule-field-hint')).toBeVisible();
            await expect(page.locator('.schedule-parsed-hint')).toHaveCount(0);
            await check(page, 'form-arabic-input');
        });
    });
}

const LOG_LINES = [
    { text: '2026-11-04 10:05:37.798 [Info] (main) Starting Lemonade Server...', severity: 'info', tag: 'main' },
    { text: '2026-11-04 10:05:37.799 [Info] (main)   Cache dir: C:\\Users\\you\\.gaia\\lemonade\\cache', severity: 'info', tag: 'main' },
    { text: '2026-11-04 10:06:11.730 [Warn] (FastFlowLM) NPU is busy with another process; retrying the load', severity: 'warning', tag: 'FastFlowLM' },
    { text: `2026-11-04 10:06:12.004 [Info] (Process) Tools JSON: [{"function":{"description":"${'List the skills installed on this machine. '.repeat(12)}"}}]`, severity: 'info', tag: 'Process' },
    { text: '2026-11-04 10:06:15.512 [Error] (FLM) flm serve exited with code 1: device or resource busy', severity: 'error', tag: 'FLM' },
];

const LOG_STATES: Array<[string, Record<string, unknown>]> = [
    ['gaia', { owner: 'gaia', base_url: 'http://127.0.0.1:56150/api/v1', source: 'file', available: true, lines: LOG_LINES, cursor: '900', reset: false, dropped: 1200, log_path: 'C:\\Users\\you\\.gaia\\lemonade\\lemond.log', server_answering: true, reason: null, detail: null }],
    ['gaia-down', { owner: 'gaia', base_url: 'http://127.0.0.1:56150/api/v1', source: 'file', available: true, lines: LOG_LINES, cursor: '900', reset: false, dropped: 0, log_path: 'C:\\Users\\you\\.gaia\\lemonade\\lemond.log', server_answering: false, reason: null, detail: null }],
    ['yours', { owner: 'user', base_url: 'http://localhost:13305/api/v1', source: 'stream', available: true, lines: LOG_LINES, cursor: '5', reset: false, dropped: 0, log_path: null, server_answering: true, reason: null, detail: null }],
    ['yours-down', { owner: 'user', base_url: 'http://localhost:13305/api/v1', source: null, available: false, lines: [], cursor: null, reset: false, dropped: 0, log_path: null, server_answering: false, reason: 'not_running', detail: 'Lemonade not reachable at http://localhost:13305/api/v1: connection refused' }],
    ['remote', { owner: 'remote', base_url: 'http://10.0.0.7:13305/api/v1', source: null, available: false, lines: [], cursor: null, reset: false, dropped: 0, log_path: null, server_answering: true, reason: 'remote', detail: 'ws://10.0.0.7:9000/logs/stream: ClientConnectorError' }],
];

const GAIA_LINES = [
    { text: '[2026-11-04 11:48:15,102] | INFO | gaia.ui.server | server.py:991 | Agent UI backend listening on 127.0.0.1:4200', severity: 'info', tag: null },
    { text: '[2026-11-04 11:48:16,311] | WARNING | gaia.llm.lemonade_client | lemonade_client.py:5828 | Model load took 41.2 s', severity: 'warning', tag: null },
    { text: "[2026-11-04 11:48:17,004] | ERROR | gaia.ui.routers.chat | chat.py:412 | Turn failed: Couldn't reach the local LLM", severity: 'error', tag: null },
    { text: 'Traceback (most recent call last):', severity: null, tag: null },
    { text: '  File "C:\\Users\\you\\gaia\\llm\\providers\\lemonade.py", line 450, in chat', severity: null, tag: null },
];

const LOG_FILES = {
    sources: [
        { id: 'model_server', files: [] },
        { id: 'gaia', files: [{ name: 'gaia.log', path: 'C:\\Users\\you\\.gaia\\gaia.log', size_bytes: 9_700_000, modified: 1 }] },
        { id: 'service', files: [{ name: 'daemon.log', path: 'C:\\Users\\you\\.gaia\\host\\daemon.log', size_bytes: 120_000, modified: 1 }] },
        {
            id: 'agent',
            files: [
                { name: 'gaia-agent.log', path: 'C:\\Users\\you\\.gaia\\logs\\gaia-agent.log', size_bytes: 2_048_000, modified: 2 },
                { name: 'gaia/sidecar-54747.log', path: 'C:\\Users\\you\\.gaia\\agents\\gaia\\logs\\sidecar-54747.log', size_bytes: 51_200, modified: 1 },
            ],
        },
        { id: 'desktop', files: [{ name: 'electron-main.log', path: 'C:\\Users\\you\\.gaia\\electron-main.log', size_bytes: 30_000, modified: 1 }] },
        { id: 'other', files: [] },
    ],
};

const fileRead = (path: string, lines: unknown[]) => ({
    owner: null, base_url: null, source: 'file', available: true, lines, cursor: '400', reset: false,
    dropped: 0, log_path: path, server_answering: null, reason: null, detail: null,
});

/** Every log route: each log whole once, then no new lines, so following doesn't repeat it. */
async function withLogs(page: Page, server: Record<string, unknown>): Promise<void> {
    await page.route('**/api/providers/local/status', (route) => route.fulfill({ json: LOCAL_STATUS }));
    await page.route(/\/api\/logs(\/|\?|$)/, (route) => {
        const url = new URL(route.request().url());
        const source = url.pathname.split('/api/logs/')[1];
        if (!source) return route.fulfill({ json: LOG_FILES });
        const body = source === 'model_server' ? server
            : source === 'gaia' ? fileRead('C:\\Users\\you\\.gaia\\gaia.log', GAIA_LINES)
            : source === 'agent' ? fileRead('C:\\Users\\you\\.gaia\\logs\\gaia-agent.log', GAIA_LINES)
            : { ...fileRead('', []), available: false, source: null, log_path: null, reason: 'no_file' };
        return route.fulfill({ json: url.searchParams.has('after') ? { ...body, lines: [], reset: false } : body });
    });
}

for (const locale of ['ar', 'en']) {
    for (const [state, body] of LOG_STATES) {
        test(`model server log from the model popover: ${state} (${locale})`, async ({ page }) => {
            await open(page, '', locale, (p) => withLogs(p, body));
            await page.locator('.sb-location').click();
            await page.locator('.popover .model-status').getByRole('button', { name: locale === 'ar' ? /سجلّ خادم النماذج/ : /Model server log/ }).click();
            const dialog = page.locator('.log-viewer');
            await expect(dialog).toBeVisible();
            await expect(dialog.locator('.spin')).toHaveCount(0);
            if (body.available) {
                const lines = dialog.locator('.log-viewer-lines');
                await expect(lines).toBeVisible();
                // Log text reads left to right in every language.
                expect(await lines.evaluate((el) => getComputedStyle(el).direction)).toBe('ltr');
            } else {
                await expect(dialog.locator('.log-viewer-warning')).toBeVisible();
                await expect(dialog.locator('.log-viewer-lines')).toHaveCount(0);
            }
            await shot(page, `log-viewer-${locale}-${state}`);
            expect(await audit(page)).toEqual([]);
            if (locale === 'ar') expect(await untranslated(page, '.log-viewer', 'bdi, pre')).toEqual([]);
            // No sideways scroll: a long line wraps inside the box.
            expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
        });
    }

    test(`model server log at phone width (${locale})`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await open(page, '', locale, (p) => withLogs(p, LOG_STATES[1][1]));
        const sidebar = page.getByRole('button', { name: /Open sidebar|فتح الشريط الجانبي/ });
        if (await sidebar.isVisible()) await sidebar.click();
        await page.locator('.sb-location').click();
        await page.locator('.popover .model-status .btn-secondary').filter({ has: page.locator('svg.lucide-scroll-text') }).click();
        const dialog = page.locator('.log-viewer');
        await expect(dialog.locator('.log-viewer-lines')).toBeVisible();
        // Opens on the newest line, whole.
        await expect.poll(() => dialog.locator('.log-viewer-lines').evaluate((el) =>
            JSON.stringify({ gap: Math.round(el.scrollHeight - el.scrollTop - el.clientHeight), sh: el.scrollHeight, ch: el.clientHeight, st: el.scrollTop }))).toContain('"gap":0');
        await shot(page, `log-viewer-${locale}-phone`);
        expect(await audit(page)).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    });

    test(`GAIA's logs from Settings › Advanced, then the agent's files (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => { await withSettingsPanes(p); await withLogs(p, LOG_STATES[0][1]); });
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="advanced"]').click();
        const card = page.getByRole('region', { name: locale === 'ar' ? /السجلات/ : 'Logs' });
        await expect(card).toBeVisible();
        await shot(page, `log-viewer-${locale}-settings-card`);
        expect(await audit(page)).toEqual([]);
        await card.getByRole('button', { name: locale === 'ar' ? /عرض السجلات/ : 'View logs' }).click();
        const dialog = page.locator('.log-viewer');
        await expect(dialog.getByRole('tab', { selected: true })).toHaveText(locale === 'ar' ? /GAIA/ : 'GAIA');
        await expect(dialog.locator('.log-viewer-lines')).toContainText('Turn failed');
        await shot(page, `log-viewer-${locale}-gaia-tab`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.log-viewer', 'bdi, pre')).toEqual([]);
        await dialog.getByRole('tab', { name: locale === 'ar' ? /الوكيل/ : 'Agent' }).click();
        await expect(dialog.getByRole('combobox')).toHaveValue('gaia-agent.log');
        await shot(page, `log-viewer-${locale}-agent-tab`);
        expect(await audit(page)).toEqual([]);
        await dialog.getByRole('tab', { name: locale === 'ar' ? /تطبيق سطح المكتب/ : 'Desktop app' }).click();
        await expect(dialog.locator('.log-viewer-warning')).toBeVisible();
        await shot(page, `log-viewer-${locale}-nothing-written`);
        expect(await audit(page)).toEqual([]);
        expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
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

for (const locale of ['ar', 'en']) {
    test(`a crashed backend says it is restarting, with a timer, and no raw error (${locale})`, async ({ page }) => {
        await open(page, '', locale, (p) => withCrashedBackend(p, 'restarting', NOW.getTime() - 75_000));
        const banner = page.locator('.connection-banner');
        await expect(banner).toContainText(locale === 'ar' ? 'يُعاد تشغيله' : 'is restarting');
        await expect(banner.locator('bdi')).toHaveText('1:15');
        await expect(page.getByText(/Failed to fetch|still running/)).toHaveCount(0);
        await shot(page, `backend-restarting-${locale}`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.connection-banner')).toEqual([]);
    });

    test(`restarts ran out: a plain message and Restart GAIA (${locale})`, async ({ page }) => {
        await open(page, '', locale, (p) => withCrashedBackend(p, 'stopped', NOW.getTime() - 300_000));
        const banner = page.locator('.connection-banner');
        await expect(banner.getByRole('button', { name: locale === 'ar' ? /إعادة تشغيل GAIA/ : /Restart GAIA/ })).toBeVisible();
        await expect(page.getByText(/Failed to fetch|still running/)).toHaveCount(0);
        await shot(page, `backend-stopped-${locale}`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.connection-banner')).toEqual([]);
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

        test('short window: the download summary scrolls with the list', async ({ page }) => {
            await page.setViewportSize({ width: 800, height: 600 });
            const s = scene('models');
            await open(page, '', locale, (p) => withSetup(p, s.answers));
            await s.reach(page);
            await expect(page.locator('.setup-models-footer')).toHaveCSS('position', 'static');
        });

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

const LONG_TITLE = 'Quarterly budget review with finance, engineering and the regional leads, including the follow-up on the September numbers';
const MANY_ATTENDEES = Array.from({ length: 30 }, (_, i) => `person.number.${i + 1}@example.com`);
const CALENDAR_PROMPTS = {
    create: { title: LONG_TITLE, start: '2026-11-03T09:00:00-05:00', end: '2026-11-03T10:00:00-05:00', notify_attendees: MANY_ATTENDEES, calendar_account: 'me@example.com', location: 'Room 4', description: 'Bring the Q3 numbers.' },
    update: { event_id: 'abc', expected_title: LONG_TITLE, expected_start: '2026-11-03T14:00:00Z', notify_attendees: ['dana@example.com'], new_title: 'Budget sync', new_start: '2026-11-04T14:00:00Z', new_end: '2026-11-04T15:00:00Z' },
    cancel: { event_id: 'abc', expected_title: 'اجتماع الميزانية', expected_start: '2026-11-03T14:00:00Z', notify_attendees: [] },
} as const;

/** A reply that asks one calendar approval and stays open: a closed stream dismisses the prompt. */
async function replyWithCalendarApproval(page: Page, kind: keyof typeof CALENDAR_PROMPTS): Promise<void> {
    const label = { create: 'creating', update: 'changing', cancel: 'cancelling' }[kind];
    const event = JSON.stringify({ type: 'permission_request', tool: `calendar/${kind}_calendar_event`, tool_label: `${label} calendar event`, args: CALENDAR_PROMPTS[kind], confirm_id: `perm-${kind}` });
    await page.addInitScript((line) => {
        const realFetch = window.fetch;
        window.fetch = (input, init) => (String(input).endsWith('/chat/send')
            ? Promise.resolve(new Response(new ReadableStream({
                start: (out) => out.enqueue(new TextEncoder().encode(`data: ${line}\n\n`)),
            }), { headers: { 'Content-Type': 'text/event-stream' } }))
            : realFetch(input, init));
    }, event);
}

for (const locale of ['ar', 'en'] as const) {
    for (const kind of ['create', 'update', 'cancel'] as const) {
        test(`calendar approval, ${kind} (${locale})`, async ({ page }, info) => {
            await open(page, `#${ANSWER_SESSION}`, locale, (p) => replyWithCalendarApproval(p, kind));
            await page.locator('textarea').first().fill('Handle my calendar');
            await page.keyboard.press('Enter');
            const facts = page.getByTestId('perm-calendar');
            await expect(facts).toBeVisible();
            // The buttons stay reachable however many people are on the event.
            await expect(page.locator('.perm-card-actions')).toBeInViewport();
            // Dates follow the UI's language, so Arabic shows no English month names.
            if (locale === 'ar') await expect(facts).not.toContainText(/Nov|AM|PM/);
            else await expect(facts).toContainText('Nov');
            await page.locator('.perm-card').screenshot({ path: info.outputPath(`calendar-${kind}-${locale}.png`) });
            await page.screenshot({ path: info.outputPath(`calendar-${kind}-${locale}-page.png`) });
            expect(await audit(page)).toEqual([]);
        });
    }
}

test('calendar approval fits a phone (ar)', async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 780 });
    await open(page, `#${ANSWER_SESSION}`, 'ar', (p) => replyWithCalendarApproval(p, 'create'));
    await page.locator('textarea').first().fill('Handle my calendar');
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('perm-calendar')).toBeVisible();
    await page.screenshot({ path: info.outputPath('calendar-create-ar-phone.png') });
    const offScreen = await page.locator('.perm-card *').evaluateAll((els) =>
        els.filter((el) => {
            const box = el.getBoundingClientRect();
            return box.width > 0 && (box.left < -1 || box.right > innerWidth + 1);
        }).length,
    );
    expect(offScreen).toBe(0);
    const actions = page.locator('.perm-card-actions');
    await actions.scrollIntoViewIfNeeded();
    await expect(actions).toBeInViewport();
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
        await open(page, '', locale, async (p) => {
            await withWindowsControls(p);
            await withoutFlagship(p);
        });
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

for (const locale of ['ar', 'en']) {
    test(`the chat-create error sits below the window controls and is not a drag handle (${locale})`, async ({ page }) => {
        await open(page, '', locale, async (p) => {
            await withWindowsControls(p);
            await p.route((u) => u.pathname === '/api/sessions', async (route) => {
                if (route.request().method() === 'POST') return route.fulfill({ json: SESSIONS[0] });
                return route.fallback();
            });
            await p.route(`**/api/chat/permissions/${SESSIONS[0].id}`, async (route) => {
                if (route.request().method() === 'GET') return route.fallback();
                return route.fulfill({ status: 500, json: { detail: 'The permission service did not answer' } });
            });
        });
        await page.evaluate((w) => document.documentElement.style.setProperty('--titlebar-controls-w', `${w}px`), WINDOW_CONTROLS_W);
        await page.locator('textarea').first().fill('hello');
        await page.keyboard.press('Enter');
        const error = page.locator('.toast-dismissible');
        await expect(error).toBeVisible();
        const { top, region } = await error.evaluate((el) => ({
            top: el.getBoundingClientRect().top,
            region: getComputedStyle(el).getPropertyValue('-webkit-app-region'),
        }));
        expect(top).toBeGreaterThanOrEqual(48);
        expect(region).toBe('no-drag');
        expect(await audit(page)).toEqual([]);
    });
}

test('the agent notice and the memory chip are in Arabic', async ({ page }) => {
    await open(page, '', 'ar', withoutFlagship);
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
        if (locale === 'ar') await expect(title).toContainText('هل تريد تشغيل نص Python');
        else await expect(title).toHaveText('Run this Python script?');
        await expect(page.getByRole('button', { name: /Allow once/ })).toContainText(locale === 'ar' ? 'السماح مرة واحدة' : 'Allow once');
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.perm-card', 'bdi, pre')).toEqual([]);
        await shot(page, `permission-python-${locale}`);
    });
}

const FOLDER = 'C:\\Users\\you\\Documents\\uxa';
const STEPS_THEN_PROMPT = [
    ...['expenses_oct.csv', 'expenses_nov.csv', 'notes.txt', 'draft_old.txt', 'a.txt', 'b.txt', 'c.txt', 'd.txt'].flatMap((f) => [
        { type: 'tool_start', tool: 'read_file', detail: `file_path: ${f}`, narration: `Reading file: ${f}` },
        { type: 'tool_end', success: true },
    ]),
    {
        type: 'permission_request', tool: 'write_file', tool_label: 'writing file', confirm_id: 'c-folder',
        args: { file_path: `${FOLDER}\\summary.md`, content: '# Summary\n' },
        always_scope: `changes to files in ${FOLDER}`, always_label: `changing files in ${FOLDER}`,
    },
];

for (const locale of ['ar', 'en'] as const) {
    test(`a permission card names a granted folder briefly (${locale})`, async ({ page }) => {
        await page.setViewportSize({ width: 1400, height: 700 });
        await page.addInitScript(streamThenWait, STEPS_THEN_PROMPT);
        await open(page, `#${ANSWER_SESSION}`, locale);
        await page.locator('textarea').first().fill('summarize the folder');
        await page.keyboard.press('Enter');
        const card = page.locator('.perm-card');
        await expect(card).toBeVisible();
        const always = card.locator('.perm-btn').nth(1);
        await expect(always).toHaveAttribute('title', `changing files in ${FOLDER}`);
        if (locale === 'en') await expect(always).toHaveText('Always allow changing files in uxa in this chat');
        expect(await audit(page)).toEqual([]);
        await shot(page, `permission-folder-${locale}`);
    });
}

const NO_VISION_MODEL = [
    { type: 'tool_start', tool: 'answer_question_about_image', narration: 'Looking at the image' },
    { type: 'tool_result', success: false, needs_model: 'vision', summary: "Vision model 'Gemma-4-E4B-it-GGUF' is not downloaded (about 6 GB)" },
    { type: 'tool_end', success: false },
    { type: 'thinking', content: 'Tell the user the vision model is needed.' },
];

for (const locale of ['ar', 'en'] as const) {
    test(`an image step with no vision model offers it (${locale})`, async ({ page }) => {
        await page.addInitScript(streamThenWait, NO_VISION_MODEL);
        await open(page, `#${ANSWER_SESSION}`, locale);
        await page.locator('textarea').first().fill('what colour is the picture?');
        await page.keyboard.press('Enter');
        const offer = page.locator('.flow-tool-offer');
        await expect(offer).toBeVisible();
        await expect(offer.getByRole('button')).toContainText(locale === 'ar' ? 'تنزيل نموذج الرؤية' : 'Get the vision model');
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.flow-tool-offer', 'bdi, code')).toEqual([]);
        await shot(page, `vision-offer-${locale}`);
    });
}

const CHANGE_PROMPTS = {
    write: {
        type: 'permission_request', tool: 'write_file', tool_label: 'writing file', confirm_id: 'c-write',
        args: { file_path: 'C:\\Users\\you\\Documents\\uxa\\summary.md', content: '# Expenses Summary\n\n| Month | Total |\n|---|---|\n| October | 412.50 |\n| November | 388.10 |\n\nGrand total: 800.60\n' },
    },
    edit: {
        type: 'permission_request', tool: 'edit_file', tool_label: 'editing file', confirm_id: 'c-edit',
        args: { file_path: 'C:\\Users\\you\\Documents\\uxa\\notes.txt', old_content: '- owner: Dana\n', new_content: '- owner: Dana\nreviewed\n' },
    },
    // A right-to-left override would draw the line reversed; it shows as a code instead.
    hidden: {
        type: 'permission_request', tool: 'write_file', tool_label: 'writing file', confirm_id: 'c-hidden',
        args: { file_path: 'C:\\Users\\you\\Documents\\uxa\\run.py', content: 'ok = 1\n# \u202Eevil()\n' },
    },
    // A right-to-left mark before each statement would draw them in reverse order.
    rlm: {
        type: 'permission_request', tool: 'write_file', tool_label: 'writing file', confirm_id: 'c-rlm',
        args: { file_path: 'C:\\Users\\you\\Documents\\uxa\\run.py', content: 'title = "مرحبا"\n\u200Fsafe_step(); \u200Fdelete_everything();\n' },
    },
};

/** Left edge of `needle` as drawn inside `selector`, so a test can check visual order. */
async function drawnLeft(page: Page, selector: string, needle: string): Promise<number> {
    return page.locator(selector).evaluate((el, word) => {
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const at = node.textContent?.indexOf(word) ?? -1;
            if (at < 0) continue;
            const range = document.createRange();
            range.setStart(node, at);
            range.setEnd(node, at + word.length);
            return range.getBoundingClientRect().left;
        }
        throw new Error(`${word} not drawn`);
    }, needle);
}

for (const locale of ['ar', 'en'] as const) {
    for (const [kind, prompt] of Object.entries(CHANGE_PROMPTS)) {
        test(`a ${kind} prompt shows what will land in the file (${locale})`, async ({ page }) => {
            await page.addInitScript(streamThenWait, [prompt]);
            await open(page, `#${ANSWER_SESSION}`, locale);
            await page.locator('textarea').first().fill('update my notes');
            await page.keyboard.press('Enter');
            const preview = page.locator('.perm-card-change');
            await expect(preview).toBeVisible();
            await expect(page.locator('.perm-card-pre').filter({ hasText: '"file_path"' })).toHaveCount(0);
            if (kind === 'rlm') {
                const shown = '[data-testid="perm-write-preview"]';
                await expect(page.locator(shown)).toContainText('[U+200F]safe_step(); [U+200F]delete_everything();');
                await expect(page.getByTestId('perm-change-unseen')).toBeVisible();
                expect(await drawnLeft(page, shown, 'safe_step')).toBeLessThan(await drawnLeft(page, shown, 'delete_everything'));
            }
            expect(await audit(page)).toEqual([]);
            if (locale === 'ar') expect(await untranslated(page, '.perm-card', 'bdi, pre, code')).toEqual([]);
            await shot(page, `permission-${kind}-${locale}`);
        });
    }
}

const DECLINED_STEPS = [
    { type: 'tool_start', tool: 'answer_question_about_image', narration: 'Looking at the image' },
    { type: 'tool_result', success: false, summary: "Vision model 'Gemma-4-E4B-it-GGUF' is not downloaded" },
    { type: 'tool_end', success: false },
    { type: 'tool_start', tool: 'run_python', narration: 'Running Python' },
    { type: 'tool_result', success: false, status: 'denied', summary: "Tool 'run_python' was denied by the user." },
    { type: 'tool_end', success: false },
    { type: 'tool_start', tool: 'request_user_input', narration: 'Asking you a question' },
];

for (const locale of ['ar', 'en'] as const) {
    test(`steps name the action, and a declined call says so (${locale})`, async ({ page }) => {
        await page.addInitScript(streamThenWait, DECLINED_STEPS);
        await open(page, `#${ANSWER_SESSION}`, locale);
        await page.locator('textarea').first().fill('what colour is the picture?');
        await page.keyboard.press('Enter');
        const rows = page.locator('.flow-steps.live .flow-tool');
        await expect(rows).toHaveCount(3);
        await expect(rows.nth(1)).toHaveClass(/declined/);
        if (locale === 'en') await expect(rows.nth(1).locator('.flow-tool-label')).toHaveText('You declined: Running Python');
        await expect(page.locator('.flow-tool-label').filter({ hasText: /run python|request user input/ })).toHaveCount(0);
        expect(await audit(page)).toEqual([]);
        await shot(page, `declined-steps-${locale}`);
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

    test(`a path-access prompt keeps a path's double spaces (${locale})`, async ({ page }) => {
        await page.addInitScript(streamThenWait, [{ ...PATH_PROMPT[0], args: { path: QUOTED_PATH, kind: 'file' } }]);
        await open(page, `#${ANSWER_SESSION}`, locale);
        await page.locator('textarea').first().fill('index my budget');
        await page.keyboard.press('Enter');
        const shown = page.locator('.perm-card-question bdi[dir="ltr"]').first();
        await expect(shown).toBeVisible();
        expect(await shown.evaluate((el) => (el as HTMLElement).innerText)).toBe(QUOTED_PATH);
        expect(await audit(page)).toEqual([]);
        await shot(page, `permission-path-spaced-${locale}`);
    });
}

const REPEATED_PROMPTS = ['c1', 'c2', 'c3'].map((id) => ({
    ...PYTHON_PROMPT[0], confirm_id: id, always_scope: 'Python snippets', risk: 'execute',
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

const PANEL_EVENTS = [
    { ...PYTHON_PROMPT[0], confirm_id: 'n-denied' },
    { ...PATH_PROMPT[0], confirm_id: 'n-pending' },
    {
        type: 'policy_alert', tool: 'run_shell_command', decision: 'BLOCK', reason: 'Shell commands are blocked here.',
        rule_ids: ['no-shell'], policy_version: 'v7', receipt_id: 'rcpt-7',
    },
];

for (const locale of ['ar', 'en']) {
    test(`the notification panel's tabs, labels and buttons are in the UI language (${locale})`, async ({ page }) => {
        await page.addInitScript(streamThenWait, PANEL_EVENTS);
        await open(page, `#${ANSWER_SESSION}`, locale, async (p) => {
            await p.route('**/api/chat/confirm-tool', (route) => route.fulfill({ json: { status: 'ok', approved: false, granted: null } }));
        });
        await page.locator('textarea').first().fill('summarize my sales data');
        await page.keyboard.press('Enter');
        await expect(page.locator('.perm-card-title')).toBeVisible();
        await page.locator('.sb-bottom-actions .sb-icon-btn').first().click();
        const panel = page.locator('.notification-center');
        await expect(panel.locator('.notification-item')).toHaveCount(3);
        // Newest first, so the last permission item is the Python prompt.
        await panel.locator('.notification-item.notif-type-permission').last().locator('.notification-action-deny').click();
        await expect(panel.locator('.notification-response-badge')).toHaveCount(1);
        const ar = locale === 'ar';
        await expect(panel.locator('.notification-center-title')).toContainText(ar ? 'الإشعارات' : 'Notifications');
        await expect(panel.getByRole('tab').first()).toContainText(ar ? 'الكل' : 'All');
        await expect(panel.locator('.notification-response-badge')).toContainText(ar ? 'تم الرفض' : 'Denied');
        await expect(panel.locator('.notification-footer-btn').first()).toContainText(ar ? 'تعليم الكل كمقروء' : 'Mark all read');
        expect(await audit(page)).toEqual([]);
        // Agent names, titles, bodies and policy reasons are the notification's own text, not the panel's.
        if (ar) expect(await untranslated(page, '.notification-center', '.notification-agent, .notification-title, .notification-message, .notification-policy-reason')).toEqual([]);
        await shot(page, `notification-panel-${locale}`);
    });
}

for (const locale of ['ar', 'en']) {
    test(`a scheduled task's failed and finished notices are in the UI language (${locale})`, async ({ page }) => {
        const before = { ...SCHEDULE_ROW, run_count: 8, error_count: 0, last_error: null };
        let loads = 0;
        await open(page, '', locale, async (p) => {
            await withSchedules(p);
            await p.route('**/api/schedules', (route) => {
                loads += 1;
                const rows = loads === 1
                    ? [before, { ...SCHEDULE_ROW, id: 's3', name: 'Inbox sweep', run_count: 4, error_count: 0, last_error: null }]
                    : [{ ...before, run_count: 9, error_count: 1, last_error: 'The model server stopped.' }, { ...SCHEDULE_ROW, id: 's3', name: 'Inbox sweep', run_count: 5, error_count: 0, last_error: null }];
                return route.fulfill({ json: { schedules: rows, total: rows.length } });
            });
        });
        await page.locator('.sb').getByRole('button', { name: locale === 'ar' ? /المهام المجدولة/ : /Scheduled tasks/ }).click();
        await expect(page.locator('.schedule-modal')).toBeVisible();
        await page.keyboard.press('Escape');
        await page.locator('.sb-bottom-actions .sb-icon-btn').first().click();
        const titles = page.locator('.notification-center .notification-title');
        await expect(titles).toHaveCount(2);
        if (locale === 'ar') {
            await expect(titles.filter({ hasText: 'فشلت المهمة المجدولة' })).toHaveCount(1);
            await expect(titles.filter({ hasText: 'انتهت المهمة المجدولة' })).toHaveCount(1);
            expect(await untranslated(page, '.notification-center', '.notification-agent, .notification-message, .i18n-gloss, bdi')).toEqual([]);
        } else {
            await expect(titles.filter({ hasText: 'Scheduled task "Morning brief" failed' })).toHaveCount(1);
        }
        expect(await audit(page)).toEqual([]);
        await shot(page, `schedule-notices-${locale}`);
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
        const warning = page.locator('.context-warning');
        await expect(warning).toBeVisible();
        // One short line by default, so it never covers the end of the chat.
        expect((await warning.boundingBox())!.height).toBeLessThan(60);
        await shot(page, `context-warning-${locale}`);
        await warning.getByRole('button', { expanded: false }).click();
        await shot(page, `context-warning-details-${locale}`);
        expect(await audit(page)).toEqual([]);
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

for (const locale of ['en', 'ar']) {
    test(`the context meter explains a fresh chat's fill and shows a dash before it is measured (${locale})`, async ({ page }) => {
        const fresh = { ...MEASURED_CONTEXT, system: 7700, tools: 16000, documents: 0, history: 300, used: 24000, at_risk: [], at_risk_tokens: 0 };
        await open(page, `#${ANSWER_SESSION}`, locale, (p) =>
            p.route(`**/api/chat/context/${ANSWER_SESSION}`, (route) => route.fulfill({ json: fresh })));
        await page.locator('button.context-meter').click();
        await expect(page.locator('.context-meter-note.is-setup')).toBeVisible();
        await shot(page, `context-meter-setup-${locale}`);
        expect(await audit(page)).toEqual([]);
    });

    test(`a reopened chat's context meter shows a dash, not an empty bar (${locale})`, async ({ page }) => {
        await open(page, `#${ANSWER_SESSION}`, locale, (p) =>
            p.route(`**/api/chat/context/${ANSWER_SESSION}`, (route) => route.fulfill({
                json: { available: false, reason: "Measured again with your next message: GAIA restarted or switched models since this chat's last answer." },
            })));
        const meter = page.locator('.context-meter.is-empty');
        await expect(meter).toBeVisible();
        await expect(meter.locator('.context-meter-bar')).toHaveCount(0);
        await shot(page, `context-meter-unmeasured-${locale}`);
        expect(await audit(page)).toEqual([]);
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

for (const locale of ['en', 'ar']) {
    test(`a narrow window keeps the model name readable in the composer (${locale})`, async ({ page }) => {
        await page.setViewportSize({ width: 820, height: 640 });
        await open(page, `#${ANSWER_SESSION}`, locale, withMeasuredContext);
        await expect(page.locator('button.context-meter')).toBeVisible();
        const label = page.locator('.composer-row .composer-chip-label').last();
        await expect(label).toBeVisible();
        await shot(page, `composer-narrow-${locale}`);
        // The day's spend and the context bar give way before the model name is cut.
        const shown = await label.evaluate((el) => el.clientWidth);
        const full = await label.evaluate((el) => el.scrollWidth);
        expect(shown, `model name cut to ${shown}px of ${full}px`).toBeGreaterThanOrEqual(Math.min(full, 140));
        expect(await audit(page)).toEqual([]);
    });
}

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

test('each line of an email being approved takes its own direction', async ({ page }) => {
    const text = 'To: dana@example.com\nSubject: Hello\n\nمرحبا، هذا اختبار.';
    const event = { type: 'permission_request', tool: 'send_now', args: { _exact_text: text }, confirm_id: 'c1' };
    // The prompt lasts only while its stream is open, which a routed response can't do.
    await open(page, `#${ANSWER_SESSION}`, 'ar', (p) =>
        p.addInitScript((line) => {
            const realFetch = window.fetch;
            window.fetch = (input, init) =>
                String(input).endsWith('/chat/send')
                    ? Promise.resolve(new Response(new ReadableStream({
                        start: (controller) => controller.enqueue(new TextEncoder().encode(line)),
                    }), { headers: { 'Content-Type': 'text/event-stream' } }))
                    : realFetch(input, init);
        }, `data: ${JSON.stringify(event)}\n\n`),
    );
    await page.locator('textarea').last().fill('send it');
    await page.keyboard.press('Enter');
    const pre = page.getByTestId('perm-exact-text');
    await expect(pre).toBeVisible();
    const gaps = await pre.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const node = el.firstChild as Text;
        const rectOf = (start: number, end: number) => {
            const range = document.createRange();
            range.setStart(node, start);
            range.setEnd(node, end);
            return range.getBoundingClientRect();
        };
        const source = node.data;
        const arabicAt = source.indexOf('مرحبا');
        return {
            english: rectOf(0, source.indexOf('\n')).left - box.left,
            arabic: box.right - rectOf(arabicAt, source.length).right,
        };
    });
    expect(gaps.english).toBeLessThan(40);
    expect(gaps.arabic).toBeLessThan(40);
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

        // The add field once had the browser's own inset border and no padding, and Undo was link blue.
        test('about you, the add field and a forgotten memory', async ({ page }) => {
            await openMemory(page, locale);
            await page.route('**/api/memory/knowledge/*', (route) =>
                route.request().method() === 'DELETE' ? route.fulfill({ json: { deleted: true } }) : route.fallback());
            const field = page.locator('.mem-about-add .mem-input');
            const look = await field.evaluate((el) => {
                const s = getComputedStyle(el);
                return { pad: parseFloat(s.paddingInlineStart), radius: parseFloat(s.borderTopLeftRadius), border: s.borderTopStyle, width: parseFloat(s.borderTopWidth) };
            });
            expect(look.pad).toBeGreaterThanOrEqual(8);
            expect(look.radius).toBeGreaterThan(0);
            expect(look).toMatchObject({ border: 'solid', width: 1 });
            const item = page.locator('.mem-about-item', { hasText: 'Lives in Calgary' });
            await item.locator('.mem-about-btn-remove').click();
            const undo = page.locator('.mem-about-forgotten .mem-about-link');
            await expect(undo).toBeVisible();
            const [undoColor, accent] = await undo.evaluate((el) => {
                const probe = document.createElement('span');
                probe.style.color = 'var(--accent)';
                el.parentElement!.appendChild(probe);
                const want = getComputedStyle(probe).color;
                probe.remove();
                return [getComputedStyle(el).color, want];
            });
            expect(undoColor).toBe(accent);
            await checkMemory(page, locale, 'about-forgotten');
        });

        test('about you, editing a memory', async ({ page }) => {
            await openMemory(page, locale);
            const item = page.locator('.mem-about-item', { hasText: 'Lives in Calgary' });
            await item.locator('.mem-about-btn').first().click();
            await expect(item.locator('textarea')).toBeVisible();
            await checkMemory(page, locale, 'about-editing');
        });

        test('about you, more than one page', async ({ page }) => {
            await openMemory(page, locale, { more: true });
            await expect(page.locator('.mem-about-more .btn-secondary')).toBeVisible();
            await checkMemory(page, locale, 'about-more');
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

        // The page once read as a developer console: monospace tabs and spaced capitals.
        test('advanced, in the app\'s own type', async ({ page }) => {
            await openMemory(page, locale);
            await memoryTab(page, /Advanced/);
            await expect(page.getByText('run_shell')).toBeVisible();
            const looks = await page.evaluate(() => [...document.querySelectorAll('.mem-tab-btn, .mem-section-title, .mem-knowledge-table th, .mem-stat-sub')]
                .map((el) => ({ cls: el.className, font: getComputedStyle(el).fontFamily, transform: getComputedStyle(el).textTransform })));
            expect(looks.length).toBeGreaterThan(8);
            expect(looks.filter((l) => /JetBrains|monospace/.test(l.font) || l.transform === 'uppercase')).toEqual([]);
            const cards = await page.locator('.mem-stat-card').evaluateAll((els) => new Set(els.map((el) => Math.round(el.getBoundingClientRect().top))).size);
            expect(cards).toBe(1);
        });

        test('advanced, a tool-recorded error next to one the user wrote', async ({ page }) => {
            await openMemory(page, locale);
            await memoryTab(page, /Advanced/);
            const badge = (text: string) => page.locator('.mem-knowledge-table tbody tr', { hasText: text }).locator('.mem-cat-badge');
            const learned = badge('note-taker');
            await expect(learned).toContainText(locale === 'ar' ? 'تعلّمه GAIA' : 'Learned by GAIA');
            await expect(badge('hotel wifi')).toContainText(locale === 'ar' ? 'خطأ' : 'Error');
            await expect(badge('hotel wifi')).not.toContainText(locale === 'ar' ? 'تعلّمه' : 'Learned');
            if (locale === 'ar') {
                // Read right to left: the Arabic verb sits right of "GAIA".
                const [verbLeft, gaiaLeft] = await learned.evaluate((el) => {
                    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
                    let node = walker.nextNode() as Text | null;
                    while (node && !node.data.includes('تعلّمه')) node = walker.nextNode() as Text | null;
                    if (!node) throw new Error('no Arabic label in the badge');
                    const at = (word: string) => {
                        const start = node!.data.indexOf(word);
                        const range = document.createRange();
                        range.setStart(node!, start);
                        range.setEnd(node!, start + word.length);
                        return range.getBoundingClientRect().left;
                    };
                    return [at('تعلّمه'), at('GAIA')];
                });
                expect(gaiaLeft).toBeLessThan(verbLeft);
            }
            await learned.scrollIntoViewIfNeeded();
            await checkMemory(page, locale, 'advanced-errors');
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

const INDEXED_MANUAL = {
    id: 'd-manual', filename: 'plant_manual.md', filepath: 'C:\\Users\\you\\.gaia\\documents\\3f9a1c2b_plant_manual.md',
    file_size: 2_048, chunk_count: 3, indexed_at: NOW.toISOString(), last_accessed_at: null, sessions_using: 1,
    indexing_status: 'complete',
};

const COPY_NOTICE = 'plant_manual.md is outside the folders your documents are read from, so your documents hold a copy '
    + 'of it. Changes to D:\\gtest\\plant_manual.md won\'t reach the copy.';
const INDEX_REQUEST = 'Add D:\\gtest\\plant_manual.md to my documents';
const INDEX_ANSWER = 'Added plant_manual.md to your documents.';

/** "Add it to my documents" on a file outside the document folders, as the backend streams it. */
const INDEXED_OUTSIDE = [
    { type: 'tool_start', tool: 'index_document', detail: '', narration: '' },
    { type: 'tool_result', summary: 'Indexed plant_manual.md (3 chunks)', success: true },
    { type: 'status', status: 'warning', message: COPY_NOTICE },
    { type: 'chunk', content: INDEX_ANSWER },
    { type: 'done', message_id: 4, content: INDEX_ANSWER, documents_added: [INDEXED_MANUAL] },
];

/** The chat as saved once that turn ends, which the app reloads. */
const SAVED_AFTER_INDEXING = {
    messages: [
        ...MESSAGES.messages,
        { id: 3, session_id: ANSWER_SESSION, role: 'user', content: INDEX_REQUEST, created_at: NOW.toISOString(), rag_sources: null },
        {
            id: 4, session_id: ANSWER_SESSION, role: 'assistant', content: INDEX_ANSWER, created_at: NOW.toISOString(), rag_sources: null,
            agent_steps: [
                { id: 1, type: 'tool', label: 'Using tool', tool: 'index_document', result: 'Indexed plant_manual.md (3 chunks)', active: false, success: true, latencyMs: 900 },
            ],
        },
    ],
    total: 4,
};

for (const locale of ['ar', 'en']) {
    test(`a document the agent indexes joins the chat's document bar (${locale})`, async ({ page }) => {
        await page.addInitScript(streamThenWait, INDEXED_OUTSIDE);
        await open(page, `#${ANSWER_SESSION}`, locale);
        await page.locator('textarea').first().fill(INDEX_REQUEST);
        await page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) => route.fulfill({ json: SAVED_AFTER_INDEXING }));
        await page.keyboard.press('Enter');
        await expect(page.locator('.doc-pill-name')).toHaveText('plant_manual.md');
        await expect(page.getByText(INDEX_ANSWER)).toBeVisible();
        expect(await audit(page)).toEqual([]);
        await shot(page, `agent-indexed-document-${locale}`);
    });
}

const PICKED = 'Qwen3.6-35B-A3B-GGUF';

/** The model list offers a downloaded Qwen; picking it starts its load, shown by *warmUp*. */
async function withPickedModelLoad(page: Page, warmUp: unknown): Promise<void> {
    await page.route('**/api/providers/local/models', (route) => route.fulfill({
        json: { models: [
            { id: 'Gemma-4-E4B-it-GGUF', name: 'Gemma-4-E4B-it-GGUF', downloaded: true, loaded: true, size_gb: 4.9 },
            { id: PICKED, name: PICKED, downloaded: true, loaded: false, size_gb: 23.3 },
        ] },
    }));
    await page.route('**/api/providers/select', (route) => route.fulfill({
        json: { provider: 'local', model: PICKED, label: `${PICKED} · local`, remote: false, is_default: false },
    }));
    await page.route('**/api/system/warm-up', (route) => route.fulfill({
        json: route.request().method() === 'GET' ? warmUp : SYSTEM_STATUS.warm_up,
    }));
}

const PICK_SCENES = [
    { name: 'loading', warmUp: { state: 'warming', elapsed_seconds: 12.4, model: PICKED, device: 'gpu' }, text: /12/ },
    {
        name: 'failed',
        warmUp: {
            state: 'failed', elapsed_seconds: 3, model: PICKED, device: 'gpu',
            error: `Not enough free memory to load ${PICKED} with a 65,536-token window: it needs about 33.0 GB and only 3.7 GB is free. Close apps that use a lot of memory (web browsers, games, other AI apps, or another Lemonade server and the models it holds), then try again.`,
        },
        text: /3\.7 GB/,
    },
];

for (const locale of ['ar', 'en']) {
    for (const { name, warmUp, text } of PICK_SCENES) {
        test(`a model picked from the list says how its load goes, with the message box open (${name}, ${locale})`, async ({ page }) => {
            await open(page, '', locale, (p) => withPickedModelLoad(p, warmUp));
            await page.getByRole('button', { name: /^Gemma-4-E4B-it-GGUF · / }).click();
            await page.getByRole('option', { name: new RegExp(PICKED) }).first().click();
            const banner = page.locator('.connection-banner');
            await expect(banner).toContainText(PICKED);
            await expect(banner).toContainText(text);
            await expect(page.getByLabel(locale === 'ar' ? /رسالة|Message/ : 'Message').first()).toBeEnabled();
            await shot(page, `pick-load-${locale}-${name}`);
            expect(await audit(page)).toEqual([]);
        });
    }
}

/** Text where an object arrives crashes the screen that renders it, so each placement's fallback shows. */
const BROKEN = { not: 'text' };
const CRASHES: Record<'main' | 'sidebar' | 'window', { hash: string; before: (page: Page) => Promise<void> }> = {
    main: {
        hash: `#${ANSWER_SESSION}`,
        before: (page) => page.route((url) => url.pathname === `/api/sessions/${ANSWER_SESSION}/messages`, (route) =>
            route.fulfill({ json: { messages: [{ id: 1, session_id: ANSWER_SESSION, role: 'user', content: BROKEN, created_at: NOW.toISOString(), rag_sources: null }] } })),
    },
    sidebar: {
        hash: '',
        before: (page) => page.route((url) => url.pathname === '/api/sessions', (route) =>
            route.fulfill({ json: { sessions: [{ ...SESSIONS[0], title: BROKEN }], total: 1 } })),
    },
    window: {
        hash: '',
        before: (page) => page.route((url) => url.pathname === '/api/providers/active', (route) =>
            route.fulfill({ json: { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma-4-E4B-it-GGUF · local', remote: false, is_default: true, restore: { status: 'failed', message: BROKEN } } })),
    },
};

for (const locale of ['ar', 'en']) {
    for (const [placement, crash] of Object.entries(CRASHES)) {
        test(`a crashed ${placement} shows its own fallback (${locale})`, async ({ page }, info) => {
            const errors: string[] = [];
            page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
            await open(page, crash.hash, locale, crash.before);
            const fallback = page.getByRole('alert').filter({ has: page.locator('.error-boundary-title') });
            await expect(fallback).toBeVisible();
            await expect(fallback).toHaveCount(1);
            expect(errors.some((e) => e.includes(`[ErrorBoundary] ${placement === 'window' ? 'app' : placement} failed to render`))).toBe(true);

            const box = (await fallback.boundingBox())!;
            const title = (await fallback.locator('.error-boundary-title').boundingBox())!;
            if (placement === 'window') {
                await expect(fallback).toHaveText(locale === 'ar' ? /أعد التحميل للمتابعة/ : /Reload to continue/);
                expect(box.width).toBe(1400);
                expect(box.height).toBe(900);
                expect(Math.abs(title.y + title.height / 2 - 450)).toBeLessThan(80);
            } else {
                await expect(fallback).toHaveText(locale === 'ar' ? /بقية التطبيق لا تزال تعمل/ : /The rest of the app still works/);
            }
            if (placement === 'sidebar') {
                expect(box.width).toBeLessThanOrEqual(261);
                expect((await page.locator('.main-content').boundingBox())!.width).toBeGreaterThan(1100);
                await expect(page.getByLabel(locale === 'ar' ? /رسالة/ : 'Message').first()).toBeVisible();
            }
            if (placement === 'main') {
                await expect(page.locator('aside.sb')).toBeVisible();
                const main = (await page.locator('.main-content').boundingBox())!;
                expect(box.x).toBeGreaterThanOrEqual(main.x);
                expect(box.x + box.width).toBeLessThanOrEqual(main.x + main.width + 1);
            }
            expect(await audit(page)).toEqual([]);
            await page.screenshot({ path: info.outputPath(`crashed-${placement}-${locale}.png`) });
        });
    }
}


const SKILL_LINK = 'https://github.com/michael-denyer/pstack-claude';
const SKILL_LINK_PIN = `commit:${'4f2a9c1e'.repeat(5)}`;
const linkSkill = (name: string, description: string, over: Record<string, unknown> = {}) => ({
    name, description, path: `skills/${name}`, has_code: false, permissions: [], permissions_unreadable: false, verdict: 'ALLOW',
    findings: [], installed: false, update: false, error: null, ...over,
});
const SKILL_LINK_PREVIEW = {
    link: SKILL_LINK,
    source: 'michael-denyer/pstack-claude',
    pin: SKILL_LINK_PIN,
    warning: 'Skills from a link are written by someone else. A skill\'s instructions steer what GAIA does with your files, shell, and accounts, and a skill that ships code (tools.py or scripts/) runs that code on this PC. GAIA audits each skill and refuses anything the audit blocks, but an audit is not a guarantee. Only add skills from people you trust, and read what they do first.',
    skills: [
        linkSkill('design-review', 'Review a design doc for gaps before any code is written.'),
        linkSkill('docs-lookup', 'Look up library documentation and cite the page it came from.', {
            permissions: [{ grant: 'network:read:*.readthedocs.io', text: 'Read pages and data from sites matching *.readthedocs.io' }],
            verdict: 'REVIEW',
            findings: ['[REVIEW] network: fetches remote content — check what it sends'],
        }),
        linkSkill('ملخص-الاجتماع', 'يلخص محضر الاجتماع ويستخرج المهام.', { permissions: [{ grant: 'scratch:write', text: 'Write files in a temporary folder that is emptied after each use' }] }),
        linkSkill('ci-fix', 'Reproduce a red CI job locally and fix the cause.', {
            has_code: true,
            permissions: [{ grant: 'scratch:write', text: 'Write files in a temporary folder that is emptied after each use' }],
        }),
        linkSkill('pr-hygiene', 'Tidy a pull request before review.', { installed: true, update: true }),
        linkSkill('release-notes', 'Draft release notes from merged pull requests.', { installed: true }),
        linkSkill('gh-triage', 'Label and triage GitHub issues.', {
            permissions: [{ grant: 'shell:execute:gh', text: 'Run the \'gh\' command: GitHub CLI — reads issues, pull requests, releases, and runs; comments, pull requests, and labels only with your per-command approval.' }],
            error: 'Skill \'gh-triage\' declares permission(s) above the \'experimental\' ceiling: shell:execute. Install a copy signed at \'community\' or higher from the hub instead.',
        }),
    ],
};
const SKILL_LINK_RESULT = {
    added: [
        { name: 'design-review', updated: false, verdict: 'ALLOW', has_code: false, findings: [] },
        { name: 'docs-lookup', updated: false, verdict: 'REVIEW', has_code: false, findings: [] },
        { name: 'pr-hygiene', updated: true, verdict: 'ALLOW', has_code: false, findings: [] },
    ],
    failed: { 'ملخص-الاجتماع': 'A skill with this name is already installed from somewhere else. Remove it first, or pass --force to replace it.' },
};

for (const locale of ['ar', 'en']) {
    test(`add skills from a link: preview and result (${locale})`, async ({ page }) => {
        const posted: Array<Record<string, unknown>> = [];
        await open(page, '', locale, async (p) => {
            await withSettingsPanes(p);
            await p.route('**/api/skills/link/*', async (route) => {
                const body = route.request().postDataJSON() as Record<string, unknown>;
                posted.push(body);
                await route.fulfill({ json: route.request().url().endsWith('/preview') ? SKILL_LINK_PREVIEW : SKILL_LINK_RESULT });
            });
        });
        await page.getByRole('button', { name: /Settings/ }).first().click();
        await page.getByRole('dialog').locator('[data-section="skills"]').click();
        const field = page.locator('.skill-link-form input');
        await field.fill(SKILL_LINK);
        await field.press('Enter');

        const preview = page.locator('.skill-link-preview');
        await expect(preview).toBeVisible();
        await expect(preview.locator('.skill-link-perms')).toHaveCount(SKILL_LINK_PREVIEW.skills.length);
        await expect(preview.locator('.skill-link-perms code', { hasText: 'network:read:*.readthedocs.io' })).toBeVisible();
        await preview.locator('details:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
        await preview.scrollIntoViewIfNeeded();
        await shot(page, `skills-link-preview-${locale}`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.skill-link-preview')).toEqual([]);
        await scrollPane(page, 'bottom');
        await shot(page, `skills-link-preview-${locale}-bottom`);

        await preview.locator('.skill-link-actions .btn-primary').click();
        const result = page.locator('.skill-link-result');
        await expect(result).toBeVisible();
        expect(posted[posted.length - 1]).toMatchObject({ url: SKILL_LINK, pin: SKILL_LINK_PIN });
        await result.locator('details:not([open]) > summary').evaluateAll((els) => els.forEach((el) => (el as HTMLElement).click()));
        await result.scrollIntoViewIfNeeded();
        await shot(page, `skills-link-result-${locale}`);
        expect(await audit(page)).toEqual([]);
        if (locale === 'ar') expect(await untranslated(page, '.skill-link-result')).toEqual([]);
    });
}
