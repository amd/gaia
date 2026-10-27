// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Message } from '../types';

/** Messages as the API sends them, with steps and stats under the names the view reads. */
export function fromApiMessages(raw: unknown[] | undefined): Message[] {
    return (raw || []).map((m: any) => ({
        ...m,
        agentSteps: m.agentSteps || m.agent_steps || undefined,
        stats: m.stats || m.inference_stats || undefined,
        turnSummary: m.turnSummary || m.turn_summary || undefined,
    }));
}
