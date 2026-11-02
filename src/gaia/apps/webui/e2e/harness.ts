// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * The world one setup journey runs in: an empty home, this checkout's backend,
 * and installer/headless/fake_model_server.py playing a Lemonade with nothing
 * downloaded. Everything lives in a temp folder and on ports the OS picked, so
 * a run never touches a real GAIA home or a Lemonade on this machine.
 */

import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { createWriteStream, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, type WriteStream } from 'node:fs';
import { createServer, type Server } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test as base, type Page } from '@playwright/test';

import { translate, type MessageKey, type Params } from '../src/i18n/registry';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, '..', '..', '..', '..', '..');
const FAKE_SERVER = join(REPO, 'installer', 'headless', 'fake_model_server.py');
const PYTHON = process.env.GAIA_E2E_PYTHON || 'python';
const UI_DIST = process.env.GAIA_E2E_UI_DIST || resolve(HERE, '..', 'dist');
const SCREENS = process.env.GAIA_E2E_SCREENS || join(HERE, 'test-results', 'screens');
const START_TIMEOUT_MS = 120_000;

export const CHAT_MODEL = 'Gemma-4-E4B-it-GGUF';
export const EMBEDDER = 'user.embeddinggemma-300m-GGUF';
export const UNTESTED_CHAT_MODEL = 'user.ggml-gpt-oss-20b-MXFP4-GGUF';

export type Lang = 'en' | 'ar';
export type Say = (key: MessageKey, params?: Params) => string;

export interface JourneyOptions {
    lang: Lang;
}

/** The app's own words, so a run in Arabic fails on a screen left in English. */
export function sayer(lang: Lang): Say {
    return (key, params) => translate(lang, key, params);
}

export interface FakeOptions {
    port?: number;
    /** Ids already downloaded when it starts. */
    models?: string[];
    /** `FAKE_LEMONADE_*` settings: see fake_model_server.py. */
    env?: Record<string, string>;
}

interface FakeRequest {
    method: string;
    path: string;
    status: number;
    model: string | null;
    body_keys: string[];
}

async function freePort(): Promise<number> {
    return new Promise((done, fail) => {
        const probe = createServer();
        probe.once('error', fail);
        probe.listen(0, '127.0.0.1', () => {
            const address = probe.address();
            const port = typeof address === 'object' && address ? address.port : 0;
            probe.close(() => done(port));
        });
    });
}

function tail(path: string, lines = 40): string {
    if (!existsSync(path)) return `(${path} was never written)`;
    return readFileSync(path, 'utf-8').split(/\r?\n/).slice(-lines).join('\n');
}

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

/** One child with its log; killed with everything it started. */
class Service {
    private readonly log: WriteStream;
    private readonly child: ChildProcess;

    constructor(readonly name: string, readonly logPath: string, argv: string[], env: NodeJS.ProcessEnv, cwd: string) {
        this.log = createWriteStream(logPath, { flags: 'a' });
        // Its own process group on POSIX, so stop() reaches what it started.
        this.child = spawn(argv[0], argv.slice(1), { env, cwd, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true, detached: process.platform !== 'win32' });
        this.child.stdout?.pipe(this.log);
        this.child.stderr?.pipe(this.log);
    }

    get exited(): boolean {
        return this.child.exitCode !== null || this.child.signalCode !== null;
    }

    async waitUntilServing(url: string): Promise<void> {
        const deadline = Date.now() + START_TIMEOUT_MS;
        while (Date.now() < deadline) {
            // Checked first: another process on this port must not pass for ours.
            if (this.exited) throw new Error(`${this.name} exited:\n${tail(this.logPath)}`);
            try {
                if ((await fetch(url, { signal: AbortSignal.timeout(3_000) })).ok) return;
            } catch {
                // Not listening yet; the deadline below names the failure.
            }
            await sleep(250);
        }
        throw new Error(`${this.name} did not answer ${url} within ${START_TIMEOUT_MS / 1000} s:\n${tail(this.logPath)}`);
    }

    async stop(): Promise<void> {
        const pid = this.child.pid;
        if (!this.exited && pid) {
            // A setup run's `gaia init` must go too, or it outlives the test.
            if (process.platform === 'win32') spawnSync('taskkill', ['/T', '/F', '/PID', String(pid)], { stdio: 'ignore' });
            else process.kill(-pid, 'SIGKILL');
            const deadline = Date.now() + 10_000;
            while (!this.exited && Date.now() < deadline) await sleep(50);
            if (!this.exited) throw new Error(`${this.name} (pid ${pid}) survived being killed`);
        }
        await new Promise<void>((done) => this.log.end(done));
    }
}

