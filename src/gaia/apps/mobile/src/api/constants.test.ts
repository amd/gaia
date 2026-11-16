// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import {
    AGENT_EVENT_TYPES,
    ATTACH_HEADER_TIMEOUT_MS,
    chatSpendingPath,
    DISCORD_LIVE_STATES,
    DISCORD_POLL_MS,
    DOWNLOAD_MISS_LIMIT,
    DOWNLOAD_NO_ROW_LIMIT,
    DOWNLOAD_POLL_MS,
    ENDPOINTS,
    MESSAGE_PAGE_SIZE,
    MODEL_NEED_CHECK_LIMIT_MS,
    MODEL_STATUS_OPEN_POLL_MS,
    PC_ONLY_REFUSAL,
    QUEUED_IDLE_MS,
    RESOLVES_PROMPTS,
    RESOURCE_RETRY_MS,
    SEARCH_DEBOUNCE_MS,
    SEND_HEADER_TIMEOUT_MS,
    SESSION_PAGE_SIZE,
    STATUS_POLL_MS,
    STREAM_IDLE_MS,
    UI_HEADERS,
} from './constants';

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
        agentInstallStatus: ['GET'],
        agents: ['GET'],
        agentsInstall: ['POST'],
        goal: ['DELETE'],
        goalApprove: ['PUT'],
        goalCancel: ['PUT'],
        goalReject: ['PUT'],
        goals: ['GET', 'POST'],
        goalsHealth: ['GET'],
        goalsStats: ['GET'],
        gpuMemory: ['GET'],
        gpuMemoryPending: ['DELETE', 'POST'],
        health: ['GET'],
        cloudPrices: ['GET'],
        lemonadeDetect: ['GET'],
        lemonadeTarget: ['GET', 'POST'],
        localCustomModel: ['POST'],
        localCustomModelCheck: ['POST'],
        localCustomModelItem: ['DELETE'],
        localModelContext: ['GET', 'PUT'],
        localModelRestart: ['POST'],
        localModelStatus: ['GET'],
        logs: ['GET'],
        mcpStatus: ['GET'],
        media: ['GET'],
        memoryActivity: ['GET'],
        memoryAll: ['DELETE'],
        memoryCommitDiscovery: ['POST'],
        memoryCommitInference: ['POST'],
        memoryConsolidate: ['POST'],
        memoryConversation: ['GET'],
        memoryConversations: ['GET'],
        memoryConversationsSearch: ['GET'],
        memoryEmbeddingCoverage: ['GET'],
        memoryEntities: ['GET'],
        memoryKnowledge: ['GET', 'POST'],
        memoryKnowledgeItem: ['DELETE', 'PUT'],
        memoryRebuildEmbeddings: ['POST'],
        memoryRebuildFts: ['POST'],
        memoryReconcile: ['POST'],
        memoryRefreshSystemContext: ['POST'],
        memoryReinitialize: ['POST'],
        memorySettings: ['GET', 'PUT'],
        memoryStats: ['GET'],
        memoryStreamDiscovery: ['GET'],
        memoryStreamInference: ['GET'],
        memoryTools: ['GET'],
        memoryUpcoming: ['GET'],
        messagingDiscord: ['GET'],
        providerConnect: ['POST'],
        providerModels: ['GET'],
        providersActive: ['GET'],
        providersKeyStoreRetry: ['POST'],
        providersList: ['GET'],
        providersSelect: ['POST'],
        schedule: ['DELETE', 'PUT'],
        scheduleResults: ['GET'],
        scheduleRun: ['POST'],
        scheduleParse: ['POST'],
        schedules: ['GET', 'POST'],
        sessions: ['GET', 'POST'],
        session: ['DELETE', 'GET', 'PUT'],
        sessionCapabilities: ['GET'],
        sessionChanges: ['GET'],
        sessionChangesDiff: ['GET'],
        sessionChangesGit: ['GET'],
        sessionChangesGitCommit: ['POST'],
        sessionChangesGitPr: ['POST'],
        sessionChangesTotal: ['GET'],
        sessionChangesUndo: ['POST'],
        sessionChangesUndoAll: ['POST'],
        sessionDocument: ['DELETE'],
        sessionDocuments: ['POST'],
        sessionEnvironment: ['GET'],
        sessionEnvironmentFolderRemove: ['POST'],
        sessionEnvironmentFolders: ['POST'],
        sessionEnvironmentInternet: ['PUT'],
        sessionEvents: ['GET'],
        sessionExport: ['GET'],
        sessionMessage: ['DELETE'],
        sessionMessages: ['GET'],
        sessionMessagesAndBelow: ['DELETE'],
        sessionOutputs: ['GET'],
        sessionPin: ['PUT'],
        sessionPrivate: ['PATCH'],
        sessionRead: ['POST'],
        sessionSearch: ['GET'],
        settings: ['GET', 'PUT'],
        settingsUndo: ['GET', 'PUT'],
        setupCancel: ['POST'],
        setupChatModel: ['POST'],
        setupCheck: ['GET'],
        setupModelDownload: ['POST'],
        setupModelRemove: ['POST'],
        setupModelSkip: ['POST'],
        setupNeeds: ['GET'],
        setupNpuRecheck: ['POST'],
        setupPlan: ['GET'],
        setupRun: ['POST'],
        setupStatus: ['GET'],
        setupVisionModel: ['POST'],
        skills: ['GET'],
        skillsFeatured: ['GET'],
        skillsLinkPreview: ['POST'],
        speech: ['POST'],
        speechModel: ['GET'],
        speechSettings: ['GET', 'PUT'],
        spending: ['GET'],
        spendingAllowMonth: ['POST'],
        spendingChat: ['GET'],
        spendingLimit: ['PUT'],
        chatActive: ['GET'],
        chatSend: ['POST'],
        chatAttach: ['GET'],
        chatCancel: ['POST'],
        chatConfirmTool: ['POST'],
        chatContext: ['GET'],
        chatPermissionGrants: ['DELETE'],
        chatPermissions: ['GET', 'PUT'],
        chatPermissionsDefault: ['GET', 'PUT'],
        chatFollowup: ['POST'],
        chatFollowupOutcome: ['GET'],
        chatPerformance: ['GET'],
        chatRetryWith: ['POST'],
        chatRetryWithChoose: ['POST'],
        chatUserInput: ['POST'],
        connector: ['DELETE'],
        connectorActivation: ['DELETE', 'PUT'],
        connectorActivations: ['GET'],
        connectorDisable: ['POST'],
        connectorGrant: ['DELETE', 'PUT'],
        connectorGrants: ['GET'],
        connectors: ['GET'],
        connectorsAgentMcps: ['GET'],
        systemDownloadModel: ['POST'],
        systemLoadModel: ['POST'],
        connectorsEvents: ['GET'],
        systemResources: ['GET'],
        systemStatus: ['GET'],
        document: ['DELETE'],
        documentCancel: ['POST'],
        documentFile: ['GET'],
        documentReindex: ['POST'],
        documentStatus: ['GET'],
        documentUpload: ['POST'],
        documentUploadPath: ['POST'],
        documents: ['GET'],
        filesBrowse: ['GET'],
        filesImage: ['GET'],
        filesPreview: ['GET'],
        filesSearch: ['GET'],
        filesStatus: ['POST'],
        filesUpload: ['POST'],
        filesUploadMedia: ['POST'],
        modelServer: ['GET'],
        modelServerBackend: ['PUT'],
        onboardingPreflight: ['GET'],
        onboardingStart: ['GET'],
    };
    const calls = Object.entries(ENDPOINTS).flatMap(([name, path]) =>
        methods[name as keyof typeof ENDPOINTS].map((method) => `${method} ${path}`),
    );

    it.each(calls)('%s has a route under src/gaia/ui', (call) => {
        expect(decorators).toContain(call);
    });
});

