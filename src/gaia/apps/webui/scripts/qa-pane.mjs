#!/usr/bin/env node
// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * QA kit for the browser pane. `launch` starts the built desktop app through
 * Playwright in dev QA mode (GAIA_BIN, a throwaway user data dir), serves the
 * fixture site on a loopback port, and listens for the other commands, which
 * talk to it through a token-gated loopback control port.
 *
 *   GAIA_BIN=<venv>/bin/gaia node scripts/qa-pane.mjs launch [--app <webui dir>]
 *   node scripts/qa-pane.mjs state | shortcut | open <url> | click <selector>
 *        | page-click <selector>   (inside the pane's visible page)
 *        | drag <selector> <dx>     (e.g. drag .bp-resize -400 widens the pane)
 *        | type <text> | key <key> | chat <text> | reply [seconds] | shot <file.png> | quit
 *   node scripts/qa-pane.mjs perf open-pane      (against the running app)
 *   GAIA_BIN=... node scripts/qa-pane.mjs perf launch [--app <webui dir>]
 *
 * QA_PANE_DIR overrides where the control file lives (default: a temp dir
 * keyed on the app dir, so lanes in separate worktrees never collide).
 */

import { _electron } from '@playwright/test';
import { createHash, randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..', '..', '..', '..');
const SITE = path.join(REPO, 'tests', 'fixtures', 'browser_pane', 'site');
const SLOW_MS = 3000;
const TOGGLE = 'ControlOrMeta+Shift+B';
const CHAT_READY = '.composer-input';

function fail(message) {
    console.error(`qa-pane: ${message}`);
    process.exit(1);
}

function parseArgs(argv) {
    const positional = [];
    const flags = {};
    for (let i = 0; i < argv.length; i += 1) {
        if (argv[i].startsWith('--')) flags[argv[i].slice(2)] = argv[(i += 1)];
        else positional.push(argv[i]);
    }
    return { positional, flags };
}

function controlFile(appDir) {
    const key = createHash('sha256').update(appDir).digest('hex').slice(0, 10);
    const dir = process.env.QA_PANE_DIR || path.join(os.tmpdir(), `gaia-qa-pane-${key}`);
    return path.join(dir, 'control.json');
}

// ── Fixture site ───────────────────────────────────────────────────────

const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript' };

function serveFixtures() {
    const server = http.createServer((req, res) => {
        const name = decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/^\/+/, '') || 'index.html';
        const file = path.join(SITE, name);
        if (!file.startsWith(SITE + path.sep) || !fs.existsSync(file)) {
            res.writeHead(404, { 'content-type': 'text/plain' }).end(`No fixture ${name}`);
            return;
        }
        const send = () => {
            res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
            fs.createReadStream(file).pipe(res);
        };
        if (name === 'slow.html') setTimeout(send, SLOW_MS);
        else send();
    });
    return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

// ── App ────────────────────────────────────────────────────────────────

async function launchApp(appDir) {
    const gaiaBin = process.env.GAIA_BIN;
    if (!gaiaBin) fail('GAIA_BIN is not set. The app only launches in dev QA mode, so it never touches the installed GAIA.');
    if (!fs.existsSync(gaiaBin)) fail(`GAIA_BIN points at ${gaiaBin}, which does not exist.`);
    if (!fs.existsSync(path.join(appDir, 'dist', 'index.html'))) fail(`No build in ${appDir}/dist. Run npm run build there first.`);
    // Without dev QA mode the app would ignore GAIA_BIN and install into the user's ~/.gaia.
    if (!fs.readFileSync(path.join(appDir, 'main.cjs'), 'utf8').includes('resolveDevQaMode(')) {
        fail(`${appDir}/main.cjs has no dev QA mode, so launching it would run the real installer. Add services/dev-qa-mode.cjs there first.`);
    }
    const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'gaia-qa-pane-userdata-'));
    const electron = createRequire(path.join(appDir, 'package.json'))('electron');
    const started = Date.now();
    const app = await _electron.launch({
        executablePath: electron,
        args: [appDir, `--user-data-dir=${userDataDir}`],
        cwd: appDir,
        env: { ...process.env, GAIA_BIN: gaiaBin },
        timeout: 120_000,
    });
    const page = await app.firstWindow({ timeout: 120_000 });
    await page.locator(CHAT_READY).first().waitFor({ state: 'visible', timeout: 120_000 });
    return { app, page, userDataDir, launchMs: Date.now() - started };
}

