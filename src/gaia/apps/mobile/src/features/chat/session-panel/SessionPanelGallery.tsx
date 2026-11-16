// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import type { BrowseResponse, ChatPerformance, ContextUsage, LocalModelStatus, Message, OutputItem, Session, SessionChanges, SessionEnvironment, SessionGit } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';
import { GaiaApiError } from '@/api/errors';
import { GaiaClientProvider } from '@/data';
import { FIXTURES } from '@/features/chat/messages/__fixtures__/messages';
import { MessageListView } from '@/features/chat/messages/MessageList';
import { Screen } from '@/ui';
import { IDLE_TURN, writeTurn } from '@/state/chatStreams';
import type { TurnPerformance, TurnSummary } from '@webui/types';

import { useSessionPanelStore } from './panelStore';
import { SessionPanel } from './SessionPanel';

/** Developer gallery: the session panel open over a chat, with Skills and Agent info in each state. */
export type SessionPanelGalleryState =
    | 'loaded'
    | 'empty'
    | 'error'
    | 'notRunning'
    | 'ctxMeasuring'
    | 'ctxNormal'
    | 'ctxEstimated'
    | 'ctxFilling'
    | 'ctxChat'
    | 'ctxNoTurn'
    | 'envFolders'
    | 'envRemove'
    | 'envOffline'
    | 'envFull'
    | 'envEmpty'
    | 'chgList'
    | 'chgConfirm'
    | 'chgConflict'
    | 'chgGit'
    | 'chgPending'
    | 'chgError'
    | 'chgReview'
    | 'ansFull'
    | 'ansPending'
    | 'ansNoLive'
    | 'ansError'
    | 'outItems'
    | 'outEmpty'
    | 'envPick'
    | 'envPickRefused';

const CONTEXT_STATES: readonly SessionPanelGalleryState[] = ['ctxMeasuring', 'ctxNormal', 'ctxEstimated', 'ctxFilling', 'ctxChat', 'ctxNoTurn'];

const ENV_STATES: readonly SessionPanelGalleryState[] = ['envFolders', 'envRemove', 'envOffline', 'envFull', 'envEmpty', 'envPick', 'envPickRefused'];

const CHANGES_STATES: readonly SessionPanelGalleryState[] = ['chgList', 'chgConfirm', 'chgConflict', 'chgGit', 'chgPending', 'chgError', 'chgReview'];

const DIFF = '@@ -1,3 +1,3 @@\n # Q3 summary\n-Revenue was flat.\n+Revenue grew 4%.\n Costs held steady.\n';
const CHANGE = (path: string, change: 'edited' | 'created' | 'deleted', additions: number | null, deletions: number | null, turns = 1): SessionChanges['files'][number] => ({
    path,
    change,
    unified: additions === null ? '' : DIFF,
    binary: additions === null,
    too_large: false,
    truncated: false,
    additions,
    deletions,
    before_size: 120,
    after_size: 140,
    message_ids: Array.from({ length: turns }, (_, i) => i + 1),
    changed_since: null,
});

const CHANGES: SessionChanges = {
    files: [
        CHANGE('/Users/avery/Documents/Quarterly reports/q3-summary.md', 'edited', 12, 4, 2),
        CHANGE('/Users/avery/Documents/Quarterly reports/forecast.csv', 'created', 40, 0),
        CHANGE('/Users/avery/Documents/Quarterly reports/old-draft.txt', 'deleted', 0, 18),
        CHANGE('/Users/avery/Documents/Quarterly reports/chart.png', 'created', null, null),
    ],
    totals: { files: 4, additions: 52, deletions: 22, uncounted: 1 },
    turns: 3,
    pending: false,
    untracked: [],
    incomplete: false,
};

const GIT: SessionGit = {
    available: true,
    repos: [
        {
            root: '/Users/avery/Documents/Quarterly reports',
            branch: 'main',
            remote: 'origin',
            default_branch: 'main',
            gh: true,
            files: CHANGES.files.map((f) => f.path),
            uncommitted: CHANGES.files.slice(0, 3).map((f) => f.path),
            ignored: [],
            draft: { title: 'Update the Q3 summary and add a forecast', body: 'Revenue grew 4%.' },
            pr_blocked: 'You are on the default branch (main). Switch to a feature branch first.',
        },
    ],
};

