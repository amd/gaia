// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Reads a voice turn's ordinary chat stream and pulls out what is spoken.
 *
 * The answer opens with `<spoken>…</spoken>`; text written before a tool call is
 * narration, so every `commentary`, `tool_start` or retracted answer starts the
 * split over. The chat view keeps rendering the stream as usual; this only
 * listens alongside it.
 */

import type { StreamCallbacks } from '../services/api';
import type { AgentStep, StreamEvent } from '../types';
import { agentEventToStep } from '../utils/streamSteps';
import { SpokenSplitter, splitSpoken } from './spoken';

export interface TurnOutcome {
    /** The spoken summary as written, whitespace collapsed. */
    spoken: string;
    /** The answer had no spoken block. */
    missing: boolean;
    /** The turn ended in an error instead of an answer. */
    failed: boolean;
    /** The user stopped it. */
    stopped: boolean;
}

export interface TurnTapOutput {
    /** New spoken text, as it streams. */
    spokenPiece: (text: string) => void;
    /** The spoken block closed; nothing more will be added to it. */
    spokenComplete: () => void;
    /** The line the model wrote before a tool call. */
    statusLine: (text: string) => void;
    toolStarted: (step: AgentStep) => void;
    toolFinished: (result: string, ok: boolean) => void;
    /** The turn is waiting on a permission prompt or a question card. */
    needsYou: (kind: 'permission' | 'question') => void;
    /** The answer's detail so far, without the spoken block. */
    detail: (text: string) => void;
    finished: (outcome: TurnOutcome) => void;
}

export class VoiceTurnTap {
    private splitter = new SpokenSplitter();
    /** Answer text fed since the last reset. */
    private fed = '';
    private detailText = '';
    private spokenSent = false;
    private completed = false;
    private ended = false;
    private stepIds = { current: 0 };

    constructor(private out: TurnTapOutput) {}

    private restart(): void {
        this.splitter.reset();
        this.fed = '';
        this.detailText = '';
        this.spokenSent = false;
        this.completed = false;
        this.out.detail('');
    }

    private feed(text: string): void {
        this.fed += text;
        let detailGrew = false;
        for (const [channel, piece] of this.splitter.feed(text)) {
            if (channel === 'spoken') {
                this.spokenSent = true;
                this.out.spokenPiece(piece);
            } else {
                this.detailText += piece;
                detailGrew = true;
            }
        }
        if (this.spokenSent && !this.splitter.open && !this.completed) {
            this.completed = true;
            this.out.spokenComplete();
        }
        if (detailGrew) this.out.detail(this.detailText);
    }

    chunk(event: StreamEvent): void {
        if (this.ended) return;
        const content = event.content || '';
        if (!content) return;
        if (event.type !== 'answer') {
            this.feed(content);
            return;
        }
        // `answer` carries the whole final text, not a delta.
        if (content.startsWith(this.fed)) {
            this.feed(content.slice(this.fed.length));
            return;
        }
        const split = splitSpoken(content);
        this.fed = content;
        this.detailText = split.detail;
        this.out.detail(split.detail);
        if (!this.spokenSent && split.spoken) {
            this.spokenSent = true;
            this.out.spokenPiece(split.spoken);
        }
    }

    agentEvent(event: StreamEvent): void {
        if (this.ended) return;
        if (event.type === 'commentary') {
            this.restart();
            const line = (event.content || '').trim();
            if (line) this.out.statusLine(line);
            return;
        }
        if (event.type === 'tool_start') {
            if (this.fed) this.restart();
            const step = agentEventToStep(event, this.stepIds);
            if (step) this.out.toolStarted(step);
            return;
        }
        if (event.type === 'tool_result') {
            this.out.toolFinished(event.summary || event.title || '', event.success !== false);
            return;
        }
        if (event.type === 'tool_end') {
            this.out.toolFinished('', event.success !== false);
            return;
        }
        if (event.type === 'permission_request' || event.type === 'needs_input') {
            this.out.needsYou(event.type === 'permission_request' ? 'permission' : 'question');
            return;
        }
        if (event.retracts_answer) this.restart();
    }

    done(event: StreamEvent): void {
        if (this.ended) return;
        this.ended = true;
        for (const [channel, piece] of this.splitter.finish()) {
            if (channel === 'spoken') {
                this.spokenSent = true;
                this.out.spokenPiece(piece);
            } else {
                this.detailText += piece;
            }
        }
        this.out.detail(this.detailText.trim());
        if (this.spokenSent && !this.completed) {
            this.completed = true;
            this.out.spokenComplete();
        }
        this.out.finished({
            spoken: this.splitter.spokenText,
            missing: this.splitter.missing,
            failed: false,
            stopped: event.cancelled === true,
        });
    }

    error(): void {
        if (this.ended) return;
        this.ended = true;
        this.out.finished({ spoken: this.splitter.spokenText, missing: true, failed: true, stopped: false });
    }
}

/** The chat view's own callbacks, with the tap listening alongside. */
export function withVoiceTap(callbacks: StreamCallbacks, tap: VoiceTurnTap): StreamCallbacks {
    return {
        ...callbacks,
        onChunk: (event) => { callbacks.onChunk(event); tap.chunk(event); },
        onAgentEvent: (event) => { callbacks.onAgentEvent(event); tap.agentEvent(event); },
        onDone: (event) => { callbacks.onDone(event); tap.done(event); },
        onError: (err) => { callbacks.onError(err); tap.error(); },
    };
}