async function closeApp({ app, userDataDir }) {
    // A plain quit asks about running work in a dialog nobody here answers; an OS shutdown
    // quits without asking and still stops the backend. main listens for it once startup ends.
    const deadline = Date.now() + 60_000;
    while (!(await app.evaluate(({ powerMonitor }) => powerMonitor.listenerCount('shutdown') > 0))) {
        if (Date.now() > deadline) throw new Error('the app never finished starting, so it cannot quit without asking');
        await new Promise((r) => setTimeout(r, 200));
    }
    await app.evaluate(({ powerMonitor }) => powerMonitor.emit('shutdown'));
    await app.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
}

const paneState = (page) => page.evaluate(() => window.gaiaBrowserPane.getState());

async function openInPane(page, url) {
    const address = page.getByRole('textbox', { name: 'Address' });
    await address.fill(url);
    await address.press('Enter');
}

const FIXTURE_TITLE = 'Pane fixture home';

/** The URL is set as soon as main accepts the navigation; the title only once the page has parsed. */
async function waitForLoad(page, prefix) {
    await page.waitForFunction(async ([p, title]) => {
        const s = await window.gaiaBrowserPane.getState();
        const tab = s.tabs.find((t) => t.id === s.activeTabId);
        return Boolean(tab && tab.url && tab.url.startsWith(p) && tab.title === title && !tab.loading);
    }, [prefix, FIXTURE_TITLE], { polling: 10, timeout: 30_000 });
}

/** Time from the toggle shortcut to the fixture page's load, starting from a closed pane on a new tab. */
async function measureOpenPane(page, fixtureUrl, run) {
    if (await page.locator('.browser-pane').count()) await page.keyboard.press(TOGGLE);
    await page.evaluate(() => window.gaiaBrowserPane.openTab(null));
    const target = `${fixtureUrl}?run=${run}`;
    const started = Date.now();
    await page.keyboard.press(TOGGLE);
    await openInPane(page, target);
    await waitForLoad(page, target);
    return Date.now() - started;
}

async function windowShot(app, file) {
    if (process.platform !== 'darwin') fail('shot uses macOS screencapture; capture the window with the OS tool on this platform.');
    const b = await app.evaluate(({ BrowserWindow }) => {
        const win = BrowserWindow.getAllWindows().find((w) => w.isVisible());
        win.moveTop();
        return win.getBounds();
    });
    fs.mkdirSync(path.dirname(path.resolve(file)), { recursive: true });
    execFileSync('screencapture', ['-x', '-o', `-R${b.x},${b.y},${b.width},${b.height}`, path.resolve(file)]);
    return path.resolve(file);
}

// ── Commands run inside the launch process ─────────────────────────────

function commands(ctx) {
    const { app, page, fixtureUrl } = ctx;
    return {
        state: () => paneState(page),
        shortcut: async () => {
            await page.keyboard.press(TOGGLE);
            return { open: (await page.locator('.browser-pane').count()) > 0 };
        },
        open: async (url) => {
            await openInPane(page, url || fixtureUrl);
            return 'navigating';
        },
        click: async (selector) => {
            await page.locator(selector).first().click();
            return 'clicked';
        },
        'page-click': (selector) => app.evaluate(async ({ BrowserWindow, WebContentsView }, sel) => {
            const win = BrowserWindow.getAllWindows().find((w) => w.isVisible());
            const view = win.contentView.children.find((v) => v instanceof WebContentsView && v.getVisible());
            if (!view) throw new Error('No pane page is showing');
            return view.webContents.executeJavaScript(
                `(() => { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return 'no element'; el.click(); return 'clicked'; })()`,
                true,
            );
        }, selector),
        drag: async (selector, dx) => {
            const box = await page.locator(selector).first().boundingBox();
            if (!box) throw new Error(`${selector} is not on screen`);
            const x = box.x + box.width / 2;
            const y = box.y + box.height / 2;
            await page.mouse.move(x, y);
            await page.mouse.down();
            await page.mouse.move(x + Number(dx), y, { steps: 10 });
            await page.mouse.up();
            return 'dragged';
        },
        type: async (text) => {
            await page.keyboard.type(text);
            return 'typed';
        },
        key: async (key) => {
            await page.keyboard.press(key);
            return 'pressed';
        },
        chat: async (text) => {
            const box = page.locator(CHAT_READY).first();
            await box.fill(text);
            await box.press('Enter');
            return 'sent';
        },
        reply: async (seconds = '180') => {
            const stop = page.locator('[aria-label="Stop"]');
            const prompt = page.locator('.perm-card');
            const deadline = Date.now() + Number(seconds) * 1000;
            await stop.first().waitFor({ timeout: 10000 }).catch(() => {});
            while (Date.now() < deadline) {
                if (await prompt.count()) return { waiting: 'approval', prompt: (await prompt.first().innerText()).slice(0, 600) };
                if (!(await stop.count())) {
                    const last = page.locator('[data-role="assistant"]').last();
                    return { reply: (await last.innerText()).slice(0, 2000) };
                }
                await page.waitForTimeout(500);
            }
            throw new Error(`No reply within ${seconds} s`);
        },
        shot: (file) => windowShot(app, file),
        'perf-open-pane': async () => {
            ctx.runs = (ctx.runs || 0) + 1;
            return { ms: await measureOpenPane(page, fixtureUrl, ctx.runs) };
        },
        quit: async () => {
            setTimeout(() => ctx.shutdown(), 50);
            return 'quitting';
        },
    };
}

