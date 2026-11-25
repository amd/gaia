// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { beforeEach, describe, expect, it } from 'vitest';
import { followSlideEvent, settleSlideDraft, SLIDE_PREVIEW_ENABLED, useSlideDraftStore } from '../slideDraftStore';
import type { StreamEvent } from '../../types';

const TOOL = 'slide-decks/build_deck_from_markdown';
const MD = '## Ferry crossings rose\n- 1,180 a week\n';
const draft = () => useSlideDraftStore.getState().drafts.s1;
const send = (event: Record<string, unknown>) => followSlideEvent('s1', event as unknown as StreamEvent);
const build = () => send({ type: 'tool_args', tool: TOOL, args: { markdown: MD } });

describe('slideDraftStore', () => {
    beforeEach(() => useSlideDraftStore.setState({ drafts: {} }));

    it('is switched off for now', () => {
        expect(SLIDE_PREVIEW_ENABLED).toBe(false);
    });

    it('finishes on the in-process result: tool, success and path at the top level', () => {
        build();
        expect(draft().status).toBe('building');
        send({ type: 'tool_result', tool: TOOL, success: true, summary: 'ok', path: 'C:/decks/ferry.pptx' });
        expect(draft()).toMatchObject({ status: 'done', path: 'C:/decks/ferry.pptx', markdown: MD });
    });

    it('finishes on the relayed result: success and path under data', () => {
        build();
        send({ type: 'tool_result', tool: TOOL, data: { success: true, path: 'C:/decks/ferry.pptx' }, preview: 'ok' });
        expect(draft()).toMatchObject({ status: 'done', path: 'C:/decks/ferry.pptx' });
    });

    it('reads a failed build from either shape as a retry', () => {
        build();
        send({ type: 'tool_result', tool: TOOL, success: false, summary: 'no source' });
        expect(draft()).toMatchObject({ status: 'retrying', error: 'no source' });
        build();
        send({ type: 'tool_result', tool: TOOL, data: { success: false, summary: 'still none' } });
        expect(draft()).toMatchObject({ status: 'retrying', error: 'still none' });
    });

    it('takes an unnamed result after a build call as that build, and no other', () => {
        send({ type: 'tool_args', tool: 'load_skill', args: { name: 'slide-decks' } });
        send({ type: 'tool_result', success: true, path: 'C:/notes.txt' });
        expect(draft().status).toBe('preparing');
        build();
        send({ type: 'tool_result', success: true, path: 'C:/decks/ferry.pptx' });
        expect(draft()).toMatchObject({ status: 'done', path: 'C:/decks/ferry.pptx' });
    });

    it("leaves another tool's result alone", () => {
        build();
        send({ type: 'tool_result', tool: 'read_file', success: true, path: 'C:/notes.txt' });
        expect(draft().status).toBe('building');
    });

    it('settles a draft still in progress when the turn ends', () => {
        build();
        send({ type: 'tool_result', tool: TOOL, success: false, summary: 'no source' });
        settleSlideDraft('s1');
        expect(draft()).toMatchObject({ status: 'failed', error: 'no source', markdown: MD });
    });

    it('keeps a finished draft as it is when the turn ends, and ignores a chat with none', () => {
        build();
        send({ type: 'tool_result', tool: TOOL, success: true, path: 'C:/decks/ferry.pptx' });
        settleSlideDraft('s1');
        settleSlideDraft('other');
        expect(draft().status).toBe('done');
        expect(useSlideDraftStore.getState().drafts.other).toBeUndefined();
    });

    it('swallows a draft event whose text is not a string without drawing it', () => {
        expect(send({ type: 'draft', tool: TOOL, text: { partial: true } })).toBe(true);
        expect(draft()).toBeUndefined();
        expect(send({ type: 'draft', tool: TOOL, text: MD })).toBe(true);
        expect(draft()).toMatchObject({ status: 'writing', markdown: MD });
    });
});
