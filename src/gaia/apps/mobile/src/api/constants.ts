// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Not importable from webui (they live in services/api.ts and ChatView.tsx, with
// their logger and stores); api/constants.test.ts fails if any of these drifts.
export const UI_HEADERS = {
    'X-Gaia-UI': '1',
    'X-Gaia-Client': 'agent-ui',
} as const;

export const ENDPOINTS = {
    goals: '/api/goals',
    health: '/api/health',
    scheduleResults: '/api/schedules/{name:path}/results',
    schedules: '/api/schedules',
    sessions: '/api/sessions',
    session: '/api/sessions/{session_id}',
    sessionEvents: '/api/sessions/events',
    sessionMessages: '/api/sessions/{session_id}/messages',
    chatActive: '/api/chat/active',
    chatSend: '/api/chat/send',
    chatAttach: '/api/chat/attach',
    chatCancel: '/api/chat/cancel',
    chatConfirmTool: '/api/chat/confirm-tool',
    chatUserInput: '/api/chat/user-input',
    systemStatus: '/api/system/status',
} as const;

export function scheduleResultsPath(name: string, limit: number): string {
    return `${ENDPOINTS.scheduleResults.replace('{name:path}', encodeURIComponent(name))}?limit=${limit}`;
}

export function sessionPath(sessionId: string): string {
    return ENDPOINTS.session.replace('{session_id}', encodeURIComponent(sessionId));
}

export function sessionMessagesPath(sessionId: string): string {
    return ENDPOINTS.sessionMessages.replace('{session_id}', encodeURIComponent(sessionId));
}

export const SESSION_PAGE_SIZE = 200;
export const MESSAGE_PAGE_SIZE = 100;

/** The backend sends a keepalive every 5 s, so this much silence means the connection is gone. */
export const STREAM_IDLE_MS = 30_000;
/** A turn waiting for the model hears nothing until its place in line changes. */
export const QUEUED_IDLE_MS = 10 * 60_000;
/** The backend may hold a send for 60 s while another chat has the model, then answers 429. */
export const SEND_HEADER_TIMEOUT_MS = 90_000;
/** The chat-list event stream sends a keepalive every 30 s. */
export const SESSION_EVENTS_IDLE_MS = 90_000;
/** An attach replays at once; a slower answer means the backend is stuck or unreachable. */
export const ATTACH_HEADER_TIMEOUT_MS = 30_000;

/** How many past runs a schedule's history shows; the web UI asks for the same 20. */
export const SCHEDULE_RESULTS_LIMIT = 20;

/** How often to re-read the schedules while their screen is open, so counts and times follow the PC. */
export const SCHEDULES_POLL_MS = 10_000;
/** How often the app, while open, re-reads the schedules and which chats are running, as the web's app-wide poll does (30 s). */
export const APP_POLL_MS = 30_000;
/** How often the goals are re-read while their screen is open (30 s). */
export const GOALS_POLL_MS = 30_000;
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