describe('log refusal', () => {
    it("is the PC's refusal in ui/security.py", () => {
        const security = readFileSync(join(UI_DIR, 'security.py'), 'utf-8').replace(/"\s*\n\s*"/g, '');
        expect(security).toContain(PC_ONLY_REFUSAL);
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
        ['components/Sidebar.tsx', 'SEARCH_DEBOUNCE_MS', SEARCH_DEBOUNCE_MS],
        ['services/statusPoll.ts', 'STATUS_POLL_MS', STATUS_POLL_MS],
        ['components/ModelStatusPill.tsx', 'POLL_MS', MODEL_STATUS_OPEN_POLL_MS],
        ['components/settings/MessagingSettings.tsx', 'POLL_MS', DISCORD_POLL_MS],
        ['components/ModelNeededDialog.tsx', 'POLL_MS', DOWNLOAD_POLL_MS],
        ['components/ModelNeededDialog.tsx', 'NO_ROW_LIMIT', DOWNLOAD_NO_ROW_LIMIT],
        ['components/ModelNeededDialog.tsx', 'MISS_LIMIT', DOWNLOAD_MISS_LIMIT],
        ['stores/modelNeededStore.ts', 'CHECK_LIMIT_MS', MODEL_NEED_CHECK_LIMIT_MS],
        ['hooks/useResourceUsage.ts', 'RETRY_MS', RESOURCE_RETRY_MS],
    ])('%s %s', (file, name, ours) => {
        expect(ours).toBe(tsNumber(file, name));
    });

    it.each([
        ['services/api.ts', 'AGENT_EVENT_TYPES', AGENT_EVENT_TYPES],
        ['components/ChatView.tsx', 'RESOLVES_PROMPTS', RESOLVES_PROMPTS],
        ['components/settings/MessagingSettings.tsx', 'LIVE', DISCORD_LIVE_STATES],
    ])('%s %s', (file, name, ours) => {
        expect([...ours].sort()).toEqual(tsStringSet(file, name));
    });
});

describe('chatSpendingPath', () => {
    it('sends the day start and, only when there is one, the model', () => {
        expect(chatSpendingPath('s 1', '2026-11-15T08:00:00.000Z', null)).toBe('/api/spending/chat/s%201?since=2026-11-15T08%3A00%3A00.000Z');
        expect(chatSpendingPath('s', 'x', 'a/b')).toBe('/api/spending/chat/s?since=x&model=a%2Fb');
    });
});
