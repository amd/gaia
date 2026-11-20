// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import {
    AGENT_EVENT_TYPES,
    ATTACH_HEADER_TIMEOUT_MS,
    ENDPOINTS,
    MESSAGE_PAGE_SIZE,
    QUEUED_IDLE_MS,
    RESOLVES_PROMPTS,
    SEND_HEADER_TIMEOUT_MS,
    SESSION_PAGE_SIZE,
    STATUS_POLL_MS,
    STREAM_IDLE_MS,
    UI_HEADERS,
} from './constants';
import { ROUTE_ACCESS } from './routeAccess.gen';

const GAIA = resolve(__dirname, '../../../..');
const UI_DIR = join(GAIA, 'ui');
const WEBUI_SRC = join(GAIA, 'apps/webui/src');
const WEBUI_API = join(WEBUI_SRC, 'services/api.ts');

function pythonFiles(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true, recursive: true })
        .filter((e) => e.isFile() && e.name.endsWith('.py'))
        .map((e) => join(e.parentPath, e.name));
}

function pyString(source: string, name: string): string {
    const match = new RegExp(`^${name}\\s*=\\s*"([^"]*)"`, 'm').exec(source);
    if (!match) throw new Error(`${name} not found in security.py; update api/constants.test.ts`);
    return match[1]!;
}

function webuiSource(file: string): string {
    return readFileSync(join(WEBUI_SRC, file), 'utf-8');
}

// `export const NAME = 10 * 60_000;` -> 600000; anything but a product of literals fails the test.
function tsNumber(file: string, name: string): number {
    const match = new RegExp(`(?:export )?const ${name} = ([^;]+);`).exec(webuiSource(file));
    if (!match) throw new Error(`${name} not found in webui ${file}; update api/constants.test.ts`);
    return match[1]!.split('*').reduce((product, factor) => product * Number(factor.trim().replace(/_/g, '')), 1);
}

function tsStringSet(file: string, name: string): string[] {
    const match = new RegExp(`const ${name} = new Set\\(\\[([^\\]]*)\\]\\)`).exec(webuiSource(file));
    if (!match) throw new Error(`${name} not found in webui ${file}; update api/constants.test.ts`);
    return [...match[1]!.matchAll(/'([^']+)'/g)].map((m) => m[1]!).sort();
}

describe('request headers', () => {
    it('match the backend guard in ui/security.py', () => {
        const security = readFileSync(join(UI_DIR, 'security.py'), 'utf-8');
        const backend = {
            [pyString(security, 'UI_HEADER_NAME')]: pyString(security, 'UI_HEADER_VALUE'),
            [pyString(security, 'CLIENT_HEADER_NAME')]: pyString(security, 'AGENT_UI_CLIENT'),
        };
        const ours = Object.fromEntries(Object.entries(UI_HEADERS).map(([k, v]) => [k.toLowerCase(), v]));
        expect(ours).toEqual(backend);
    });

    it("match webui's UI_HEADER", () => {
        const api = readFileSync(WEBUI_API, 'utf-8');
        const match = /export const UI_HEADER = \{([^}]*)\}/.exec(api);
        if (!match) throw new Error('UI_HEADER not found in webui services/api.ts; update api/constants.test.ts');
        const webui = Object.fromEntries(
            [...match[1]!.matchAll(/'([^']+)'\s*:\s*'([^']*)'/g)].map((m) => [m[1]!, m[2]!]),
        );
        const ours = Object.fromEntries(Object.entries(UI_HEADERS).map(([k, v]) => [k.toLowerCase(), v]));
        expect(ours).toEqual(webui);
    });
});

describe('endpoints', () => {
    // A router mounted with `APIRouter(prefix=...)` declares its routes relative to it.
    const decorators = pythonFiles(UI_DIR).flatMap((file) => {
        const source = readFileSync(file, 'utf-8');
        const prefix = /APIRouter\([^)]*prefix="([^"]*)"/.exec(source)?.[1] ?? '';
        return [...source.matchAll(/@(?:router|app)\.(get|post|put|patch|delete)\(\s*"([^"]*)"/g)].flatMap((m) => [
            `${m[1]!.toUpperCase()} ${m[2]!}`,
            `${m[1]!.toUpperCase()} ${prefix}${m[2]!}`,
        ]);
    });
    const methods: Record<keyof typeof ENDPOINTS, string[]> = {
        goals: ['GET'],
        health: ['GET'],
        scheduleResults: ['GET'],
        schedules: ['GET'],
        sessions: ['GET', 'POST'],
        session: ['GET'],
        sessionEvents: ['GET'],
        sessionMessages: ['GET'],
        chatActive: ['GET'],
        chatSend: ['POST'],
        chatAttach: ['GET'],
        chatCancel: ['POST'],
        chatConfirmTool: ['POST'],
        chatUserInput: ['POST'],
        systemStatus: ['GET'],
    };
    const calls = Object.entries(ENDPOINTS).flatMap(([name, path]) =>
        methods[name as keyof typeof ENDPOINTS].map((method) => `${method} ${path}`),
    );

    it.each(calls)('%s has a route under src/gaia/ui', (call) => {
        expect(decorators).toContain(call);
    });

    it.each(calls)('%s is open to a paired phone', (call) => {
        expect(ROUTE_ACCESS[call]).toBe('remote');
    });
});

describe('page sizes', () => {
    const sessions = readFileSync(join(UI_DIR, 'routers/sessions.py'), 'utf-8');

    it('sessions: the largest page the backend serves', () => {
        expect(sessions).toContain(`limit = max(1, min(limit, ${SESSION_PAGE_SIZE}))`);
    });

    it('messages: within what the backend serves, and its default page', () => {
        expect(sessions).toContain(`    limit: int = ${MESSAGE_PAGE_SIZE},`);
    });
});

describe("webui's stream rules", () => {
    it.each([
        ['services/api.ts', 'STREAM_IDLE_MS', STREAM_IDLE_MS],
        ['services/api.ts', 'QUEUED_IDLE_MS', QUEUED_IDLE_MS],
        ['services/api.ts', 'SEND_HEADER_TIMEOUT_MS', SEND_HEADER_TIMEOUT_MS],
        ['services/api.ts', 'ATTACH_HEADER_TIMEOUT_MS', ATTACH_HEADER_TIMEOUT_MS],
        ['services/statusPoll.ts', 'STATUS_POLL_MS', STATUS_POLL_MS],
    ])('%s %s', (file, name, ours) => {
        expect(ours).toBe(tsNumber(file, name));
    });

    it.each([
        ['services/api.ts', 'AGENT_EVENT_TYPES', AGENT_EVENT_TYPES],
        ['components/ChatView.tsx', 'RESOLVES_PROMPTS', RESOLVES_PROMPTS],
    ])('%s %s', (file, name, ours) => {
        expect([...ours].sort()).toEqual(tsStringSet(file, name));
    });
});
