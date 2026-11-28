// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { AgentStep } from '../types';
import type { AgentCapabilities } from '../types/sessionPanel';

type Program = NonNullable<AgentCapabilities['program']>;

/** Requests in flight, so every answer in a chat shares one. */
const inFlight = new Map<string, Promise<Program | null>>();

function read(sessionId: string): Promise<Program | null> {
    let pending = inFlight.get(sessionId);
    if (!pending) {
        pending = Promise.resolve()
            .then(() => api.getCapabilities(sessionId))
            .then((res) => res?.program ?? null)
            .finally(() => { inFlight.delete(sessionId); });
        inFlight.set(sessionId, pending);
    }
    return pending;
}

/**
 * The program running this chat's agent (`gaiapy` or `gaiacpp`), or null while
 * unknown: asked only when `wanted`, and never guessed when the agent can't say.
 */
export function useSessionProgram(sessionId: string, wanted: boolean): Program | null {
    const [program, setProgram] = useState<Program | null>(null);
    useEffect(() => {
        setProgram(null);
        if (!wanted) return;
        let live = true;
        read(sessionId)
            .then((found) => { if (live) setProgram(found); })
            // A chat whose agent isn't running answers 409; it names no program, so nothing is claimed.
            .catch((err) => log.ui.warn(`Could not tell which agent runs session=${sessionId}`, err));
        return () => { live = false; };
    }, [sessionId, wanted]);
    return program;
}

/** The Python names the UI shows for tools that write files or run commands. */
const CHANGING_TOOLS = new Set(['write_file', 'edit_file', 'run_shell_command']);

/** Whether a turn ran a tool that can change files: one that was declined or failed changed nothing. */
export function ranChangingTool(steps: AgentStep[] | undefined): boolean {
    return (steps ?? []).some((s) => s.type === 'tool' && !!s.tool && CHANGING_TOOLS.has(s.tool)
        && !s.declined && !s.timedOut && !s.refused && s.success !== false);
}
