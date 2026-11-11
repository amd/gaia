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
 *        | page-click <selector>   (inside the pane's visible page, by script)
 *        | user-click <selector>   (real mouse input on it, as the user's own click)
 *        | session                 (the bridge discovery file and the chat the window shows)
 *        | drag <selector> <dx>     (e.g. drag .bp-resize -400 widens the pane)
 *        | type <text> | key <key> | chat <text> | reply [seconds] | shot <file.png> | quit
 *        | pick <selector> [shift]  (real mouse input on it while Select element runs)
 *        | page-key <key>           (a real key press in the pane's page, e.g. ArrowUp, Escape)
 *        | chips                    (the composer's picked-element chips)
 *        | attach <file>            (attaches a file to the composer)
 *   node scripts/qa-pane.mjs perf open-pane      (against the running app)
 *   node scripts/qa-pane.mjs perf pick [runs]    (pick click to chip, against the running app)
 *   node scripts/qa-pane.mjs perf send [runs] [prompt] [settle s]
 *                                       (send to first reply token in a new chat, with and without one chip,
 *                                        interleaved; waits settle seconds for the new chat's warm-up first)
 *        | crash                    (SIGKILL this kit's Electron process, as a crash would)
 *   GAIA_BIN=... node scripts/qa-pane.mjs perf launch [--app <webui dir>]
 *   GAIA_BIN=... node scripts/qa-pane.mjs perf quit [runs]
 *                                       (quit time with the fixture dev server running and with none, interleaved)
 *
 * launch --user-data <dir> reuses that user data dir and keeps it on quit, so a
 * relaunch sees what the last run left (the dev-server ledger, for one).
 *
 * QA_PANE_DIR overrides where the control file lives (default: a temp dir
 * keyed on the app dir, so lanes in separate worktrees never collide).
 */

import { _electron } from '@playwright/test';
import { createHash, randomBytes } from 'node:crypto';
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
const PICK = 'ControlOrMeta+Shift+S';
const NEW_CHAT = 'ControlOrMeta+Shift+O';
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

let reactApp = null;

/** The React dev page's script: react-app.src.js bundled with React's development build, so component names stay readable. */
function bundleReactApp() {
    if (!reactApp) {
        reactApp = (async () => {
            const req = createRequire(path.join(HERE, '..', 'package.json'));
            const { build } = await import(req.resolve('rolldown'));
            const out = await build({
                input: path.join(SITE, 'react-app.src.js'),
                write: false,
                resolve: { alias: { react: path.dirname(req.resolve('react/package.json')), 'react-dom': path.dirname(req.resolve('react-dom/package.json')) } },
                transform: { define: { 'process.env.NODE_ENV': '"development"' } },
                output: { format: 'iife' },
                logLevel: 'warn',
            });
            return out.output[0].code;
        })();
    }
    return reactApp;
}

function serveFixtures() {
    const server = http.createServer((req, res) => {
        const name = decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/^\/+/, '') || 'index.html';
        if (name === 'react-app.js') {
            bundleReactApp()
                .then((code) => res.writeHead(200, { 'content-type': TYPES['.js'], 'cache-control': 'no-store' }).end(code))
                .catch((err) => res.writeHead(500, { 'content-type': 'text/plain' }).end(`Could not build the React fixture: ${err.message}`));
            return;
        }
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

async function launchApp(appDir, keepUserData = null) {
    const gaiaBin = process.env.GAIA_BIN;
    if (!gaiaBin) fail('GAIA_BIN is not set. The app only launches in dev QA mode, so it never touches the installed GAIA.');
    if (!fs.existsSync(gaiaBin)) fail(`GAIA_BIN points at ${gaiaBin}, which does not exist.`);
    if (!fs.existsSync(path.join(appDir, 'dist', 'index.html'))) fail(`No build in ${appDir}/dist. Run npm run build there first.`);
    // Without dev QA mode the app would ignore GAIA_BIN and install into the user's ~/.gaia.
    if (!fs.readFileSync(path.join(appDir, 'main.cjs'), 'utf8').includes('resolveDevQaMode(')) {
        fail(`${appDir}/main.cjs has no dev QA mode, so launching it would run the real installer. Add services/dev-qa-mode.cjs there first.`);
    }
    const userDataDir = keepUserData ? path.resolve(keepUserData) : fs.mkdtempSync(path.join(os.tmpdir(), 'gaia-qa-pane-userdata-'));
    fs.mkdirSync(userDataDir, { recursive: true });
    const electron = createRequire(path.join(appDir, 'package.json'))('electron');
    const started = Date.now();
    const app = await _electron.launch({
        executablePath: electron,
        args: [appDir, `--user-data-dir=${userDataDir}`],
        cwd: appDir,
        env: { ...process.env, GAIA_BIN: gaiaBin },
        timeout: 120_000,
    });
    // The renderer names its chat to main on every switch; remembered here for `session`.
    await app.evaluate(({ ipcMain }) => {
        ipcMain.prependListener('pane:set-view', (_event, chat) => { globalThis.qaPaneSession = chat; });
        ipcMain.prependListener('pane:claim-draft', (_event, chat) => { globalThis.qaPaneSession = chat; });
    });
    const page = await app.firstWindow({ timeout: 120_000 });
    await page.locator(CHAT_READY).first().waitFor({ state: 'visible', timeout: 120_000 });
    return { app, page, userDataDir, keepUserData: Boolean(keepUserData), launchMs: Date.now() - started };
}

/** Quit as closeApp does, and return how long the app took from the quit to its exit (ms). */
async function closeApp({ app, userDataDir, keepUserData }) {
    // A plain quit asks about running work in a dialog nobody here answers; an OS shutdown
    // quits without asking and still stops the backend. main listens for it once startup ends.
    const deadline = Date.now() + 60_000;
    while (!(await app.evaluate(({ powerMonitor }) => powerMonitor.listenerCount('shutdown') > 0))) {
        if (Date.now() > deadline) throw new Error('the app never finished starting, so it cannot quit without asking');
        await new Promise((r) => setTimeout(r, 200));
    }
    await app.evaluate(({ powerMonitor }) => powerMonitor.emit('shutdown'));
    const exited = new Promise((resolve) => app.process().once('exit', resolve));
    const started = Date.now();
    await app.close();
    await exited;
    const quitMs = Date.now() - started;
    if (!keepUserData) fs.rmSync(userDataDir, { recursive: true, force: true });
    return quitMs;
}

/** One call to the app's own bridge, as the agent makes it. */
async function bridgeCall(app, userDataDir, op, body) {
    const disc = JSON.parse(fs.readFileSync(path.join(userDataDir, 'run', 'browser-pane.json'), 'utf8'));
    const sessionId = await app.evaluate(() => globalThis.qaPaneSession ?? null);
    const res = await fetch(`http://127.0.0.1:${disc.port}/v1/${op}`, {
        method: 'POST',
        headers: { authorization: `Bearer ${disc.token}`, 'content-type': 'application/json' },
        body: JSON.stringify({ sessionId, ...body }),
    });
    const out = await res.json();
    if (!res.ok) throw new Error(`${op}: ${out.error.code}: ${out.error.message}`);
    return out;
}

const DEVSERVER_APP = path.join(REPO, 'tests', 'fixtures', 'browser_pane', 'devserver');

/** The fixture's "web" server, started through main's supervisor the way dev_server does. */
async function startFixtureServer(app, userDataDir) {
    const port = 41730;
    const spec = { name: 'web', argv: ['node', 'server.js', '--port', String(port)], cwd: DEVSERVER_APP, port, env: {}, url: `http://localhost:${port}/`, readyTimeoutMs: 20000 };
    // A fresh profile shows no chat yet, so name one the way the renderer does.
    await app.evaluate(({ BrowserWindow, ipcMain }) => {
        if (globalThis.qaPaneSession) return;
        const win = BrowserWindow.getAllWindows().find((w) => w.isVisible());
        ipcMain.emit('pane:set-view', { sender: win.webContents }, 'qa-perf-quit', false);
    });
    return bridgeCall(app, userDataDir, 'devServers', { op: 'ensure', key: 'f1f1f1f1f1f1f1f1', spec });
}

async function measureQuit(appDir, withServer) {
    const launched = await launchApp(appDir);
    let pid = null;
    if (withServer) pid = (await startFixtureServer(launched.app, launched.userDataDir)).server.pid;
    const quitMs = await closeApp(launched);
    const survived = pid !== null && (() => {
        try {
            process.kill(pid, 0);
            return true;
        } catch {
            return false;
        }
    })();
    return { withServer, quitMs, serverSurvived: survived };
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

// Drawn from the app itself: a screen capture would also grab any window on top of it.
async function windowShot(app, file) {
    fs.mkdirSync(path.dirname(path.resolve(file)), { recursive: true });
    await compositeShot(app, file);
    return path.resolve(file);
}

/** The window's own page with each visible pane view painted over it at its bounds. */
async function compositeShot(app, file) {
    const parts = await app.evaluate(async ({ BrowserWindow, WebContentsView }) => {
        const win = BrowserWindow.getAllWindows().find((w) => w.isVisible());
        const views = [];
        for (const v of win.contentView.children) {
            if (!(v instanceof WebContentsView) || !v.getVisible()) continue;
            views.push({ bounds: v.getBounds(), data: (await v.webContents.capturePage()).toDataURL() });
        }
        const base = (await win.webContents.capturePage()).toDataURL();
        return { base, views, size: win.getContentBounds() };
    });
    const page = (await app.windows())[0];
    const png = await page.evaluate(async ({ base, views, size }) => {
        const load = (src) => new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = () => reject(new Error('could not decode a capture'));
            img.src = src;
        });
        const b = await load(base);
        const scale = b.width / size.width;
        const canvas = document.createElement('canvas');
        canvas.width = b.width;
        canvas.height = b.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(b, 0, 0);
        for (const v of views) ctx.drawImage(await load(v.data), v.bounds.x * scale, v.bounds.y * scale, v.bounds.width * scale, v.bounds.height * scale);
        return canvas.toDataURL('image/png').split(',')[1];
    }, parts);
    fs.writeFileSync(path.resolve(file), Buffer.from(png, 'base64'));
}

/** Real input in the pane's visible page: what a user's own mouse or keyboard sends. */
function paneInput(app, kind, arg, flags = {}) {
    return app.evaluate(async ({ BrowserWindow, WebContentsView }, [k, a, f]) => {
        const win = BrowserWindow.getAllWindows().find((w) => w.isVisible());
        const view = win.contentView.children.find((v) => v instanceof WebContentsView && v.getVisible());
        if (!view) throw new Error('No pane page is showing');
        const wc = view.webContents;
        if (k === 'key') {
            const keyCode = { ArrowUp: 'Up', ArrowDown: 'Down', Escape: 'Escape', Enter: 'Return' }[a] || a;
            wc.focus();
            wc.sendInputEvent({ type: 'keyDown', keyCode });
            wc.sendInputEvent({ type: 'keyUp', keyCode });
            return { at: Date.now() };
        }
        const r = await wc.executeJavaScript(
            `(() => { const el = document.querySelector(${JSON.stringify(a)}); if (!el) return null; el.scrollIntoView({ block: 'center' }); const b = el.getBoundingClientRect(); return { x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) }; })()`,
            true,
        );
        if (!r) return { error: `no element ${a}` };
        const modifiers = f.shift ? ['shift'] : [];
        wc.sendInputEvent({ type: 'mouseMove', x: r.x, y: r.y, modifiers });
        const at = Date.now();
        wc.sendInputEvent({ type: 'mouseDown', x: r.x, y: r.y, button: 'left', clickCount: 1, modifiers });
        wc.sendInputEvent({ type: 'mouseUp', x: r.x, y: r.y, button: 'left', clickCount: 1, modifiers });
        return { at };
    }, [kind, arg, flags]);
}

const readChips = (page) => page.evaluate(() => [...document.querySelectorAll('.element-chips:not(.is-sent) .element-chip')]
    .map((li) => ({ ref: li.dataset.ref, text: li.innerText.replace(/\s+/g, ' ').trim() })));

function median(xs) {
    const s = [...xs].sort((a, b) => a - b);
    return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
}

async function setPicking(page, on) {
    const s = await paneState(page);
    if (s.picking !== on) await page.evaluate(() => window.gaiaBrowserPane.togglePicking());
    await page.waitForFunction(async (want) => (await window.gaiaBrowserPane.getState()).picking === want, on, { polling: 10, timeout: 5000 });
}

async function clearChips(page) {
    for (const button of await page.locator('.element-chips:not(.is-sent) [aria-label^="Remove"]').all()) await button.click();
}

/** Time from a real click on the Save button to its chip in the composer, each pick in a fresh picking run. */
async function measurePick(ctx) {
    const { app, page } = ctx;
    await clearChips(page);
    await setPicking(page, false);
    await setPicking(page, true);
    await page.evaluate(() => {
        window.__chipAt = 0;
        const list = () => document.querySelectorAll('.element-chips:not(.is-sent) .element-chip').length;
        const obs = new MutationObserver(() => {
            if (list() > 0 && !window.__chipAt) {
                window.__chipAt = Date.now();
                obs.disconnect();
            }
        });
        obs.observe(document.body, { childList: true, subtree: true });
    });
    const { at, error } = await paneInput(app, 'click', 'button.primary');
    if (error) throw new Error(error);
    await page.waitForFunction(() => window.__chipAt > 0, null, { polling: 5, timeout: 10_000 });
    const ms = (await page.evaluate(() => window.__chipAt)) - at;
    await page.waitForFunction(() => !document.querySelector('.element-chips:not(.is-sent)')?.innerText.includes('Uploading'), null, { timeout: 10_000 });
    await setPicking(page, false);
    return ms;
}

/** Send to the first reply token in a new chat, and the prompt tokens the model reported. */
async function measureSend(ctx, withChip, prompt, settleMs) {
    const { app, page } = ctx;
    await page.keyboard.press(NEW_CHAT);
    await page.locator(CHAT_READY).first().waitFor({ state: 'visible' });
    // A new chat warms the model with the system prompt (about 22 s here); a send during it waits for it.
    await page.waitForTimeout(settleMs);
    await clearChips(page);
    if (withChip) {
        await setPicking(page, true);
        await paneInput(app, 'click', 'button.primary');
        await page.waitForFunction(() => {
            const chips = document.querySelector('.element-chips:not(.is-sent)');
            return chips && chips.querySelector('.element-chip') && !chips.innerText.includes('Uploading');
        }, null, { timeout: 10_000 });
        await setPicking(page, false);
    }
    const box = page.locator(CHAT_READY).first();
    await box.fill(prompt);
    await page.evaluate(() => {
        window.__firstTokenAt = 0;
        const obs = new MutationObserver(() => {
            const text = [...document.querySelectorAll('.msg-assistant .md-content')].map((n) => n.innerText).join('').trim();
            if (text && !window.__firstTokenAt) {
                window.__firstTokenAt = performance.now();
                obs.disconnect();
            }
        });
        obs.observe(document.body, { childList: true, subtree: true, characterData: true });
        window.__sentAt = performance.now();
    });
    await box.press('Enter');
    await page.waitForFunction(() => window.__firstTokenAt > 0, null, { polling: 10, timeout: 300_000 });
    const ms = await page.evaluate(() => window.__firstTokenAt - window.__sentAt);
    await page.locator('[aria-label="Stop"]').waitFor({ state: 'detached', timeout: 300_000 });
    const sid = await app.evaluate(() => globalThis.qaPaneSession ?? null);
    const msgs = await page.evaluate(async (id) => {
        // A file:// window names its backend in ?api=, as utils/apiBase.ts reads it.
        const api = location.protocol === 'file:' ? new URLSearchParams(location.search).get('api') : '/api';
        return (await (await fetch(`${api}/sessions/${id}/messages`, { headers: { 'x-gaia-ui': '1' } })).json()).messages;
    }, sid);
    const answer = [...msgs].reverse().find((m) => m.role === 'assistant');
    return { ms: Math.round(ms), inputTokens: answer && answer.stats ? answer.stats.input_tokens : null, sid };
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
        'user-click': (selector) => app.evaluate(async ({ BrowserWindow, WebContentsView }, sel) => {
            const win = BrowserWindow.getAllWindows().find((w) => w.isVisible());
            const view = win.contentView.children.find((v) => v instanceof WebContentsView && v.getVisible());
            if (!view) throw new Error('No pane page is showing');
            const r = await view.webContents.executeJavaScript(
                `(() => { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return null; const b = el.getBoundingClientRect(); return { x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) }; })()`,
                true,
            );
            if (!r) return 'no element';
            view.webContents.sendInputEvent({ type: 'mouseDown', x: r.x, y: r.y, button: 'left', clickCount: 1 });
            view.webContents.sendInputEvent({ type: 'mouseUp', x: r.x, y: r.y, button: 'left', clickCount: 1 });
            return 'clicked';
        }, selector),
        session: async () => ({
            sessionId: await app.evaluate(() => globalThis.qaPaneSession ?? null),
            discovery: path.join(ctx.userDataDir, 'run', 'browser-pane.json'),
            userDataDir: ctx.userDataDir,
            electronPid: app.process().pid,
        }),
        crash: async () => {
            const pid = app.process().pid;
            setTimeout(() => process.kill(pid, 'SIGKILL'), 50);
            return { killed: pid };
        },
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
            ctx.repliesBefore = await page.locator('[data-role="assistant"]').count();
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
                const fresh = (await page.locator('[data-role="assistant"]').count()) > (ctx.repliesBefore ?? -1);
                if (fresh && !(await stop.count())) {
                    const last = page.locator('[data-role="assistant"]').last();
                    return { reply: (await last.innerText()).slice(0, 2000) };
                }
                await page.waitForTimeout(500);
            }
            throw new Error(`No reply within ${seconds} s`);
        },
        shot: (file) => windowShot(app, file),
        pick: async (selector, shift) => {
            const res = await paneInput(app, 'click', selector, { shift: shift === 'shift' });
            if (res.error) throw new Error(res.error);
            return 'picked';
        },
        'page-key': async (key) => {
            await paneInput(app, 'key', key);
            return 'pressed';
        },
        chips: () => readChips(page),
        attach: async (file) => {
            await page.locator('.composer input[type="file"]').first().setInputFiles(path.resolve(file));
            return 'attached';
        },
        'perf-pick': async (runs = '20') => {
            const ms = [];
            for (let i = 0; i < Number(runs); i += 1) ms.push(await measurePick(ctx));
            await clearChips(page);
            return { runs: ms, median: median(ms) };
        },
        'perf-send': async (runs = '5', prompt = 'Reply with one short sentence: what is on this settings page?', settle = '35') => {
            const rows = [];
            for (let i = 0; i < Number(runs); i += 1) {
                rows.push({ chip: false, ...(await measureSend(ctx, false, prompt, Number(settle) * 1000)) });
                rows.push({ chip: true, ...(await measureSend(ctx, true, prompt, Number(settle) * 1000)) });
            }
            const pick = (c) => rows.filter((r) => r.chip === c);
            return {
                rows,
                medianNoChip: median(pick(false).map((r) => r.ms)),
                medianOneChip: median(pick(true).map((r) => r.ms)),
                medianTokensNoChip: median(pick(false).map((r) => r.inputTokens ?? 0)),
                medianTokensOneChip: median(pick(true).map((r) => r.inputTokens ?? 0)),
            };
        },
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
    const launched = await launchApp(appDir, flags['user-data'] || null);
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
        if (launched.app.process().exitCode === null && launched.app.process().signalCode === null) {
            await closeApp(launched).catch((err) => console.error(`qa-pane: closing the app failed: ${err.message}`));
        }
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
    if (cmd === 'perf' && rest[0] === 'quit') {
        const appDir = path.resolve(flags.app || path.resolve(HERE, '..'));
        const rows = [];
        for (let i = 0; i < Number(rest[1] || 5); i += 1) {
            rows.push(await measureQuit(appDir, false));
            rows.push(await measureQuit(appDir, true));
        }
        const pick = (w) => rows.filter((r) => r.withServer === w).map((r) => r.quitMs);
        console.log(JSON.stringify({ rows, medianNoServer: median(pick(false)), medianOneServer: median(pick(true)) }, null, 2));
        return;
    }
    if (cmd === 'perf' && (rest[0] === 'pick' || rest[0] === 'send')) {
        const out = await send(`perf-${rest[0]}`, rest.slice(1));
        console.log(JSON.stringify(out, null, 2));
        return;
    }
    if (cmd === 'perf' && rest[0] === 'open-pane') {
        const { ms } = await send('perf-open-pane', []);
        console.log(`perf open-pane ${ms} ms`);
        return;
    }
    if (!cmd) fail('give a command: launch, state, session, crash, shortcut, open, click, page-click, user-click, pick, page-key, chips, attach, drag, type, key, chat, reply, shot, perf, quit');
    const result = await send(cmd, rest);
    console.log(typeof result === 'string' ? result : JSON.stringify(result, null, 2));
}

main().catch((err) => fail(err.stack || err.message));
