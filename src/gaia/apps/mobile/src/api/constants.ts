// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { parseMediaSrc } from '@webui/utils/renderCards.core';

// Not importable from webui (they live in services/api.ts and ChatView.tsx, with
// their logger and stores); api/constants.test.ts fails if any of these drifts.
export const UI_HEADERS = {
    'X-Gaia-UI': '1',
    'X-Gaia-Client': 'agent-ui',
} as const;

export const ENDPOINTS = {
    agentInstallStatus: '/api/agents/{agent_id}/install-status',
    agents: '/api/agents',
    agentsInstall: '/api/agents/install',
    goal: '/api/goals/{goal_id}',
    goalApprove: '/api/goals/{goal_id}/approve',
    goalCancel: '/api/goals/{goal_id}/cancel',
    goalReject: '/api/goals/{goal_id}/reject',
    goals: '/api/goals',
    goalsHealth: '/api/goals/health',
    goalsStats: '/api/goals/stats',
    gpuMemory: '/api/gpu-memory',
    gpuMemoryPending: '/api/gpu-memory/pending',
    health: '/api/health',
    cloudPrices: '/api/spending/prices/{provider}',
    lemonadeDetect: '/api/lemonade/detect',
    lemonadeTarget: '/api/lemonade/target',
    localCustomModel: '/api/providers/local/custom',
    localCustomModelCheck: '/api/providers/local/custom/check',
    localCustomModelItem: '/api/providers/local/custom/{model_id}',
    localModelContext: '/api/providers/local/context',
    localModelRestart: '/api/providers/local/restart',
    localModelStatus: '/api/providers/local/status',
    logs: '/api/logs',
    mcpStatus: '/api/mcp/status',
    media: '/api/media/{kind}/{name}',
    memoryActivity: '/api/memory/activity',
    memoryAll: '/api/memory/all',
    memoryCommitDiscovery: '/api/memory/commit-discovery',
    memoryCommitInference: '/api/memory/commit-inference',
    memoryConsolidate: '/api/memory/consolidate',
    memoryConversation: '/api/memory/conversations/{session_id}',
    memoryConversations: '/api/memory/conversations',
    memoryConversationsSearch: '/api/memory/conversations/search',
    memoryEmbeddingCoverage: '/api/memory/embedding-coverage',
    memoryEntities: '/api/memory/entities',
    memoryKnowledge: '/api/memory/knowledge',
    memoryKnowledgeItem: '/api/memory/knowledge/{knowledge_id}',
    memoryRebuildEmbeddings: '/api/memory/rebuild-embeddings',
    memoryRebuildFts: '/api/memory/rebuild-fts',
    memoryReconcile: '/api/memory/reconcile',
    memoryRefreshSystemContext: '/api/memory/refresh-system-context',
    memoryReinitialize: '/api/memory/reinitialize',
    memorySettings: '/api/memory/settings',
    memoryStats: '/api/memory/stats',
    memoryStreamDiscovery: '/api/memory/stream-discovery',
    memoryStreamInference: '/api/memory/stream-inference',
    memoryTools: '/api/memory/tools',
    memoryUpcoming: '/api/memory/upcoming',
    messagingDiscord: '/api/messaging/discord',
    modelServer: '/api/system/model-server',
    modelServerBackend: '/api/system/model-server/backend',
    providerConnect: '/api/providers/{provider}/connect',
    providerModels: '/api/providers/{provider}/models',
    providersActive: '/api/providers/active',
    providersKeyStoreRetry: '/api/providers/key-store/retry',
    providersList: '/api/providers/list',
    providersSelect: '/api/providers/select',
    schedule: '/api/schedules/{name:path}',
    scheduleResults: '/api/schedules/{name:path}/results',
    scheduleRun: '/api/schedules/{name:path}/run',
    scheduleParse: '/api/schedules/parse',
    schedules: '/api/schedules',
    sessions: '/api/sessions',
    session: '/api/sessions/{session_id}',
    sessionCapabilities: '/api/sessions/{session_id}/capabilities',
    sessionChanges: '/api/sessions/{session_id}/changes',
    sessionChangesDiff: '/api/sessions/{session_id}/changes/{turn_id}/diff',
    sessionChangesGit: '/api/sessions/{session_id}/changes/git',
    sessionChangesGitCommit: '/api/sessions/{session_id}/changes/git/commit',
    sessionChangesGitPr: '/api/sessions/{session_id}/changes/git/pr',
    sessionChangesTotal: '/api/sessions/{session_id}/changes/total',
    sessionChangesUndo: '/api/sessions/{session_id}/changes/{turn_id}/undo',
    sessionChangesUndoAll: '/api/sessions/{session_id}/changes/undo',
    sessionDocument: '/api/sessions/{session_id}/documents/{doc_id}',
    sessionDocuments: '/api/sessions/{session_id}/documents',
    sessionEnvironment: '/api/sessions/{session_id}/environment',
    sessionEnvironmentFolderRemove: '/api/sessions/{session_id}/environment/folders/remove',
    sessionEnvironmentFolders: '/api/sessions/{session_id}/environment/folders',
    sessionEnvironmentInternet: '/api/sessions/{session_id}/environment/internet',
    sessionEvents: '/api/sessions/events',
    sessionExport: '/api/sessions/{session_id}/export',
    sessionMessage: '/api/sessions/{session_id}/messages/{message_id}',
    sessionMessages: '/api/sessions/{session_id}/messages',
    sessionMessagesAndBelow: '/api/sessions/{session_id}/messages/{message_id}/and-below',
    sessionOutputs: '/api/sessions/{session_id}/outputs',
    sessionPin: '/api/sessions/{session_id}/pin',
    sessionPrivate: '/api/sessions/{session_id}/private',
    sessionRead: '/api/sessions/{session_id}/read',
    sessionSearch: '/api/sessions/search',
    settings: '/api/settings',
    settingsUndo: '/api/settings/undo',
    setupCancel: '/api/setup/cancel',
    setupChatModel: '/api/setup/chat-model',
    setupCheck: '/api/setup/check',
    setupModelDownload: '/api/setup/models/download',
    setupModelRemove: '/api/setup/models/remove',
    setupModelSkip: '/api/setup/models/skip',
    setupNeeds: '/api/setup/needs/{job}',
    setupNpuRecheck: '/api/setup/npu/recheck',
    setupPlan: '/api/setup/plan',
    setupRun: '/api/setup/run',
    setupStatus: '/api/setup/status',
    setupVisionModel: '/api/setup/vision-model',
    skills: '/api/skills',
    skillsFeatured: '/api/skills/featured',
    skillsLinkPreview: '/api/skills/link/preview',
    speech: '/api/speech',
    speechModel: '/api/speech/model',
    speechSettings: '/api/speech/settings',
    spending: '/api/spending',
    spendingAllowMonth: '/api/spending/allow-month',
    spendingChat: '/api/spending/chat/{session_id}',
    spendingLimit: '/api/spending/limit',
    chatActive: '/api/chat/active',
    chatSend: '/api/chat/send',
    chatAttach: '/api/chat/attach',
    chatCancel: '/api/chat/cancel',
    chatConfirmTool: '/api/chat/confirm-tool',
    chatContext: '/api/chat/context/{session_id}',
    chatPermissionGrants: '/api/chat/permissions/{session_id}/grants',
    chatPermissions: '/api/chat/permissions/{session_id}',
    chatPermissionsDefault: '/api/chat/permissions',
    chatFollowup: '/api/chat/followup',
    chatFollowupOutcome: '/api/chat/followup/{session_id}',
    chatPerformance: '/api/chat/performance/{session_id}',
    chatRetryWith: '/api/chat/retry-with',
    chatRetryWithChoose: '/api/chat/retry-with/choose',
    chatUserInput: '/api/chat/user-input',
    connector: '/api/connectors/{connector_id}',
    connectorActivation: '/api/connectors/{connector_id}/activations/{agent_id:path}',
    connectorActivations: '/api/connectors/{connector_id}/activations',
    connectorDisable: '/api/connectors/{connector_id}/disable',
    connectorGrant: '/api/connectors/{connector_id}/grants/{agent_id:path}',
    connectorGrants: '/api/connectors/{connector_id}/grants',
    connectors: '/api/connectors',
    connectorsAgentMcps: '/api/connectors/agent-mcps',
    systemDownloadModel: '/api/system/download-model',
    systemLoadModel: '/api/system/load-model',
    connectorsEvents: '/api/connectors/events',
    systemResources: '/api/system/resources',
    systemStatus: '/api/system/status',
    document: '/api/documents/{doc_id}',
    documentCancel: '/api/documents/{doc_id}/cancel',
    documentFile: '/api/documents/{doc_id}/file',
    documentReindex: '/api/documents/{doc_id}/reindex',
    documentStatus: '/api/documents/{doc_id}/status',
    documentUpload: '/api/documents/upload',
    documentUploadPath: '/api/documents/upload-path',
    documents: '/api/documents',
    filesBrowse: '/api/files/browse',
    filesImage: '/api/files/image',
    filesPreview: '/api/files/preview',
    filesSearch: '/api/files/search',
    filesStatus: '/api/files/status',
    filesUpload: '/api/files/upload',
    filesUploadMedia: '/api/files/upload-media',
    onboardingPreflight: '/api/onboarding/preflight',
    onboardingStart: '/api/onboarding/start',
} as const;

