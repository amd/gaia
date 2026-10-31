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

import { ANSWER_SESSION, NOW, mockApi, useLocale, useTheme } from './fixtures';

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

async function open(page: Page, hash = '', locale = 'ar', before?: (page: Page) => Promise<void>): Promise<void> {
    await page.clock.setFixedTime(NOW);
    await mockApi(page);
    await before?.(page);
    await useTheme(page, 'light');
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
            if (el.children.length > 0 || !text) continue;

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
});
