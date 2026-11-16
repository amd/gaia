// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { CODE_PREVIEW_LINES, STEP_COLLAPSE_CHARS, STEP_COLLAPSE_LINES, countLines } from '@webui/utils/longContent';

/** The most characters of one command's output the list ever renders, expanded or not. */
export const TERMINAL_HARD_CAP_CHARS = 20000;

export interface TerminalWindow {
    /** What to render. */
    text: string;
    /** Longer than the preview, so "Expand" has something to show. */
    collapsible: boolean;
    /** Cut at the hard cap even when expanded. */
    capped: boolean;
}

/** The slice of a command's output to show: a preview while folded, never more than the hard cap. */
export function terminalWindow(output: string, expanded: boolean): TerminalWindow {
    const capped = output.length > TERMINAL_HARD_CAP_CHARS;
    const all = capped ? output.slice(0, TERMINAL_HARD_CAP_CHARS) : output;
    const collapsible = countLines(all) > STEP_COLLAPSE_LINES || all.length > STEP_COLLAPSE_CHARS;
    if (!collapsible || expanded) return { text: all, collapsible, capped };
    const preview = all.split('\n').slice(0, CODE_PREVIEW_LINES).join('\n');
    return { text: preview.slice(0, STEP_COLLAPSE_CHARS), collapsible, capped };
}
