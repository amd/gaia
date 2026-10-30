// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** The Agent UI's key screens, one baseline per screen per theme. */

import { expect, test, type Page } from '@playwright/test';

import { ANSWER_SESSION, NOW, mockApi, useTheme } from './fixtures';

let unhandled: string[] = [];

async function open(page: Page, theme: 'light' | 'dark', hash = ''): Promise<void> {
    await page.clock.setFixedTime(NOW);
    unhandled = await mockApi(page);
    await useTheme(page, theme);
    await page.goto(`/${hash}`);
    await page.evaluate(() => document.fonts.ready);
}

test.afterEach(() => {
    expect(unhandled, 'add a fixture to visual/fixtures.ts ROUTES for each call').toEqual([]);
});

const themeOf = (name: string) => (name === 'dark' ? 'dark' : 'light');

test('new chat', async ({ page }, info) => {
    // The backdrop animates; reduced motion holds it to one still frame.
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await open(page, themeOf(info.project.name));
    await expect(page.getByPlaceholder('Ask GAIA anything')).toBeVisible();
    await expect(page).toHaveScreenshot('new-chat.png');
});

test('finished answer', async ({ page }, info) => {
    await open(page, themeOf(info.project.name), `#${ANSWER_SESSION}`);
    await expect(page.getByText('Action items from Aug 28')).toBeVisible();
    await expect(page).toHaveScreenshot('finished-answer.png');
});

test('settings, general', async ({ page }, info) => {
    await open(page, themeOf(info.project.name));
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page).toHaveScreenshot('settings-general.png');
});

test('scheduled tasks', async ({ page }, info) => {
    await open(page, themeOf(info.project.name));
    await page.getByRole('button', { name: 'Scheduled tasks' }).click();
    await expect(page.getByText('Morning brief')).toBeVisible();
    await expect(page).toHaveScreenshot('scheduled-tasks.png');
});

test('documents', async ({ page }, info) => {
    await open(page, themeOf(info.project.name));
    await page.getByRole('button', { name: 'Documents' }).click();
    await expect(page.getByText('q3-board.pdf')).toBeVisible();
    await expect(page).toHaveScreenshot('documents.png');
});

test('phone width', async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page, themeOf(info.project.name), `#${ANSWER_SESSION}`);
    await expect(page.getByText('Action items from Aug 28')).toBeVisible();
    await expect(page).toHaveScreenshot('phone-answer.png');
});
