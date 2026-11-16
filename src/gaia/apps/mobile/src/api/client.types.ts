// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ActiveRuns, Message, Schedule, ScheduleResult, Session, StreamEvent, SystemStatus } from '@webui/types';
import type { Goal } from '@webui/services/memoryApi';
import type {
    AgentUiHealthResponse,
    CancelStreamResponse,
    ConfirmToolResponse,
    CreateSessionRequest,
    ToolConfirmRequest,
    UserInputRequest as ApiUserInputRequest,
    UserInputResponse,
} from '@webui/types/api.gen';

export type { ActiveRuns, Message, PermissionMode, Schedule, ScheduleResult, Session, StreamEvent, SystemStatus } from '@webui/types';
export type { Goal, GoalStatus, GoalTask, Priority as GoalPriority } from '@webui/services/memoryApi';
export type { CreateSessionRequest, ToolConfirmRequest } from '@webui/types/api.gen';

/** `display` is what the phone shows as the answer; it is never sent to GAIA. */
export type UserInputRequest = ApiUserInputRequest & { display?: string };

export interface SessionList {
    sessions: Session[];
    total: number;
}

export interface MessageList {
    messages: Message[];
    total: number;
}

export interface ScheduleList {
    schedules: Schedule[];
    total: number;
}

export interface ScheduleResults {
    results: ScheduleResult[];
    total: number;
}

export type HealthResponse = AgentUiHealthResponse;
export type CancelChatResult = CancelStreamResponse;
export type ConfirmToolResult = ConfirmToolResponse;
export type UserInputResult = UserInputResponse;

export interface RequestOptions {
    signal?: AbortSignal;
}

export interface GaiaClient {
    health(options?: RequestOptions): Promise<HealthResponse>;
    listSessions(options?: RequestOptions): Promise<SessionList>;
    getSession(sessionId: string, options?: RequestOptions): Promise<Session>;
    createSession(request?: CreateSessionRequest, options?: RequestOptions): Promise<Session>;
    getMessages(sessionId: string, options?: RequestOptions): Promise<MessageList>;
    /** Starts a turn and streams it to its `done` or `error` event. */
    sendMessage(sessionId: string, message: string, options?: RequestOptions): AsyncIterable<StreamEvent>;
    /**
     * Streams the chat's running turn: every event so far, then the live ones.
     * Ends at once, with no events, when no turn is running.
     * Aborting only stops this phone's view; the turn goes on on the PC.
     */
    attachToRun(sessionId: string, options?: RequestOptions): AsyncIterable<StreamEvent>;
    /** Asks GAIA to stop the chat's turn; the turn's stream then ends with a cancelled `done`. */
    cancelChat(sessionId: string, options?: RequestOptions): Promise<CancelChatResult>;
    /** Answers a `permission_request`: allow once, always allow this call in this chat, or deny. */
    confirmTool(request: ToolConfirmRequest, options?: RequestOptions): Promise<ConfirmToolResult>;
    /** Answers a `needs_input` question; the turn continues once GAIA has it. */
    sendUserInput(request: UserInputRequest, options?: RequestOptions): Promise<UserInputResult>;
    /**
     * Streams events about the PC's chat list, such as `set_active_session`. Never ends on its own:
     * it throws when the connection drops or goes silent, and the caller reconnects.
     */
    watchSessionEvents(options?: RequestOptions): AsyncIterable<StreamEvent>;
    /** The chats with a turn in flight, those queued, and those paused on the user. */
    getActiveRuns(options?: RequestOptions): Promise<ActiveRuns>;
    listSchedules(options?: RequestOptions): Promise<ScheduleList>;
    /** Every goal with its tasks, newest first. */
    listGoals(options?: RequestOptions): Promise<{ goals: Goal[]; total: number }>;
    /** A schedule's most recent runs, newest first. */
    listScheduleResults(name: string, limit: number, options?: RequestOptions): Promise<ScheduleResults>;
    getSystemStatus(options?: RequestOptions): Promise<SystemStatus>;
}
