// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * The indexing view, fed real progress payloads. Checks layout in both directions,
 * in both languages; set GAIA_INDEXING_SHOTS to a folder to also save each state
 * for reading by eye.
 *
 *   npm run test:rtl
 */

import { expect, test, type Page } from '@playwright/test';

import { ANSWER_SESSION, NOW, mockApi, session, useLocale, useTheme } from './fixtures';

const SHOTS = process.env.GAIA_INDEXING_SHOTS;

const row = (id: string, filename: string, indexing_status: string, extra: object = {}) => ({
    id,
    filename,
    filepath: `C:\\Users\\me\\Documents\\${filename}`,
    file_size: 8_400_000,
    chunk_count: indexing_status === 'complete' ? 412 : 0,
    indexed_at: NOW.toISOString(),
    last_accessed_at: null,
    sessions_using: 1,
    indexing_status,
    ...extra,
});

const reading = { phase: 'reading', done: 37, total: 229, seconds_left: 340, estimate: 'ready', elapsed_seconds: 95 };
const queued = { phase: 'queued', done: 0, total: null, seconds_left: null, estimate: 'estimating', elapsed_seconds: 0 };

const DOCS = [
    row('i1', 'annual-report-2025.pdf', 'indexing'),
    row('i2', 'تقرير الميزانية.pdf', 'indexing'),
    row('i3', 'network-survey.pdf', 'complete'),
    row('i4', 'scanned-contract.pdf', 'failed', { last_error: 'RAG indexing failed for scanned-contract.pdf: the embedding model stopped responding.' }),
    row('i5', 'old-handbook.pdf', 'cancelled'),
];

const overview = (over: object = {}) => ({
    batch_id: 'b1',
    active: true,
    total: 7,
    done: 3,
    failed: 1,
    cancelled: 1,
    current_id: 'i1',
    current: 'annual-report-2025.pdf',
    percent: 41,
    seconds_left: 720,
    estimate: 'ready',
    documents: [
        { id: 'i1', filename: 'annual-report-2025.pdf', state: 'running', progress: reading, error: null },
        { id: 'i2', filename: 'تقرير الميزانية.pdf', state: 'queued', progress: queued, error: null },
    ],
    ...over,
});

const STATES: Record<string, object> = {
    running: {},
    estimating: { seconds_left: null, estimate: 'estimating', percent: 3, done: 0, failed: 0, cancelled: 0 },
    atLeast: { estimate: 'at_least' },
    stalled: { seconds_left: null, estimate: 'stalled' },
    finishedWithFailure: {
        active: false, done: 5, failed: 2, cancelled: 0, current_id: null, current: null, percent: 100, seconds_left: null,
        documents: [{ id: 'i4', filename: 'scanned-contract.pdf', state: 'failed', progress: null, error: 'stopped' }],
    },
};

async function open(page: Page, state: object, theme: 'light' | 'dark', locale: string, hash = ''): Promise<void> {
    await page.clock.setFixedTime(NOW);
    await mockApi(page);
    await page.route('**/api/documents', (route) =>
        route.fulfill({ json: { documents: DOCS, total: 5, total_size_bytes: 42_000_000, total_chunks: 412 } }),
    );
    await page.route('**/api/documents/indexing', (route) => route.fulfill({ json: overview(state) }));
    await page.route('**/api/documents/*/status', (route) =>
        route.fulfill({
            json: {
                id: 'x', indexing_status: 'indexing', chunk_count: 0, is_active: true,
                progress: route.request().url().includes('/i1/') ? reading : queued,
            },
        }),
    );
    await page.route(/\/api\/sessions(\?.*)?$/, (route) =>
        route.fulfill({
            json: {
                sessions: [{ ...session(ANSWER_SESSION, 'Questions about the annual report', 2, 2), document_ids: ['i1', 'i2', 'i3', 'i5'] }],
                total: 1,
            },
        }),
    );
    await useTheme(page, theme);
    await useLocale(page, locale);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`/${hash}`);
    await page.evaluate(() => document.fonts.ready);
}