const ANSWER_STATES: readonly SessionPanelGalleryState[] = ['ansFull', 'ansPending', 'ansNoLive', 'ansError'];

const OUTPUT_STATES: readonly SessionPanelGalleryState[] = ['outItems', 'outEmpty'];

const SUMMARY: TurnSummary = {
    cost: { kind: 'estimate', usd: 0.0016 },
    model: { name: 'claude-sonnet-4-5', provider: 'claude', provider_name: 'Claude', remote: true },
    pages: [
        { kind: 'page', title: 'Mount Rainier weather forecast', url: 'https://www.weather.gov/rainier' },
        { kind: 'search_result', title: null, url: 'https://www.nps.gov/mora/planyourvisit/conditions.htm' },
    ],
    documents: [
        { name: 'trail-guide.pdf', path: '/Users/avery/Documents/trail-guide.pdf', pages: [5, 6, 8], passages: 6 },
        { name: 'notes.md', path: '/Users/avery/Documents/notes.md', passages: 1 },
    ],
    files: [
        { name: 'packing-list.md', path: '/Users/avery/Documents/packing-list.md', mtime: 100, openable: true },
        { name: 'old-plan.md', path: '/Users/avery/Documents/old-plan.md', mtime: 100, openable: true },
    ],
    sites: ['weather.gov', 'nps.gov'],
    live_data: { attempted: 2, failed: 0 },
};

const utc = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString().slice(0, 19).replace('T', ' ');

const OUTPUT = (id: string, kind: OutputItem['kind'], name: string, minutesAgo: number, extra: Partial<OutputItem> = {}): OutputItem => ({
    id,
    kind,
    name,
    path: kind === 'file' || kind === 'export' || kind === 'image' ? `/Users/avery/Documents/${name}` : null,
    detail: null,
    message_id: 2,
    created_at: utc(minutesAgo),
    change: kind === 'file' ? 'created' : null,
    state: 'present',
    openable: true,
    revealable: true,
    ...extra,
});

const OUTPUTS: OutputItem[] = [
    OUTPUT('o1', 'file', 'packing-list.md', 2),
    OUTPUT('o2', 'image', 'summit-sketch.png', 14),
    OUTPUT('o3', 'file', 'itinerary.md', 95, { change: 'edited' }),
    OUTPUT('o4', 'document', 'trail-guide.pdf', 180),
    OUTPUT('o5', 'memory', 'Prefers early starts', 300),
    OUTPUT('o6', 'file', 'old-plan.md', 600, { state: 'gone', change: 'deleted' }),
    OUTPUT('o7', 'schedule', 'Weekly forecast check', 1500, { message_id: null }),
];

const withSummary = (m: Message, summary: TurnSummary): Message => ({ ...m, turn_summary: summary }) as Message;

const FOLDER = (path: string, source: SessionEnvironment['folders'][number]['source'], removable = true) =>
    ({ path, name: path.split('/').pop(), kind: 'folder' as const, source, removable });
const FILE = (path: string) => ({ path, name: path.split('/').pop(), kind: 'file' as const, source: 'document' as const, removable: true });

const ENVIRONMENT: SessionEnvironment = {
    session_id: 'dev',
    folders: [
        FOLDER('/Users/avery/Documents/Quarterly reports', 'added'),
        FOLDER('/Users/avery/.gaia/documents', 'gaia_documents', false),
        FOLDER('/Users/avery/.gaia/scratch/dev', 'scratch', false),
        FILE('/Users/avery/.gaia/documents/budget-2026.xlsx'),
        FILE('/Users/avery/.gaia/documents/board-notes.pdf'),
        FILE('/Users/avery/.gaia/documents/vendor-contract.pdf'),
        FILE('/Users/avery/.gaia/documents/roadmap.md'),
    ],
    removed: [],
    internet: true,
    enforced: true,
    internet_enforced: true,
};

