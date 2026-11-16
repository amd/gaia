// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** How an assistant reply shows it failed. Pure, so the phone app can import it. */

/** Detect if message content looks like an error. */
export function isErrorContent(content: string): boolean {
    if (!content) return false;
    const lower = content.toLowerCase();
    return (
        lower.startsWith('error:') ||
        lower.startsWith('error -') ||
        lower.includes('traceback (most recent') ||
        lower.includes("object has no attribute") ||
        lower.includes('is lemonade server running') ||
        lower.includes('connection refused') ||
        lower.includes('failed to fetch')
    );
}

const squash = (text: string) => text.replace(/\s+/g, ' ').trim();

/**
 * Drop an error step the answer is, or ends with, word for word. A run that
 * fails before answering saves its error as the whole answer (`echoed`); one
 * that dies mid-answer saves what it wrote, then the error, which then reads
 * below that text rather than above it. An answer that only quotes a tool's
 * error keeps the step, as does one ending with an error that isn't the
 * turn's last step.
 */
export function dropErrorsEchoedInAnswer<S extends { type: string; detail?: string }>(
    steps: S[],
    content: string,
): { steps: S[]; echoed: boolean; endedBy: string | null } {
    const answer = squash(content);
    if (!answer) return { steps, echoed: false, endedBy: null };
    const tail = content.trimEnd();
    const last = steps[steps.length - 1];
    let echoed = false;
    let endedBy: string | null = null;
    const kept = steps.filter((s) => {
        if (s.type !== 'error' || !s.detail?.trim()) return true;
        if (squash(s.detail) === answer) {
            echoed = true;
            return false;
        }
        // The server appends the error that ended the turn as its own paragraph.
        if (s !== last || !tail.endsWith(`\n\n${s.detail.trim()}`)) return true;
        endedBy = s.detail.trim();
        return false;
    });
    return { steps: kept, echoed, endedBy };
}
