// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { AgentInfo } from '../types';

/** The one agent the Agent UI talks to; every chat it creates runs it. */
export const FLAGSHIP_AGENT_ID = 'gaia';

/** Whether the backend's agent list carries the flagship. */
export function hasFlagshipAgent(agents: AgentInfo[]): boolean {
    return agents.some((a) => a.id === FLAGSHIP_AGENT_ID);
}

/** The 422 a new chat gets when the flagship is not installed (sessions.py `_reject_unknown_agent_type`). */
export function isFlagshipMissingError(err: unknown): boolean {
    const e = err as { httpStatus?: unknown; detail?: unknown; message?: unknown } | null;
    if (e?.httpStatus !== 422) return false;
    const text = `${typeof e.detail === 'string' ? e.detail : ''} ${typeof e.message === 'string' ? e.message : ''}`;
    return text.includes(`Unknown agent_type '${FLAGSHIP_AGENT_ID}'`);
}
