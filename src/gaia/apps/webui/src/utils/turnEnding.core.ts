// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** How a turn ends short of an answer. Pure, so the phone app can import it. */

import type { MessageKey } from '../i18n/registry';
import type { Message } from '../types';
import { CUT_OFF_NOTICE_TEXT } from './constants';

/** How a turn that never finished its answer ended. */
export type TurnEnding = 'stopped' | 'interrupted' | 'failed';

/** How long Stop waits for the server's closing `done` before the client closes the turn itself. */
export const STOP_GRACE_MS = 15_000;

/** What the server persists for a turn stopped before it wrote any text (_empty_answer_outcome). */
export const SERVER_CANCELLED_TEXT = 'Cancelled.';

/** The notices that close a turn cut off before its answer (gaia/ui/restart_recovery.py), and their translations. */
const CUT_OFF_NOTICES: ReadonlyArray<readonly [string, MessageKey]> = [
    [CUT_OFF_NOTICE_TEXT.interrupted, 'msg.cutOff.interrupted'],
    [CUT_OFF_NOTICE_TEXT.cancelled, 'msg.cutOff.cancelled'],
    [CUT_OFF_NOTICE_TEXT.noReply, 'msg.cutOff.noReply'],
    [CUT_OFF_NOTICE_TEXT.scheduleDeleted, 'msg.cutOff.scheduleDeleted'],
    [CUT_OFF_NOTICE_TEXT.schedulePaused, 'msg.cutOff.schedulePaused'],
];

/** restart_recovery.tools_ran_notice, the line the server puts just above a cut-off notice. */
const TOOLS_RAN_RE = /(?:^|\n\n)Before it stopped, it had started these tools: (.+)\.$/;

export interface CutOff {
    /** What the turn streamed before it was cut off. */
    body: string;
    notice: MessageKey;
    tools: string | null;
}

/** A reply the server closed with a cut-off notice, split so the notice shows in the UI's language. */
export function splitCutOff(content: string): CutOff | null {
    let text = content.trim();
    const hit = CUT_OFF_NOTICES.find(([notice]) => text.endsWith(notice));
    if (!hit) return null;
    text = text.slice(0, -hit[0].length).trimEnd();
    const tools = TOOLS_RAN_RE.exec(text);
    if (tools) text = text.slice(0, tools.index).trimEnd();
    return { body: text, notice: hit[1], tools: tools ? tools[1] : null };
}

/** How an assistant turn ended short of an answer, or undefined when it answered. */
export function turnEnding(content: string, stopState: Message['stopState'], failed = false): TurnEnding | undefined {
    if (stopState) return 'stopped';
    if (splitCutOff(content)) return 'interrupted';
    return failed ? 'failed' : undefined;
}