const entry = (name: string, dir: string) => ({ name, path: `${dir}/${name}`, type: 'folder' as const, size: 0, extension: null, modified: '2026-11-10T09:00:00Z' });
const QUICK_LINKS = [
    { name: 'Home', path: '/Users/avery', icon: 'home' },
    { name: 'Documents', path: '/Users/avery/Documents', icon: 'documents' },
];
const BROWSE_FOLDERS: Record<string, BrowseResponse> = {
    '': {
        current_path: '/Users/avery/Documents',
        parent_path: '/Users/avery',
        entries: [entry('Quarterly reports', '/Users/avery/Documents'), entry('Tax 2025', '/Users/avery/Documents'), entry('.cache', '/Users/avery/Documents')],
        quick_links: QUICK_LINKS,
    },
    '/Users/avery': {
        current_path: '/Users/avery',
        parent_path: '/Users',
        entries: [entry('Documents', '/Users/avery'), entry('Downloads', '/Users/avery')],
        quick_links: QUICK_LINKS,
        folder_refusal: "That's your whole user folder. Pick a folder inside it.",
    },
};

const USAGE: ContextUsage = {
    window: 65536,
    reply_reserve: 4096,
    system: 9000,
    tools: 6200,
    documents: 3100,
    history: 8400,
    query: 120,
    used: 26820,
    history_budget: 40000,
    history_used: 8400,
    turns_in_view: 6,
    turns_hidden: 0,
    at_risk: [],
    at_risk_tokens: 0,
    tokenizer: { exact: true, model: 'Gemma-4-E4B-it-GGUF' },
    categories: [
        { key: 'system', label: 'System prompt', tokens: 9000 },
        { key: 'tools', label: 'Tools', tokens: 6200 },
        { key: 'documents', label: 'Documents', tokens: 3100 },
        { key: 'history', label: 'Conversation', tokens: 8400 },
        { key: 'query', label: 'Your message', tokens: 120 },
    ],
    tool_loading: { loaded: ['search_web', 'read_file'], on_demand: ['gmail_search', 'calendar_list', 'drive_find'] },
    model: 'Gemma-4-E4B-it-GGUF',
    model_window: 131072,
    context_state: { put_away: { results: 4, tokens: 5200 }, summarized: { turns: 12, tokens: 14800, summary_tokens: 900 } },
};

const TURN: TurnPerformance = {
    message_id: 9,
    created_at: '2026-11-14T10:00:00Z',
    model: 'Gemma-4-E4B-it-GGUF',
    time: { wall_seconds: 14.2, model_seconds: 9.8, gaia_seconds: 4.4, tool_seconds: 3.1, waiting_seconds: 0, own_seconds: 1.3 },
    first_token_seconds: 1.4,
    first_token_source: 'server',
    prefill_tokens_per_second: 812.4,
    prefill_source: 'client',
    generation_tokens_per_second: 31.6,
    generation_source: 'server',
    model_calls: 3,
    tokens_in: 26820,
    tokens_new: 4200,
    tokens_cached: 22620,
    tokens_out: 640,
    last_prompt_tokens: 27011,
    tool_calls: 2,
    tool_seconds_total: 3.1,
    tools: [{ name: 'search_web', seconds: 2.4 }, { name: null, seconds: 0.7 }],
    loads: [],
    cost: { kind: 'local', usd: null },
};

const PERFORMANCE: ChatPerformance = {
    latest_message_id: 9,
    turns: [TURN],
    totals: {
        turns: 7,
        wall_seconds: 96.4,
        model_seconds: 70.1,
        gaia_seconds: 26.3,
        tokens_in: 150400,
        tokens_cached: 121000,
        tokens_out: 4300,
        tool_calls: 11,
        loads: 1,
        load_seconds: 6.2,
        usd: 0,
        unpriced_turns: 0,
    },
    now: { model: 'Gemma-4-E4B-it-GGUF', provider: 'local', loaded: true, device: 'gpu', backend: 'vulkan', ctx_size: 65536, weights_gb: 5.4, kv_cache_gb: 1.3, free_gb: 18.2, error: null },
};