export function agentInstallStatusPath(agentId: string): string {
    return ENDPOINTS.agentInstallStatus.replace('{agent_id}', encodeURIComponent(agentId));
}

export function connectorActivationPath(connectorId: string, agentId: string): string {
    return ENDPOINTS.connectorActivation
        .replace('{connector_id}', encodeURIComponent(connectorId))
        .replace('{agent_id:path}', encodeURIComponent(agentId));
}

export function connectorActivationsPath(connectorId: string): string {
    return ENDPOINTS.connectorActivations.replace('{connector_id}', encodeURIComponent(connectorId));
}

export function connectorDisablePath(connectorId: string): string {
    return ENDPOINTS.connectorDisable.replace('{connector_id}', encodeURIComponent(connectorId));
}

export function connectorPath(connectorId: string): string {
    return ENDPOINTS.connector.replace('{connector_id}', encodeURIComponent(connectorId));
}

export function connectorGrantPath(connectorId: string, agentId: string): string {
    return ENDPOINTS.connectorGrant
        .replace('{connector_id}', encodeURIComponent(connectorId))
        .replace('{agent_id:path}', encodeURIComponent(agentId));
}

export function connectorGrantsPath(connectorId: string): string {
    return ENDPOINTS.connectorGrants.replace('{connector_id}', encodeURIComponent(connectorId));
}

