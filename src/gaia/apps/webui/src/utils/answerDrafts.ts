// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { AgentStep } from '../types';

/** Below this many word pairs a note is narration ("Reading the file first."), never a draft. */
const MIN_DRAFT_PAIRS = 10;

/** Share of the earlier note's word pairs a later text must repeat for the note to be a draft of it. */
const SAME_ANSWER_OVERLAP = 0.6;

function wordPairs(text: string): Set<string> {
    const words = text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
    const pairs = new Set<string>();
    for (let i = 1; i < words.length; i++) pairs.add(`${words[i - 1]} ${words[i]}`);
    return pairs;
}

function restates(earlier: Set<string>, later: Set<string>): boolean {
    if (earlier.size < MIN_DRAFT_PAIRS || later.size < MIN_DRAFT_PAIRS) return false;
    let shared = 0;
    for (const pair of earlier) if (later.has(pair)) shared++;
    // Of the note, not of the shorter text: a one-line answer must not hide a note's other findings.
    return shared / earlier.size >= SAME_ANSWER_OVERLAP;
}

/**
 * Drop the notes that are earlier drafts of the answer. When GAIA checks its
 * work, the model writes its answer, reads a file back, then writes it again;
 * the first copy arrives as a note before that read. A note is a draft when a
 * later note or the answer (streamed so far, or final) says it again.
 */
export function dropAnswerDrafts(steps: AgentStep[], answer: string): AgentStep[] {
    const notes = steps
        .map((step, index) => ({ step, index }))
        .filter(({ step }) => step.type === 'commentary' && step.detail?.trim());
    if (notes.length === 0) return steps;
    const pairs = notes.map(({ step }) => wordPairs(step.detail ?? ''));
    const answerPairs = wordPairs(answer);
    const drafts = new Set<number>();
    notes.forEach(({ index }, i) => {
        const later = [...pairs.slice(i + 1), answerPairs];
        if (later.some((p) => restates(pairs[i], p))) drafts.add(index);
    });
    return drafts.size === 0 ? steps : steps.filter((_, index) => !drafts.has(index));
}