const SESSION = {
    id: 'dev',
    title: 'Quarterly reports',
    created_at: '2026-11-14T10:00:00Z',
    updated_at: '2026-11-14T10:00:00Z',
    model: 'Gemma-4-E4B-it-GGUF',
    system_prompt: null,
    message_count: 4,
    document_ids: [],
    agent_type: 'gaia',
    inference_remote: false,
} as Session;

const LOCAL_MODEL = {
    state: 'loaded',
    active_model: 'Gemma-4-E4B-it-GGUF',
    active_display_name: 'Gemma 4 E4B',
    active_remote: false,
    model: 'Gemma-4-E4B-it-GGUF',
    display_name: 'Gemma 4 E4B',
    placement: { device: 'gpu', backend: 'vulkan', label: 'GPU · Vulkan', source: 'reported', warning: null },
    ctx_size: 65536,
    memory: { source: 'gpu', total_gb: 32, free_gb: 18.2, model_gb: 5.4 },
    memory_error: null,
    error: null,
    base_url: null,
    owner: 'gaia',
    busy: false,
    restarting: false,
    restart_error: null,
} as LocalModelStatus;

const TOOLS = [
    {
        name: 'search_web',
        description: 'Search the web and return the top results.\nSecond line is not shown.',
        category: 'agent',
    },
    {
        name: 'read_file',
        description: 'Read a text file from the working folder.',
        category: 'file_io',
    },
    {
        name: 'gmail_search',
        description: 'Find mail in the connected Gmail account.',
        category: 'mcp',
    },
    { name: 'coding', description: 'Write and run code', category: 'skill' },
];

