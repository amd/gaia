// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it, vi } from 'vitest';
import type { StreamEvent } from '../../types';
import { VoiceTurnTap, withVoiceTap, type TurnTapOutput } from '../turnTap';

function tap() {
    const out = {
        spokenPiece: vi.fn(), spokenComplete: vi.fn(), statusLine: vi.fn(),
        toolStarted: vi.fn(), toolFinished: vi.fn(), needsYou: vi.fn(), detail: vi.fn(), finished: vi.fn(),
    } satisfies TurnTapOutput;
    return { t: new VoiceTurnTap(out), out };
}

const chunk = (content: string) => ({ type: 'chunk', content }) as StreamEvent;
const spoken = (out: ReturnType<typeof tap>['out']) => out.spokenPiece.mock.calls.map(([p]) => p).join('');

describe('VoiceTurnTap', () => {
    it('speaks the spoken block as it streams and closes it before the detail', () => {
        const { t, out } = tap();
        for (const c of '<spoken>Four invoices, about 3,400 dollars.</spoken>\n\n| a | b |') t.chunk(chunk(c));
        expect(spoken(out)).toBe('Four invoices, about 3,400 dollars.');
        expect(out.spokenComplete).toHaveBeenCalledOnce();
        expect(out.detail).toHaveBeenLastCalledWith('| a | b |');
        t.done({ type: 'done' } as StreamEvent);
        expect(out.finished).toHaveBeenCalledWith({ spoken: 'Four invoices, about 3,400 dollars.', missing: false, failed: false, stopped: false });
    });

    it('treats text before a tool call as narration, not the answer', () => {
        const { t, out } = tap();
        t.chunk(chunk('Let me look in Downloads.'));
        t.agentEvent({ type: 'commentary', content: 'Let me look in Downloads.' } as StreamEvent);
        t.agentEvent({ type: 'tool_start', tool: 'search_files' } as StreamEvent);
        t.agentEvent({ type: 'tool_result', summary: '4 PDFs found', success: true } as StreamEvent);
        t.chunk(chunk('<spoken>Found four.</spoken> Table.'));
        t.done({ type: 'done' } as StreamEvent);
        expect(out.statusLine).toHaveBeenCalledWith('Let me look in Downloads.');
        expect(out.toolStarted).toHaveBeenCalledWith(expect.objectContaining({ tool: 'search_files' }));
        expect(out.toolFinished).toHaveBeenCalledWith('4 PDFs found', true);
        expect(spoken(out)).toBe('Found four.');
        expect(out.finished).toHaveBeenCalledWith(expect.objectContaining({ missing: false }));
    });

    it('says when the block is missing, and speaks none of the detail', () => {
        const { t, out } = tap();
        t.chunk(chunk('Here is the answer without a summary.'));
        t.done({ type: 'done' } as StreamEvent);
        expect(out.spokenPiece).not.toHaveBeenCalled();
        expect(out.finished).toHaveBeenCalledWith(expect.objectContaining({ missing: true, failed: false }));
    });

    it('starts over when an answer is retracted', () => {
        const { t, out } = tap();
        t.chunk(chunk('half an answer'));
        t.agentEvent({ type: 'status', status: 'warning', retracts_answer: true } as StreamEvent);
        t.chunk(chunk('<spoken>Done.</spoken>'));
        t.done({ type: 'done' } as StreamEvent);
        expect(out.finished).toHaveBeenCalledWith(expect.objectContaining({ spoken: 'Done.', missing: false }));
    });

    it('reads a whole `answer` event after its deltas without repeating them', () => {
        const { t, out } = tap();
        t.chunk(chunk('<spoken>Hi.'));
        t.chunk({ type: 'answer', content: '<spoken>Hi. There.</spoken> More.' } as StreamEvent);
        t.done({ type: 'done' } as StreamEvent);
        expect(spoken(out)).toBe('Hi. There.');
        expect(out.detail).toHaveBeenLastCalledWith('More.');
    });

    it('says when the turn waits on a permission card or a question', () => {
        const { t, out } = tap();
        t.agentEvent({ type: 'permission_request', tool: 'run_shell_command' } as StreamEvent);
        t.agentEvent({ type: 'needs_input' } as StreamEvent);
        expect(out.needsYou).toHaveBeenCalledTimes(2);
    });

    it('reports a stopped turn and a failed one', () => {
        const a = tap();
        a.t.done({ type: 'done', cancelled: true } as StreamEvent);
        expect(a.out.finished).toHaveBeenCalledWith(expect.objectContaining({ stopped: true }));
        const b = tap();
        b.t.error();
        b.t.error();
        expect(b.out.finished).toHaveBeenCalledTimes(1);
        expect(b.out.finished).toHaveBeenCalledWith(expect.objectContaining({ failed: true }));
    });
});

describe('withVoiceTap', () => {
    it('lets the chat view see every event first, then the tap', () => {
        const order: string[] = [];
        const callbacks = {
            onChunk: () => order.push('view:chunk'),
            onAgentEvent: () => order.push('view:event'),
            onDone: () => order.push('view:done'),
            onError: () => order.push('view:error'),
        };
        const { t } = tap();
        vi.spyOn(t, 'chunk').mockImplementation(() => { order.push('tap:chunk'); });
        vi.spyOn(t, 'done').mockImplementation(() => { order.push('tap:done'); });
        const wrapped = withVoiceTap(callbacks, t);
        wrapped.onChunk(chunk('x'));
        wrapped.onDone({ type: 'done' } as StreamEvent);
        expect(order).toEqual(['view:chunk', 'tap:chunk', 'view:done', 'tap:done']);
    });
});