async function launch(flags) {
    const appDir = path.resolve(flags.app || path.resolve(HERE, '..'));
    const site = await serveFixtures();
    const fixtureUrl = `http://127.0.0.1:${site.address().port}/index.html`;
    const launched = await launchApp(appDir);
    const ctx = { ...launched, fixtureUrl };
    const handlers = commands(ctx);
    const token = randomBytes(32).toString('hex');
    const file = controlFile(appDir);

    const control = http.createServer((req, res) => {
        let body = '';
        req.on('data', (c) => { body += c; });
        req.on('end', async () => {
            if (req.headers.authorization !== `Bearer ${token}`) {
                res.writeHead(401).end('{"error":"bad token"}');
                return;
            }
            try {
                const { cmd, args } = JSON.parse(body);
                const handler = handlers[cmd];
                if (!handler) throw new Error(`unknown command ${cmd}`);
                const result = await handler(...(args || []));
                res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ result }));
            } catch (err) {
                res.writeHead(500, { 'content-type': 'application/json' }).end(JSON.stringify({ error: err.message }));
            }
        });
    });
    await new Promise((resolve) => control.listen(0, '127.0.0.1', resolve));
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify({ port: control.address().port, token, pid: process.pid, fixtureUrl }), { mode: 0o600 });

    let stopping = false;
    ctx.shutdown = async () => {
        if (stopping) return;
        stopping = true;
        fs.rmSync(path.dirname(file), { recursive: true, force: true });
        control.close();
        site.close();
        await closeApp(launched).catch((err) => console.error(`qa-pane: closing the app failed: ${err.message}`));
        process.exit(0);
    };
    process.on('SIGINT', ctx.shutdown);
    process.on('SIGTERM', ctx.shutdown);
    launched.app.on('close', () => {
        if (!stopping) {
            console.error('qa-pane: the app exited');
            ctx.shutdown();
        }
    });
    console.log(`qa-pane ready ${fixtureUrl}`);
}

async function send(cmd, args) {
    const file = controlFile(path.resolve(HERE, '..'));
    if (!fs.existsSync(file)) fail(`No running app (no ${file}). Start one with: GAIA_BIN=... node scripts/qa-pane.mjs launch`);
    const { port, token } = JSON.parse(fs.readFileSync(file, 'utf8'));
    const res = await fetch(`http://127.0.0.1:${port}/`, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
        body: JSON.stringify({ cmd, args }),
    });
    const out = await res.json();
    if (!res.ok) fail(out.error);
    return out.result;
}

async function main() {
    const { positional, flags } = parseArgs(process.argv.slice(2));
    const [cmd, ...rest] = positional;
    if (cmd === 'launch') return launch(flags);
    if (cmd === 'perf' && rest[0] === 'launch') {
        const launched = await launchApp(path.resolve(flags.app || path.resolve(HERE, '..')));
        await closeApp(launched);
        console.log(`perf launch ${launched.launchMs} ms`);
        return;
    }
    if (cmd === 'perf' && rest[0] === 'open-pane') {
        const { ms } = await send('perf-open-pane', []);
        console.log(`perf open-pane ${ms} ms`);
        return;
    }
    if (!cmd) fail('give a command: launch, state, shortcut, open, click, type, key, chat, shot, perf, quit');
    const result = await send(cmd, rest);
    console.log(typeof result === 'string' ? result : JSON.stringify(result, null, 2));
}

main().catch((err) => fail(err.stack || err.message));
