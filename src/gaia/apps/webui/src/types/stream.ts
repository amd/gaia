// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Events streamed over SSE during a chat turn. */

import type { Document, InferenceStats, MCPServerStatus, McpOutput, TurnSummary } from './index';

/** Extended SSE event types for agent communication. */
export type StreamEventType =
    | 'chunk'        // Text content chunk
    | 'done'         // Stream complete
    | 'error'        // Error
    | 'status'       // Agent state change
    | 'step'         // Step progress
    | 'thinking'     // Agent reasoning
    | 'commentary'   // A note the agent wrote before a tool call
    | 'plan'         // Agent plan
    | 'tool_start'   // Tool execution started
    | 'tool_end'     // Tool execution completed
    | 'tool_result'  // Tool result summary
    | 'tool_args'    // Tool arguments detail
    | 'skill_use'    // A turn runs on a skill (skill, how, loaded)
    | 'answer'       // Final answer from agent
    | 'agent_error'  // Agent-level error (non-fatal)
    | 'permission_request' // Tool confirmation request
    | 'needs_confirmation' // Stateless confirmation card (email /query, #2109) — informational, non-blocking
    | 'needs_input' // Mid-run question (#2595) — answerable; the run blocks until POST /chat/user-input
    | 'policy_alert' // Governance policy blocked a tool
    | 'mcp_status'   // MCP server connection status update
    | 'agent_created' // New agent created — triggers agent list refresh
    | 'followup_taken'    // The running turn took a message sent mid-turn
    | 'followup_returned' // The turn ended without taking these mid-turn messages
    | 'followup_unavailable' // The running turn can't take a message mid-turn
    | 'context_usage';    // What this turn's model call holds, by kind

/** One thing found in a recording section, before the whole recording is read. */
export interface FoundItem {
    title: string;
    /** `MM:SS` or `H:MM:SS`. */
    start: string;
    seconds: number;
}

export interface StreamEvent {
    type: StreamEventType;
    content?: string;
    message_id?: number;
    /** On `done`: the user's Stop ended this run (absent when it completed normally). */
    cancelled?: boolean;
    /** On `done`: a model failure ended this run. */
    failed?: boolean;
    /** On a failed `done`: a cause the UI offers a fix for. */
    failure?: 'model_server_down' | string;
    /** On `followup_taken`: the message the running turn took. */
    text?: string;
    /** On `followup_returned`: messages the turn ended without taking. */
    texts?: string[];
    // Agent-specific fields
    status?: string;
    message?: string;
    /** Model phase on a status event: reading, loading_tools, loading_model, reasoning, tool_call, … */
    phase?: string;
    /** On a `recording` phase: which stage, and how it stands. */
    stage?: number;
    state?: number;
    percent?: number;
    eta_seconds?: number;
    /** On a `recording_found` phase: one section's results, while the rest are read. */
    section?: number;
    sections?: number;
    start?: string;
    end?: string;
    items?: FoundItem[];
    /** Drop the answer text streamed since the last tool. On `thinking`: it was this reasoning.
     *  On `status`: the reply was cut off (the model server stopped) and starts over. */
    retracts_answer?: boolean;
    step?: number;
    total?: number;
    tool?: string;
    summary?: string;
    success?: boolean;
    /** On a `tool_result`: no answer came in time, so a "denied" status is not the user's no. */
    timed_out?: boolean;
    steps?: string[];
    /** One plain line per plan step, parallel to `steps` (for plan events). */
    step_narrations?: string[];
    current_step?: number;
    /** What a tool call does, in plain words (for tool_start and tool_args). */
    narration?: string;
    /** The skill a turn runs on (skill_use). */
    skill?: string;
    /** How that skill came to be loaded (skill_use): 'model', 'auto' or 'loaded'. */
    how?: 'model' | 'auto' | 'loaded';
    /** Every skill now loaded in the chat (skill_use). */
    loaded?: string[];
    title?: string;
    detail?: string;
    args?: Record<string, unknown>;
    model?: string;
    elapsed?: number;
    tools_used?: number;
    /** Inference stats from the LLM backend (attached to done events). */
    stats?: InferenceStats;
    /** Per-turn sources, files and data flow (attached to done events). */
    turn_summary?: TurnSummary;
    /** Library documents the agent indexed and attached to the chat (done events). */
    documents_added?: Document[];
    /** Notices explaining files that were kept or could not be added (done events). */
    library_notices?: string[];
    /** MCP server statuses (for mcp_status events). */
    servers?: MCPServerStatus[];
    /** Structured command output (for tool_result of run_shell_command). */
    command_output?: {
        command: string;
        stdout: string;
        stderr: string;
        return_code: number;
        expected_exit?: string | null;
        cwd?: string;
        duration_seconds?: number;
        truncated?: boolean;
    };
    /** Agent ID of the newly created agent (for agent_created events). */
    agent_id?: string;
    /** Confirmation ID (for permission_request events). */
    confirm_id?: string;
    /** What "always allow" would grant for this call; absent means it is not offered. */
    always_scope?: string;
    /** `always_scope` in plain words, e.g. "editing notes.txt" (for permission_request events). */
    always_label?: string;
    /** The tool in plain words, e.g. "writing file" (for permission_request events). */
    tool_label?: string;
    /** What the call does: read, write, execute, or destructive (for permission_request events). */
    risk?: string;
    /** Machine tool name a confirmation is about (for needs_confirmation events). */
    action?: string;
    /** Question id to echo back on POST /chat/user-input (for needs_input events). */
    request_id?: string;
    /** The question text (for needs_input events). */
    question?: string;
    /** 2-4 mutually-exclusive answer options (for needs_input events). */
    options?: Array<{ value: string; label: string; description: string }>;
    /** Whether a free-text answer is also accepted (for needs_input events). */
    allow_free_text?: boolean;
    /** Whether the answer must never be echoed into visible history (for needs_input events). */
    sensitive?: boolean;
    /** Timeout in seconds (for tool_confirm events). */
    timeout_seconds?: number;
    /** MCP server name (for tool_start of MCP tools). */
    mcp_server?: string;
    /** The connector's display name (for tool_start of MCP tools). */
    mcp_server_label?: string;
    /** The server's own tool name (for tool_start of MCP tools). */
    mcp_tool?: string;
    /** The text an MCP tool returned (for tool_result of MCP tools). */
    mcp_output?: McpOutput;
    /** Tool call latency in milliseconds (for tool_result). */
    latency_ms?: number;
    /** agent_error the agent recovers from by retrying, rather than a turn-ending one. */
    recoverable?: boolean;
    /** Governance decision (for policy_alert). */
    decision?: string;
    /** Governance policy reason (for policy_alert). */
    reason?: string;
    /** Governance rule IDs (for policy_alert). */
    rule_ids?: string[];
    /** Governance policy version (for policy_alert). */
    policy_version?: string;
    /** Governance receipt ID (for policy_alert). */
    receipt_id?: string;
    /** Structured result data (for tool_result with search results, file lists, etc.). */
    result_data?: {
        type: string;
        count?: number;
        source_files?: string[];
        chunks?: Array<{
            id: number;
            source?: string;
            sourcePath?: string;
            page?: number | null;
            score?: number | null;
            preview: string;
            content: string;
        }>;
        files?: Array<Record<string, unknown>>;
        total?: number;
    };
    /**
     * Card render key (issue #2108, additive on `tool_result`). Non-empty
     * string means the frontend should mount a registered card via
     * RenderCard against `data`. Absent on every other event type.
     */
    render?: string;
    /** Card payload for `render` (issue #2108, additive on `tool_result`). */
    data?: unknown;
}
