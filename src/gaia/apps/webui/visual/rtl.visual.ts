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

for (const locale of ['ar', 'en']) {
    test.describe(`first-run setup (${locale})`, () => {
        for (const s of SETUP_SCENES) {
            test(s.name, async ({ page }) => {
                await open(page, '', locale, (p) => withSetup(p, s.answers));
                await s.reach(page);
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