export function goalPath(goalId: string, action?: 'approve' | 'cancel' | 'reject'): string {
    const path = ENDPOINTS.goal.replace('{goal_id}', encodeURIComponent(goalId));
    return action ? `${path}/${action}` : path;
}

export function memoryActivityPath(days: number): string {
    return `${ENDPOINTS.memoryActivity}?days=${days}`;
}

export function memoryConversationPath(sessionId: string): string {
    return ENDPOINTS.memoryConversation.replace('{session_id}', encodeURIComponent(sessionId));
}

export function memoryConversationsPath(limit: number): string {
    return `${ENDPOINTS.memoryConversations}?limit=${limit}`;
}

export function memoryConversationsSearchPath(query: string, limit: number): string {
    return `${ENDPOINTS.memoryConversationsSearch}?query=${encodeURIComponent(query)}&limit=${limit}`;
}

export function memoryKnowledgeItemPath(knowledgeId: string): string {
    return ENDPOINTS.memoryKnowledgeItem.replace('{knowledge_id}', encodeURIComponent(knowledgeId));
}

/** The knowledge list's query, built as the web's `toQuery` does: empty values dropped, the rest encoded. */
export function memoryKnowledgePath(params: object): string {
    const parts = Object.entries(params as Record<string, unknown>)
        .filter(([, value]) => value !== undefined && value !== null && value !== '')
        .flatMap(([key, value]) => (Array.isArray(value) ? value : [value]).map((one) => `${encodeURIComponent(key)}=${encodeURIComponent(String(one))}`));
    return parts.length ? `${ENDPOINTS.memoryKnowledge}?${parts.join('&')}` : ENDPOINTS.memoryKnowledge;
}

export function memoryStreamInferencePath(includeBrowser: boolean): string {
    return `${ENDPOINTS.memoryStreamInference}?include_browser=${includeBrowser}`;
}

export function memoryUpcomingPath(days: number): string {
    return `${ENDPOINTS.memoryUpcoming}?days=${days}`;
}

export function schedulePath(name: string): string {
    return ENDPOINTS.schedule.replace('{name:path}', encodeURIComponent(name));
}

export function scheduleResultsPath(name: string, limit: number): string {
    return `${ENDPOINTS.scheduleResults.replace('{name:path}', encodeURIComponent(name))}?limit=${limit}`;
}

export function scheduleRunPath(name: string): string {
    return ENDPOINTS.scheduleRun.replace('{name:path}', encodeURIComponent(name));
}

