// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Auto-follow while a reply streams, in a real browser: smooth scrolling and
 * scroll anchoring are what jsdom can't run, so the unit tests can't see them.
 *
 * The reply streams from an in-page fetch stub at about 65 chunks a second.
 * These read scroll geometry, need no baseline and run on any OS:
 *
 *   npm run test:scroll
 */

import { expect, test, type Page } from '@playwright/test';

import { ANSWER_SESSION, mockApi, useLocale, useTheme } from './fixtures';

test.use({
    viewport: { width: 1400, height: 900 },
    // Headless Chromium hides scrollbars by default; the drag tests need a real one.
    launchOptions: { executablePath: process.env.GAIA_VISUAL_CHROMIUM || undefined, ignoreDefaultArgs: ['--hide-scrollbars'] },
});

/** Stream `chunk` events into POST /api/chat/send until the page closes; `__pauseStream` holds them. */
function streamReplies(): void {
    const words = 'The trail crew met at dawn and walked the ridge to check every marker post. '.split(' ');
    const realFetch = window.fetch.bind(window);
    const w = window as unknown as { __pauseStream: boolean; __chunks: number };
    w.__pauseStream = false;
    w.__chunks = 0;
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (!url.includes('/api/chat/send')) return realFetch(input, init);
        const encoder = new TextEncoder();
        let timer: ReturnType<typeof setInterval>;
        const body = new ReadableStream<Uint8Array>({
            start(controller) {
                let n = 0;
                timer = setInterval(() => {
                    if (w.__pauseStream) return;
                    const word = words[n++ % words.length];
                    const content = (n % 40 === 0 ? '\n\n' : ' ') + word;
                    w.__chunks++;
                    controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: 'chunk', content })}\n\n`));
                }, 15);
            },
            cancel() { clearInterval(timer); },
        });
        return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
    };
}

let unhandled: string[] = [];

test.beforeEach(() => {
    unhandled = [];
});

test.afterEach(() => {
    expect(unhandled, 'add a fixture to visual/fixtures.ts ROUTES for each call').toEqual([]);
});

const list = (page: Page) => page.locator('.messages-scroll');

const distFromBottom = (page: Page): Promise<number> =>
    list(page).evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);

const scrollTop = (page: Page): Promise<number> => list(page).evaluate((el) => el.scrollTop);

/** Open the answered chat, send a prompt, and wait until the reply overflows the list and is followed. */
async function streamingChat(page: Page, locale = 'en'): Promise<void> {
    await page.addInitScript(streamReplies);
    unhandled = await mockApi(page);
    await useTheme(page, 'light');
    await useLocale(page, locale);
    await page.goto(`/#${ANSWER_SESSION}`);
    await expect(page.locator('.flow-steps-summary').first()).toBeVisible();
    const box = page.getByRole('textbox').first();
    await box.fill('Write a long field report');
    await box.press('Enter');
    await expect.poll(() => list(page).evaluate((el) => el.scrollHeight - el.clientHeight), { timeout: 15_000 })
        .toBeGreaterThan(900);
    await expect.poll(() => distFromBottom(page), { timeout: 5_000 }).toBeLessThan(40);
}

async function wheelOverList(page: Page, deltaY: number): Promise<void> {
    const box = await list(page).boundingBox();
    if (!box) throw new Error('the message list is not on screen');
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, deltaY);
}

test('a wheel up mid-stream holds the reader where they stopped', async ({ page }) => {
    await streamingChat(page);
    await wheelOverList(page, -400);
    await page.waitForTimeout(500);
    const held = await scrollTop(page);
    await page.waitForTimeout(1500);
    expect(Math.abs((await scrollTop(page)) - held), 'the view moved while the reader was above').toBeLessThan(2);
    expect(await distFromBottom(page)).toBeGreaterThan(300);
});

test('a card above toggling mid-stream does not stop following', async ({ page }) => {
    await streamingChat(page);
    const card = page.locator('.flow-steps-summary').first();
    for (let burst = 0; burst < 3; burst++) {
        for (let i = 0; i < 4; i++) {
            // A DOM click: Playwright's click would scroll the card into view first.
            await card.evaluate((el: HTMLElement) => el.click());
            await page.waitForTimeout(120 + 40 * i);
        }
        await expect.poll(() => distFromBottom(page), {
            message: 'the reply stopped following after a card above changed size',
            timeout: 3_000,
        }).toBeLessThan(40);
    }
});

test('content growing above a reader 40px from the bottom holds them', async ({ page }) => {
    await streamingChat(page);
    await page.evaluate(() => { (window as unknown as { __pauseStream: boolean }).__pauseStream = true; });
    await expect.poll(() => distFromBottom(page), { timeout: 3_000 }).toBeLessThan(2);
    await wheelOverList(page, -40);
    await expect.poll(() => distFromBottom(page), { timeout: 3_000 }).toBeGreaterThan(30);
    await page.waitForTimeout(600);
    const before = await distFromBottom(page);
    // An image above finishes loading: anchoring pushes scrollTop down by its height.
    await list(page).evaluate((el) => {
        const img = document.createElement('div');
        img.style.height = '320px';
        el.querySelector('.flow-steps-summary')?.closest('.flow-steps')?.before(img);
    });
    await page.evaluate(() => { (window as unknown as { __pauseStream: boolean }).__pauseStream = false; });
    await page.waitForTimeout(1500);
    expect(await distFromBottom(page), 'the reader was pulled back to the bottom').toBeGreaterThanOrEqual(before - 2);
});

for (const locale of ['en', 'ar']) {
    test(`dragging the scrollbar up mid-stream holds the reader (${locale})`, async ({ page }) => {
        await streamingChat(page, locale);
        // A point on the list's own scrollbar that nothing else (a resize handle) covers.
        const grip = await list(page).evaluate((el) => {
            const box = el.getBoundingClientRect();
            const gutter = el.offsetWidth - el.clientWidth;
            const rtl = getComputedStyle(el).direction === 'rtl';
            const y = box.bottom - 30;
            for (let i = 0; i < gutter; i++) {
                const x = rtl ? box.left + 0.5 + i : box.right - 0.5 - i;
                if (document.elementFromPoint(x, y) === el) return { x, y, gutter };
            }
            return { x: -1, y, gutter };
        });
        expect(grip.gutter, 'no classic scrollbar to drag').toBeGreaterThan(0);
        expect(grip.x, 'the scrollbar is covered by another element').toBeGreaterThan(0);
        const { x, y } = grip;
        await page.mouse.move(x, y);
        await page.mouse.down();
        for (let step = 1; step <= 10; step++) {
            await page.mouse.move(x, y - 15 * step);
            await page.waitForTimeout(30);
        }
        await page.waitForTimeout(300);
        const held = await scrollTop(page);
        await page.waitForTimeout(1000);
        // A held thumb keeps its pixel spot as the reply grows, which nudges scrollTop a few px.
        expect(Math.abs((await scrollTop(page)) - held), 'the view snapped back during the drag').toBeLessThan(20);
        await page.mouse.up();
        await page.waitForTimeout(1000);
        expect(Math.abs((await scrollTop(page)) - held), 'the view snapped back after the drag').toBeLessThan(20);
        expect(await distFromBottom(page)).toBeGreaterThan(100);
    });
}
