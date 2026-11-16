// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SetupLogStage, SetupRunStatus } from '@webui/types';

export const LOG_STAGES: SetupLogStage[] = ['check', 'download', 'test'];

const PERCENT = /\d+(\.\d+)?\s*%/;

/** A redrawn progress bar prints a line per redraw: keep only the last of each run; the web UI's `collapseProgress`. */
export function collapseProgress(lines: string[]): string[] {
    const out: string[] = [];
    let last: string | null = null;
    for (const line of lines) {
        const at = line.search(PERCENT);
        const shape = at >= 0 ? line.slice(0, at) : null;
        if (shape !== null && shape === last) out[out.length - 1] = line;
        else out.push(line);
        last = shape;
    }
    return out;
}

/** The stages that have lines, each with what the screen saw before the run and then what the run printed. */
export function logsByStage(
    run: SetupRunStatus | null,
    before: Partial<Record<SetupLogStage, string[]>> = {},
): { stage: SetupLogStage; lines: string[] }[] {
    return LOG_STAGES.map((stage) => ({
        stage,
        lines: collapseProgress([...(before[stage] ?? []), ...(run?.logs?.[stage] ?? [])]),
    })).filter((s) => s.lines.length > 0);
}