export function SessionPanelGallery({ state }: { state: SessionPanelGalleryState }) {
    const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
    const client = useMemo(() => {
        const ctx = CONTEXT_STATES.includes(state);
        const env = ENV_STATES.includes(state);
        const chg = CHANGES_STATES.includes(state);
        const environment: SessionEnvironment =
            state === 'envOffline'
                ? { ...ENVIRONMENT, folders: ENVIRONMENT.folders.slice(0, 2), internet: false, removed: ['/Users/avery/Downloads/old-drafts'] }
                : state === 'envEmpty'
                  ? { ...ENVIRONMENT, folders: [], internet: false }
                  : state === 'envFull'
                    ? { ...ENVIRONMENT, folders: ENVIRONMENT.folders.slice(1, 2) }
                    : ENVIRONMENT;
        const answerState = ANSWER_STATES.includes(state);
        const summary: TurnSummary =
            state === 'ansNoLive'
                ? { ...SUMMARY, live_data: { attempted: 2, failed: 2 }, pages: [], sites: [] }
                : state === 'ansError'
                  ? { cost: { kind: 'unreported' }, error: 'the PC ran out of memory' }
                  : SUMMARY;
        const reply = FIXTURES.short.messages[1]!;
        const base = createFakeClient({
            messages: { dev: [FIXTURES.short.messages[0]!, withSummary(reply, summary)] },
            outputs: state === 'outItems' ? { dev: { session_id: 'dev', items: OUTPUTS } } : undefined,
            fileStatus: answerState ? { '/Users/avery/Documents/packing-list.md': { path: '/Users/avery/Documents/packing-list.md', exists: true, mtime: 200 }, '/Users/avery/Documents/old-plan.md': { path: '/Users/avery/Documents/old-plan.md', exists: false } } : undefined,
            sessions: env || chg ? [SESSION] : undefined,
            sessionChanges: chg && state !== 'chgError' ? { dev: { ...CHANGES, pending: state === 'chgPending' } } : undefined,
            sessionGit: state === 'chgGit' || state === 'chgConfirm' ? { dev: GIT } : undefined,
            undoConflict: state === 'chgConflict' ? new GaiaApiError(409, 'a file changed since', undefined, undefined, { status: 'conflict' }) : undefined,
            environments: env ? { dev: environment } : undefined,
            fileFolders: env ? (state === 'envPickRefused' ? { ...BROWSE_FOLDERS, '': BROWSE_FOLDERS['/Users/avery']! } : BROWSE_FOLDERS) : undefined,
            permissions: state === 'envOffline' ? { dev: 'full_access' } : undefined,
            activeModel: env ? { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', display_name: 'Gemma 4 E4B', label: null, remote: false, is_default: true, downloaded: true } : undefined,
            localModel: env ? LOCAL_MODEL : undefined,
            contextUsage: ctx
                ? {
                      available: true,
                      ...USAGE,
                      ...(state === 'ctxEstimated' ? { tokenizer: { exact: false, reason: 'the model is not loaded, so a shared tokenizer counted each part.' } } : {}),
                      ...(state === 'ctxFilling' ? { used: 61200, at_risk: ['What did the budget say last quarter?', 'Summarize the contract'], at_risk_tokens: 4100 } : {}),
                  }
                : undefined,
            chatPerformance: state === 'ctxNoTurn' ? { ...PERFORMANCE, latest_message_id: null, turns: [] } : ctx ? PERFORMANCE : undefined,
            capabilities:
                state === 'loaded'
                    ? {
                          dev: {
                              session_id: 'dev',
                              agent_type: 'gaia',
                              tools: TOOLS,
                              skills: [
                                  { name: 'coding', how: 'model' },
                                  { name: 'file-ops', how: 'auto' },
                                  { name: 'memory', how: 'always' },
                              ],
                          },
                      }
                    : state === 'empty'
                      ? {
                            dev: {
                                session_id: 'dev',
                                agent_type: 'gaia',
                                tools: [],
                                skills: [],
                            },
                        }
                      : undefined,
        });
        if (state === 'ctxMeasuring') {
            base.getContextUsage = () => new Promise(() => undefined);
            base.getChatPerformance = () => new Promise(() => undefined);
        }
        if (state === 'chgError') {
            base.getSessionChanges = async () => {
                throw new GaiaApiError(500, 'The PC could not read its saved copies');
            };
        }
        if (state === 'error') {
            base.getCapabilities = async () => {
                throw new GaiaApiError(500, 'The agent stopped responding');
            };
        }
        return base;
    }, [state]);
    useEffect(() => {
        const store = useSessionPanelStore.getState();
        const ctx = CONTEXT_STATES.includes(state);
        const env = ENV_STATES.includes(state);
        const chg = CHANGES_STATES.includes(state);
        const ans = ANSWER_STATES.includes(state);
        const out = OUTPUT_STATES.includes(state);
        store.setSection('answer', ans);
        store.setSection('outputs', out);
        store.setSection('changes', chg);
        store.setSection('skills', !ctx && !env && !chg && !ans && !out);
        store.setSection('tools', !ctx && !env && !chg && !ans && !out);
        store.setChangesSeed(
            state === 'chgConfirm'
                ? { phase: 'confirm' }
                : state === 'chgConflict'
                  ? { phase: 'conflict', message: 'a file changed since' }
                  : state === 'chgReview'
                    ? { review: true }
                    : null,
        );
        writeTurn('dev', state === 'ansPending' ? { ...IDLE_TURN, status: 'streaming' } : null);
        store.setSection('context', ctx);
        store.setEnvPicking(state === 'envPick' || state === 'envPickRefused');
        store.setEnvConfirm(state === 'envRemove' ? { kind: 'remove', path: ENVIRONMENT.folders[0]!.path } : state === 'envFull' ? { kind: 'full' } : null);
        store.setContextScope(state === 'ctxChat' ? 'chat' : 'turn');
        return () => writeTurn('dev', null);
    }, [state]);
    return (
        <>
            <Screen>
                <MessageListView messages={FIXTURES.short.messages} />
            </Screen>
            <SessionPanel
                sessionId="dev"
                open
                onClose={() => undefined}
                wrap={(body) => (
                    <QueryClientProvider client={queryClient}>
                        <GaiaClientProvider client={client}>{body}</GaiaClientProvider>
                    </QueryClientProvider>
                )}
            />
        </>
    );
}

export const SessionPanelGalleryData = { performance: PERFORMANCE, usage: USAGE };
