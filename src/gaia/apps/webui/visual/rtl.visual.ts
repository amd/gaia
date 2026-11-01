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

import { ANSWER_SESSION, MODEL_LIST, NOW, mockApi, useLocale, useTheme, withConnectors, withModelPage, withSettingsPanes } from './fixtures';

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
        const latin = /[A-Za-z]/;
        const arabic = /[\u0600-\u06ff]/;

        if (document.documentElement.scrollWidth > innerWidth) problems.push('the page scrolls sideways');

        for (const el of document.querySelectorAll('body *')) {
            if (!shown(el)) continue;
            const box = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            if ((box.right > innerWidth + 1 || box.left < -1) && style.position !== 'fixed' && !el.closest('svg')) {
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

/** First-run setup, with the model screen's data from the backend shapes. */
async function withSetup(page: Page, status: object = { state: 'idle' }): Promise<void> {
    const answers: Record<string, unknown> = {
        'GET /setup/check': { ready: false, stage: 'setup', reasons: [], steps: [{ key: 'server', label: 'Install the local model server', detail: 'about 1 minute' }] },
        'GET /setup/plan': SETUP_PLAN,
        'GET /setup/status': status,
        'GET /gpu-memory': SETUP_GPU,
        'GET /onboarding/preflight': { compatible: true, blockers: [], warnings: [], ram_gb: 128, gpu_name: 'AMD Radeon 8060S', npu_detected: true, disk_free_gb: 25, lemonade_running: true },
        'GET /lemonade/target': { mode: 'gaia', saved_mode: 'gaia', source: 'saved', base_url: null, saved_base_url: null, api_key_stored: false, offer: false, what_gaia_does: '' },
    };
    await page.route('**/api/**', async (route) => {
        const url = new URL(route.request().url());
        const key = `${route.request().method()} ${url.pathname.replace(/^\/api/, '')}`;
        if (key in answers) await route.fulfill({ json: answers[key] });
        else await route.fallback();
    });
}

for (const locale of ['ar', 'en']) {
    test.describe(`first-run setup (${locale})`, () => {
        test('choosing models, with the won\'t-fit and disk warnings', async ({ page }) => {
            await open(page, '', locale, (p) => withSetup(p));
            await page.locator('.setup-step.is-current .btn-primary').click();
            await expect(page.locator('.mc-list').first()).toBeVisible();
            await page.locator('.mc-more summary').first().click();
            await page.locator('.mc-more').first().getByRole('checkbox').first().click();
            await expect(page.locator('.mc-disk-warn')).toBeVisible();
            await expect(page.locator('.mc-warn.is-big')).toBeVisible();
            expect(await audit(page)).toEqual([]);
        });

        test('downloading, one row failed', async ({ page }) => {
            await open(page, '', locale, (p) => withSetup(p, SETUP_RUNNING));
            await expect(page.locator('.dl-row.is-failed')).toBeVisible();
            expect(await audit(page)).toEqual([]);
        });

        test('phone width: the default chat model select keeps its full width', async ({ page }) => {
            await page.setViewportSize({ width: 390, height: 844 });
            await open(page, '', locale, (p) => withSetup(p));
            await page.locator('.setup-step.is-current .btn-primary').click();
            const select = page.locator('.mc-select');
            await expect(select).toBeVisible();
            const [box, row] = await Promise.all([select.boundingBox(), page.locator('.mc-default').boundingBox()]);
            // Wrapped onto its own line at the row's full width, not squeezed beside the label.
            expect(box!.width).toBeGreaterThan(row!.width * 0.9);
            expect(await select.evaluate((e) => e.scrollWidth <= e.clientWidth)).toBe(true);
            expect(await audit(page)).toEqual([]);
        });

        test('narrow window: no sideways scroll', async ({ page }) => {
            await page.setViewportSize({ width: 320, height: 720 });
            await open(page, '', locale, (p) => withSetup(p));
            await page.locator('.setup-step.is-current .btn-primary').click();
            await expect(page.locator('.mc-list').first()).toBeVisible();
            expect(await audit(page)).toEqual([]);
        });
    });
}

test('window controls take the color of the sidebar beneath them', async ({ page }) => {
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
    await open(page);
    const [last, sidebar] = await page.evaluate(() => [
        (window as unknown as { __colors: { color: string }[] }).__colors.at(-1)?.color,
        getComputedStyle(document.documentElement).getPropertyValue('--bg-sidebar').trim(),
    ]);
    expect(last).toBe(sidebar);

    await page.locator('.sb').getByRole('button', { name: /الإعدادات/ }).first().click();
    await expect(page.locator('.settings-overlay')).toBeVisible();
    await expect.poll(async () => brightness(await lastColor(page))).toBeLessThan(brightness(sidebar));

    await page.keyboard.press('Escape');
    await expect(page.locator('.settings-overlay')).toHaveCount(0);
    await expect.poll(() => lastColor(page)).toBe(sidebar);
});

test('the agent notice and the memory chip are in Arabic', async ({ page }) => {
    await open(page);
    await expect(page.locator('.app-notice')).toContainText('غير مثبّت');
    await expect(page.getByRole('button', { name: /تفعيل المحادثة الخاصة/ })).toContainText(/\p{Script=Arabic}/u);
});

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