export class Journey {
    readonly root = mkdtempSync(join(tmpdir(), 'gaia-setup-journey-'));
    readonly home = join(this.root, 'home');
    readonly logs = join(this.root, 'logs');
    private readonly requestLog = join(this.logs, 'fake-requests.jsonl');
    private backend: Service | null = null;
    private fake: Service | null = null;
    private backendPort = 0;
    private lemonadePort = 0;
    private readonly listeners: Server[] = [];

    constructor() {
        mkdirSync(this.home);
        mkdirSync(this.logs);
    }

    get url(): string {
        return `http://127.0.0.1:${this.backendPort}`;
    }

    get lemonadeUrl(): string {
        return `http://127.0.0.1:${this.lemonadePort}/api/v1`;
    }

    private env(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
        const env: NodeJS.ProcessEnv = { ...process.env };
        // Nothing of this machine's GAIA, Lemonade or proxy may reach the journey.
        for (const name of Object.keys(env)) {
            if (/^(GAIA_|LEMONADE_|PYTHONPATH$|VIRTUAL_ENV_DISABLE)/.test(name) || /^(all|http|https)_proxy$/i.test(name)) delete env[name];
        }
        return {
            ...env,
            HOME: this.home,
            USERPROFILE: this.home,
            APPDATA: join(this.home, 'AppData', 'Roaming'),
            LOCALAPPDATA: join(this.home, 'AppData', 'Local'),
            XDG_CONFIG_HOME: join(this.home, '.config'),
            XDG_CACHE_HOME: join(this.home, '.cache'),
            GAIA_HOME: join(this.home, '.gaia'),
            HF_HOME: join(this.home, '.cache', 'huggingface'),
            PYTHONUTF8: '1',
            PYTHONIOENCODING: 'utf-8',
            ...(process.env.GAIA_E2E_PYTHONPATH ? { PYTHONPATH: process.env.GAIA_E2E_PYTHONPATH } : {}),
            ...extra,
        };
    }

    /** A port nothing answers on until {@link startFake} or {@link listen} takes it. */
    closedPort(): Promise<number> {
        return freePort();
    }

    /** Answer TCP connections on *port*, which is all the gateway check asks of a host. */
    async listen(port: number): Promise<void> {
        const server = createServer((socket) => socket.end());
        await new Promise<void>((done) => server.listen(port, '127.0.0.1', done));
        this.listeners.push(server);
    }

    async startFake({ port, models = [], env = {} }: FakeOptions = {}): Promise<void> {
        this.lemonadePort = port ?? (await freePort());
        const argv = [PYTHON, FAKE_SERVER, '--catalog', '--port', String(this.lemonadePort), '--log', this.requestLog];
        for (const model of models) argv.push('--model', model);
        this.fake = new Service('the fake Lemonade', join(this.logs, 'fake.log'), argv, this.env(env), this.root);
        await this.fake.waitUntilServing(`${this.lemonadeUrl}/health`);
    }

    async startBackend(env: Record<string, string> = {}): Promise<void> {
        this.backendPort = await freePort();
        const argv = [PYTHON, '-m', 'gaia.ui.server', '--host', '127.0.0.1', '--port', String(this.backendPort), '--ui-dist', UI_DIST];
        const lemonade = { LEMONADE_BASE_URL: this.lemonadeUrl, ...env };
        this.backend = new Service('the GAIA backend', join(this.logs, 'backend.log'), argv, this.env(lemonade), this.root);
        await this.backend.waitUntilServing(`${this.url}/api/health`);
    }

    /** Point a backend at a Lemonade that isn't running yet. */
    async startBackendWithoutServer(env: Record<string, string> = {}): Promise<number> {
        this.lemonadePort = await this.closedPort();
        await this.startBackend(env);
        return this.lemonadePort;
    }

    async stopBackend(): Promise<void> {
        await this.backend?.stop();
        this.backend = null;
    }

