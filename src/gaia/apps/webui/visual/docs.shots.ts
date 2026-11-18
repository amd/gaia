// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** The user guide's screenshots, written to docs/images/agent-ui. Fixture data only. */

import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test, type Page } from '@playwright/test';

import { ANSWER_SESSION, NOW, mockApi, useLocale, useTheme } from './fixtures';
import { SETUP_SCENES, withSetup } from './setup-scenes';

const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../../docs/images/agent-ui');

let unhandled: string[] = [];

async function open(page: Page, hash = ''): Promise<void> {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.clock.setFixedTime(NOW);
    unhandled = await mockApi(page);
    await useTheme(page, 'light');
    await page.goto(`/${hash}`);
    await page.evaluate(() => document.fonts.ready);
}

const shot = (page: Page, name: string) => page.screenshot({ path: path.join(OUT, `${name}.png`), animations: 'disabled', caret: 'hide' });

test.afterEach(() => {
    expect(unhandled, 'add a fixture to visual/fixtures.ts ROUTES for each call').toEqual([]);
});

test('new chat', async ({ page }) => {
    await open(page);
    await expect(page.getByPlaceholder('Ask GAIA anything')).toBeVisible();
    await shot(page, 'new-chat');
});

test('answer', async ({ page }) => {
    await open(page, `#${ANSWER_SESSION}`);
    await expect(page.getByText('Action items from Aug 28')).toBeVisible();
    await shot(page, 'answer');
});

test('documents', async ({ page }) => {
    await open(page);
    await page.getByRole('button', { name: 'Documents' }).click();
    await expect(page.getByText('q3-board.pdf')).toBeVisible();
    await shot(page, 'documents');
});

test('scheduled tasks', async ({ page }) => {
    await open(page);
    await page.getByRole('button', { name: 'Scheduled tasks' }).click();
    await expect(page.getByText('Morning brief')).toBeVisible();
    await shot(page, 'scheduled-tasks');
});

test('settings', async ({ page }) => {
    await open(page);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await shot(page, 'settings');
});

// Setup's screens, in the order a new user meets them.
const SETUP_SHOTS: Record<string, string> = {
    'check-found': 'setup-check',
    plan: 'setup-plan',
    'download-running': 'setup-download',
    'test-running': 'setup-test',
};

for (const scene of SETUP_SCENES.filter((s) => s.name in SETUP_SHOTS)) {
    test(`setup, ${scene.name}`, async ({ page }) => {
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await page.clock.setFixedTime(NOW);
        unhandled = await mockApi(page);
        await withSetup(page, scene.answers);
        await useTheme(page, 'light');
        await useLocale(page, 'en');
        await page.goto('/');
        await page.evaluate(() => document.fonts.ready);
        await scene.reach(page);
        await shot(page, SETUP_SHOTS[scene.name]);
    });
}