export function setupNeedsPath(job: string): string {
    return ENDPOINTS.setupNeeds.replace('{job}', encodeURIComponent(job));
}

export function sessionPath(sessionId: string): string {
    return ENDPOINTS.session.replace('{session_id}', encodeURIComponent(sessionId));
}

export function chatPerformancePath(sessionId: string): string {
    return ENDPOINTS.chatPerformance.replace('{session_id}', encodeURIComponent(sessionId));
}

export function chatPermissionsPath(sessionId: string): string {
    return ENDPOINTS.chatPermissions.replace('{session_id}', encodeURIComponent(sessionId));
}

export function providerConnectPath(provider: string): string {
    return ENDPOINTS.providerConnect.replace('{provider}', encodeURIComponent(provider));
}

export function chatPermissionGrantsPath(sessionId: string, key?: string): string {
    const path = ENDPOINTS.chatPermissionGrants.replace('{session_id}', encodeURIComponent(sessionId));
    return key === undefined ? path : `${path}?key=${encodeURIComponent(key)}`;
}

export function chatSpendingPath(sessionId: string, since: string, model: string | null): string {
    const query = new URLSearchParams({ since });
    if (model) query.set('model', model);
    return `${ENDPOINTS.spendingChat.replace('{session_id}', encodeURIComponent(sessionId))}?${query}`;
}

export function cloudPricesPath(provider: string): string {
    return ENDPOINTS.cloudPrices.replace('{provider}', encodeURIComponent(provider));
}

export function providerModelsPath(provider: string): string {
    return ENDPOINTS.providerModels.replace('{provider}', encodeURIComponent(provider));
}

export function localCustomModelPath(model: string): string {
    return ENDPOINTS.localCustomModelItem.replace('{model_id}', encodeURIComponent(model));
}

export function chatContextPath(sessionId: string): string {
    return ENDPOINTS.chatContext.replace('{session_id}', encodeURIComponent(sessionId));
}