    /** What reached the fake, oldest first. */
    requests(): FakeRequest[] {
        if (!existsSync(this.requestLog)) return [];
        return readFileSync(this.requestLog, 'utf-8').split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line) as FakeRequest);
    }

    pulls(): FakeRequest[] {
        return this.requests().filter((r) => r.method === 'POST' && r.path.endsWith('/pull'));
    }

    /** Delete a model from the fake, as if it never finished downloading. */
    async deleteModel(model: string): Promise<void> {
        const reply = await fetch(`${this.lemonadeUrl}/delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_name: model }),
        });
        expect(reply.ok, `deleting ${model} from the fake`).toBe(true);
    }

    config(): Record<string, unknown> {
        const path = join(this.home, '.gaia', 'config.json');
        return existsSync(path) ? (JSON.parse(readFileSync(path, 'utf-8')) as Record<string, unknown>) : {};
    }

    hasGaiaFile(name: string): boolean {
        return existsSync(join(this.home, '.gaia', name));
    }

    /** The backend's and fake's logs, for a failure's report. */
    describe(): string {
        return [join(this.logs, 'backend.log'), join(this.logs, 'fake.log')].map((p) => `--- ${p}\n${tail(p, 80)}`).join('\n');
    }

    async stop(): Promise<void> {
        await this.backend?.stop();
        await this.fake?.stop();
        await Promise.all(this.listeners.map((s) => new Promise((done) => s.close(done))));
        rmSync(this.root, { recursive: true, force: true, maxRetries: 5, retryDelay: 500 });
    }
}

interface Fixtures {
    journey: Journey;
    say: Say;
    /** Save this screen for people to read, per language. */
    shot: (name: string) => Promise<void>;
    /** Open the app in the test's language. */
    open: () => Promise<void>;
}

export const test = base.extend<Fixtures & JourneyOptions>({
    lang: ['en', { option: true }],
    say: async ({ lang }, use) => {
        await use(sayer(lang));
    },
    journey: async ({ page }, use, info) => {
        const journey = new Journey();
        const crashes: string[] = [];
        page.on('pageerror', (e) => crashes.push(e.message));
        await use(journey);
        if (info.status !== info.expectedStatus) await info.attach('services', { body: journey.describe(), contentType: 'text/plain' });
        await journey.stop();
        expect(crashes, 'uncaught errors in the page').toEqual([]);
    },
    shot: async ({ page, lang }, use, info) => {
        await use(async (name) => {
            const folder = join(SCREENS, lang);
            mkdirSync(folder, { recursive: true });
            const file = `${info.title.replace(/\W+/g, '-').toLowerCase()}--${name}.png`;
            await page.screenshot({ path: join(folder, file), animations: 'disabled' });
        });
    },
    open: async ({ page, lang, journey }, use) => {
        await page.addInitScript((code) => window.localStorage.setItem('gaia-chat-locale', code), lang);
        await use(async () => {
            await page.goto(`${journey.url}/`);
        });
    },
});

export { expect };

export const stepTitle = (page: Page) => page.locator('#setup-title');

/** From the plan: press Download and start, watch the bar move, end on Ready. */
export async function downloadToReady(page: Page, say: Say, shot: (name: string) => Promise<void>): Promise<void> {
    await page.getByRole('button', { name: say('setup.plan.start') }).click();
    await expect(stepTitle(page)).toHaveText(say('setup.download.title'));
    await watchDownload(page, say, shot);
    await testToReady(page, say, shot);
}

/** The overall bar must really move: seen partway, more than once, never backwards. */
export async function watchDownload(page: Page, say: Say, shot: (name: string) => Promise<void>): Promise<void> {
    const bar = page.getByRole('progressbar', { name: say('setup.progress.overallLabel') });
    const title = stepTitle(page);
    const seen: number[] = [];
    let shown = false;
    while ((await title.textContent()) === say('setup.download.title')) {
        // The bar appears after the run starts and goes when it ends: never wait on it.
        const value = (await bar.count()) ? Number(await bar.getAttribute('aria-valuenow', { timeout: 1_000 }).catch(() => 0)) : 0;
        if (value > 0 && value < 100) {
            if (seen.at(-1) !== value) seen.push(value);
            if (!shown) {
                await shot('downloading');
                shown = true;
            }
        }
        await sleep(100);
    }
    expect(seen.length, `overall progress seen: ${seen.join(', ')}`).toBeGreaterThanOrEqual(2);
    expect(seen).toEqual([...seen].sort((a, b) => a - b));
}

/** The Test step runs for real, and Ready comes only when it passed. */
export async function testToReady(page: Page, say: Say, shot: (name: string) => Promise<void>): Promise<void> {
    await expect(stepTitle(page)).toHaveText(say('setup.test.title'));
    await expect(page.locator('.setup-result.is-running, .setup-result.is-passed').first()).toBeVisible();
    await shot('testing');
    const ready = page.getByRole('button', { name: say('setup.ready.start') });
    const failed = page.locator('.setup-failure');
    await expect(ready.or(failed)).toBeVisible({ timeout: 5 * 60_000 });
    if (await failed.count()) {
        throw new Error(`the Test step failed: ${(await page.locator('.setup-result-error').allTextContents()).join('; ')}`);
    }
    await expect(stepTitle(page)).toHaveText(say('setup.ready.heading'));
}

/** Every Test row the backend ran, which must all have passed. */
export async function expectTestsPassed(page: Page, base: string, jobs: string[]): Promise<void> {
    const status = (await (await page.request.get(`${base}/api/setup/status`)).json()) as {
        tests: Array<{ job: string; state: string; error: string | null }>;
    };
    const failed = status.tests.filter((t) => t.state !== 'passed');
    expect(failed, 'Test rows that did not pass').toEqual([]);
    expect(status.tests.map((t) => t.job)).toEqual(expect.arrayContaining(jobs));
}