async function shot(page: Page, name: string): Promise<void> {
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

/** Whatever sticks out of the window, or scrolls the page sideways. */
const overflow = (page: Page): Promise<string[]> =>
    page.evaluate(() => {
        const problems: string[] = [];
        if (document.documentElement.scrollWidth > innerWidth) problems.push('the page scrolls sideways');
        for (const el of document.querySelectorAll('.index-indicator *, .index-summary *, .index-chat-notice *, .index-progress *')) {
            const box = el.getBoundingClientRect();
            if (box.width > 0 && (box.right > innerWidth + 1 || box.left < -1)) problems.push(`off screen: ${String(el.className)}`);
        }
        for (const el of document.querySelectorAll('.index-chat-notice')) {
            if (el.scrollWidth > el.clientWidth + 1) problems.push('the chat notice is cut off');
        }
        return problems;
    });

const library = (page: Page) => page.locator('.sb').getByRole('button', { name: /Documents/ }).click();

for (const locale of ['en', 'ar']) {
    for (const theme of ['light', 'dark'] as const) {
        test(`indicator and library, ${locale} ${theme}`, async ({ page }, info) => {
            test.skip(info.project.name !== 'light');
            await page.setViewportSize({ width: 1280, height: 800 });
            await open(page, STATES.running, theme, locale);
            await expect(page.locator('.index-indicator')).toBeVisible();
            await shot(page, `indicator-${locale}-${theme}`);
            expect(await overflow(page)).toEqual([]);

            await library(page);
            await expect(page.locator('.index-summary')).toBeVisible();
            await expect(page.locator('.index-progress-eta').first()).toBeVisible();
            await expect(page.locator('.index-indicator')).toHaveCount(0);
            await shot(page, `library-${locale}-${theme}`);
            expect(await overflow(page)).toEqual([]);
        });
    }

    test(`phone width, ${locale}`, async ({ page }, info) => {
        test.skip(info.project.name !== 'light');
        await page.setViewportSize({ width: 390, height: 780 });
        await open(page, STATES.running, 'light', locale);
        await expect(page.locator('.index-indicator')).toBeVisible();
        await shot(page, `phone-indicator-${locale}`);
        expect(await overflow(page)).toEqual([]);

        await page.locator('.index-indicator-open').click();
        await expect(page.locator('.index-summary')).toBeVisible();
        await shot(page, `phone-library-${locale}`);
        expect(await overflow(page)).toEqual([]);
    });

    for (const theme of ['light', 'dark'] as const) {
        test(`a chat with a document still indexing, ${locale} ${theme}`, async ({ page }, info) => {
            test.skip(info.project.name !== 'light');
            await page.setViewportSize({ width: 1280, height: 800 });
            await open(page, STATES.running, theme, locale, `#${ANSWER_SESSION}`);
            await expect(page.locator('.index-chat-notice')).toBeVisible();
            await expect(page.locator('.index-chat-notice p')).toHaveCount(2);
            await shot(page, `chat-${locale}-${theme}`);
            expect(await overflow(page)).toEqual([]);
        });
    }

    test(`a chat at phone width, ${locale}`, async ({ page }, info) => {
        test.skip(info.project.name !== 'light');
        await page.setViewportSize({ width: 390, height: 780 });
        await open(page, STATES.running, 'light', locale, `#${ANSWER_SESSION}`);
        await expect(page.locator('.index-chat-notice')).toBeVisible();
        await shot(page, `phone-chat-${locale}`);
        expect(await overflow(page)).toEqual([]);
    });

    for (const name of ['estimating', 'atLeast', 'stalled', 'finishedWithFailure']) {
        test(`${name}, ${locale}`, async ({ page }, info) => {
            test.skip(info.project.name !== 'light');
            await page.setViewportSize({ width: 1280, height: 800 });
            await open(page, STATES[name], 'light', locale);
            await expect(page.locator('.index-indicator')).toBeVisible();
            await shot(page, `${name}-${locale}`);
            expect(await overflow(page)).toEqual([]);
        });
    }
}