export function followupOutcomePath(sessionId: string): string {
    return ENDPOINTS.chatFollowupOutcome.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionDocumentPath(sessionId: string, docId: string): string {
    return ENDPOINTS.sessionDocument
        .replace('{session_id}', encodeURIComponent(sessionId))
        .replace('{doc_id}', encodeURIComponent(docId));
}

export function sessionDocumentsPath(sessionId: string): string {
    return ENDPOINTS.sessionDocuments.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionOutputsPath(sessionId: string): string {
    return ENDPOINTS.sessionOutputs.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionCapabilitiesPath(sessionId: string): string {
    return ENDPOINTS.sessionCapabilities.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionEnvironmentPath(sessionId: string, part?: 'folderRemove' | 'folders' | 'internet'): string {
    const template =
        part === 'folderRemove'
            ? ENDPOINTS.sessionEnvironmentFolderRemove
            : part === 'folders'
              ? ENDPOINTS.sessionEnvironmentFolders
              : part === 'internet' ? ENDPOINTS.sessionEnvironmentInternet : ENDPOINTS.sessionEnvironment;
    return template.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionChangesPath(sessionId: string): string {
    return ENDPOINTS.sessionChanges.replace('{session_id}', encodeURIComponent(sessionId));
}

const CHANGES_PARTS = {
    git: ENDPOINTS.sessionChangesGit,
    gitCommit: ENDPOINTS.sessionChangesGitCommit,
    gitPr: ENDPOINTS.sessionChangesGitPr,
    total: ENDPOINTS.sessionChangesTotal,
    undoAll: ENDPOINTS.sessionChangesUndoAll,
};

/** The chat-wide Changes calls: the running total, Undo all and the git actions. */
export function sessionChangesPartPath(sessionId: string, part: keyof typeof CHANGES_PARTS): string {
    return CHANGES_PARTS[part].replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionChangesDiffPath(sessionId: string, turnId: string): string {
    return ENDPOINTS.sessionChangesDiff.replace('{session_id}', encodeURIComponent(sessionId)).replace('{turn_id}', encodeURIComponent(turnId));
}

export function sessionChangesUndoPath(sessionId: string, turnId: string): string {
    return ENDPOINTS.sessionChangesUndo.replace('{session_id}', encodeURIComponent(sessionId)).replace('{turn_id}', encodeURIComponent(turnId));
}

export function sessionExportPath(sessionId: string): string {
    return ENDPOINTS.sessionExport.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionPinPath(sessionId: string): string {
    return ENDPOINTS.sessionPin.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionPrivatePath(sessionId: string): string {
    return ENDPOINTS.sessionPrivate.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionReadPath(sessionId: string): string {
    return ENDPOINTS.sessionRead.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionMessagesPath(sessionId: string): string {
    return ENDPOINTS.sessionMessages.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionMessagePath(sessionId: string, messageId: number): string {
    return ENDPOINTS.sessionMessage
        .replace('{session_id}', encodeURIComponent(sessionId))
        .replace('{message_id}', String(messageId));
}

export function sessionMessagesAndBelowPath(sessionId: string, messageId: number): string {
    return ENDPOINTS.sessionMessagesAndBelow
        .replace('{session_id}', encodeURIComponent(sessionId))
        .replace('{message_id}', String(messageId));
}

export function documentPath(docId: string, endpoint: 'document' | 'documentCancel' | 'documentFile' | 'documentReindex' | 'documentStatus' = 'document'): string {
    return ENDPOINTS[endpoint].replace('{doc_id}', encodeURIComponent(docId));
}

/** A library document's file, named with the chat that may read it away from the PC. */
export function documentFilePath(docId: string, sessionId: string): string {
    return `${documentPath(docId, 'documentFile')}?session_id=${encodeURIComponent(sessionId)}`;
}

/** The library file's URL, as the web UI's `documentFileUrl` builds it: the page rides in the fragment. */
export function documentFileUrl(baseUrl: string, docId: string, sessionId: string, page?: number): string {
    return `${baseUrl.replace(/\/+$/, '')}${documentFilePath(docId, sessionId)}${page ? `#page=${page}` : ''}`;
}

/** A PC file's preview or image, named by its path on the PC; the chat's id is what lets a phone read it. */
export function filePath(endpoint: 'filesImage' | 'filesPreview', path: string, sessionId: string, lines?: number): string {
    const query = `path=${encodeURIComponent(path)}&session_id=${encodeURIComponent(sessionId)}`;
    return `${ENDPOINTS[endpoint]}?${query}${lines ? `&lines=${lines}` : ''}`;
}

/** A generated audio file's path, from its card's `/api/media/audio/<name>`; null for any other `src`, so a card never names its own URL. */
export function generatedAudioPath(src: string): string | null {
    const name = parseMediaSrc('audio', src);
    return name === null ? null : ENDPOINTS.media.replace('{kind}', 'audio').replace('{name}', name);
}

/** The types an audio card accepts: the PC's media route serves WAV only (`generated_media.MEDIA_TYPES`). */
export const GENERATED_AUDIO_TYPES: readonly string[] = ['audio/wav', 'audio/wave', 'audio/x-wav'];
/** About six minutes of CD-quality WAV; the whole file is held in memory before it plays. */
export const MAX_GENERATED_AUDIO_BYTES = 64 * 1024 * 1024;

/** Typing pauses this long before a search goes out; the web UI's `SEARCH_DEBOUNCE_MS`. */
export const SEARCH_DEBOUNCE_MS = 200;

export const SESSION_PAGE_SIZE = 200;
export const MESSAGE_PAGE_SIZE = 100;

/** The backend sends a keepalive every 5 s, so this much silence means the connection is gone. */
export const STREAM_IDLE_MS = 30_000;
/** A turn waiting for the model hears nothing until its place in line changes. */
export const QUEUED_IDLE_MS = 10 * 60_000;
/** The backend may hold a send for 60 s while another chat has the model, then answers 429. */
export const SEND_HEADER_TIMEOUT_MS = 90_000;
/** The chat-list event stream sends a keepalive every 30 s. */
/** How long a disconnect waits for the PC to report whether the provider confirmed the revoke. */
export const CONNECTOR_REVOKE_OUTCOME_MS = 3_000;
export const SESSION_EVENTS_IDLE_MS = 90_000;
/** An attach replays at once; a slower answer means the backend is stuck or unreachable. */
export const ATTACH_HEADER_TIMEOUT_MS = 30_000;
/** How often to re-read the PC's connectors while a reply waits on an account being connected (webui's ConnectorRetryBanner). */
export const CONNECTOR_POLL_MS = 4_000;

/** How many past runs a schedule's history shows; the web UI asks for the same 20. */
export const SCHEDULE_RESULTS_LIMIT = 20;

/** How often to re-read the schedules while their screen is open, so counts and times follow the PC. */
export const SCHEDULES_POLL_MS = 10_000;
/** How often the app, while open, re-reads the schedules and which chats are running, as the web's app-wide poll does (30 s). */
export const APP_POLL_MS = 30_000;
/** How often the goal store's health is re-read, as the web's does (30 s). */
export const GOAL_HEALTH_POLL_MS = 30_000;

/** How often Settings > Messaging re-reads the Discord bridge while it is in one of the states it can leave by itself (webui's MessagingSettings). */
export const DISCORD_POLL_MS = 3_000;
export const DISCORD_LIVE_STATES: ReadonlySet<string> = new Set(['starting', 'pairing', 'connected']);
/** How long a memory result notice stays up, as the web's toasts do (4 s); a failure stays twice as long to be read. */
/** A profile scan or inference may go quiet for a while (a model call, a big home folder); the web's EventSource waits without limit, so this is only a dead-connection guard. */
export const PROFILE_STREAM_IDLE_MS = 5 * 60_000;
export const MEMORY_NOTICE_MS = 4_000;
export const MEMORY_ERROR_NOTICE_MS = 8_000;
/** How long a Delete button waits for its "Confirm?" press; the web UI now confirms in a dialog. */
export const CONFIRM_DELETE_MS = 8000;
/** How often the model status pill re-reads the model server while its sheet is open, and while it is closed. */
export const MODEL_STATUS_OPEN_POLL_MS = 3_000;
export const MODEL_STATUS_POLL_MS = 15_000;
/** How often a model download's progress is read, and how many missing or failed reads end the wait. */
export const DOWNLOAD_POLL_MS = 1_000;
export const DOWNLOAD_NO_ROW_LIMIT = 15;
export const DOWNLOAD_MISS_LIMIT = 5;
/** How often a Reconnect waiting on the PC's OS key-store prompt asks again; the web UI's CONNECT_RETRY_MS. */
export const CONNECT_RETRY_MS = 2_000;
/** The longest the model-needed check may take before the job goes ahead without it. */
export const MODEL_NEED_CHECK_LIMIT_MS = 8_000;
/** What the PC answers a log request from a phone; the model server log opens only on the PC (security.py). */
export const PC_ONLY_REFUSAL =
    "Do this on the PC running GAIA. It isn't allowed over mobile access, from a paired device or from another computer.";
/** How long to wait after a failed resource read before trying again (webui's `RETRY_MS`). */
export const RESOURCE_RETRY_MS = 5_000;
/** How often a chat's tools and skills are re-read while its reply runs, so they show once its agent starts. */
export const CAPABILITIES_POLL_MS = 2_000;
/** How often to re-read the PC's status while it is still starting or warming up. */
export const STATUS_POLL_MS = 5_000;
/** How often a healthy PC's status is re-read, so an idle app notices the PC going away. */
export const STATUS_IDLE_POLL_MS = 30_000;

/** Stream events that report the agent's activity rather than the answer's text. */
export const AGENT_EVENT_TYPES: ReadonlySet<string> = new Set([
    'status', 'step', 'thinking', 'commentary', 'plan',
    'tool_start', 'tool_end', 'tool_result', 'tool_args', 'tool_confirm', 'skill_use', 'agent_error',
    'permission_request', 'needs_confirmation', 'needs_input', 'policy_alert',
    'followup_taken', 'followup_returned', 'followup_unavailable', 'context_usage',
]);

/** Once the run reaches one of these, a permission prompt or question before it is no longer waiting. */
export const RESOLVES_PROMPTS: ReadonlySet<string> = new Set(['tool_start', 'tool_end', 'tool_result']);

/** The PC's folder listing; a phone names the chat whose folders it may see. */
export function filesBrowsePath(sessionId: string, path?: string): string {
    const query = new URLSearchParams(path ? { path } : {});
    query.set('session_id', sessionId);
    return `${ENDPOINTS.filesBrowse}?${query}`;
}

/** A name search on the PC; `fileTypes` is a comma list of extensions. */
export function filesSearchPath(sessionId: string, query: string, fileTypes: string | undefined, limit: number): string {
    const params = new URLSearchParams({ query, max_results: String(limit), session_id: sessionId });
    if (fileTypes) params.set('file_types', fileTypes);
    return `${ENDPOINTS.filesSearch}?${params}`;
}

/** Indexes a file that is already on the PC, naming the chat that may read it. */
export function documentUploadPathPath(sessionId: string): string {
    return `${ENDPOINTS.documentUploadPath}?session_id=${encodeURIComponent(sessionId)}`;
}
