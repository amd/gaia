// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { ApiError, StreamDroppedError, StreamTimeoutError } from '../../services/api';
import { classifyChatError, mayStillFinish } from '../streamErrors';
import { translator } from '../../i18n/useT';

const kind = (err: Error) => classifyChatError(err).kind;

describe('classifyChatError (#599)', () => {
    it.each([
        ['request (9000 tokens) exceeds the available context size (4096 tokens)', 'context'],
        ['Context window exceeded for model', 'context'],
        ['maximum context length is 32768 tokens', 'context'],
        ['Too many tokens in prompt', 'context'],
        ['Lemonade Server not reachable at http://localhost:13305', 'model_server'],
        ['LLM returned no choices', 'model_server'],
        ['Could not get response from model', 'model_server'],
        ['Failed to fetch', 'connection'],
        ['NetworkError when attempting to fetch resource.', 'connection'],
        ['The operation timed out', 'timeout'],
        ['ReadTimeout', 'timeout'],
        ["'NoneType' object has no attribute 'get'", 'unknown'],
        ['', 'unknown'],
    ])('%s → %s', (message, expected) => {
        expect(kind(new Error(message))).toBe(expected);
    });

    it('a context overflow reported by Lemonade is a context problem, not a server one', () => {
        expect(kind(new Error('Lemonade: request exceeds the available context size'))).toBe('context');
    });

    it('a TypeError is a lost connection whatever it says', () => {
        expect(kind(new TypeError('Load failed'))).toBe('connection');
    });

    it('does not mistake words that merely contain a pattern', () => {
        // "fetched" / "LLMs" / "contextual" are not failures of their own.
        expect(kind(new Error('The file was fetched but parsing failed'))).toBe('unknown');
        expect(kind(new Error('contextual parsing failed'))).toBe('unknown');
    });

    it('keeps the stream-specific wording for errors the client can name', () => {
        const { kind: k, text } = classifyChatError(new StreamTimeoutError('send', 30_000));
        expect(k).toBe('stream');
        expect(text).toContain('within 30 seconds');
        expect(classifyChatError(new ApiError('busy', 429, '', null)).kind).toBe('stream');
    });

    it('never shows the raw error text', () => {
        const raw = "Traceback (most recent call last): KeyError: 'secret_field' at C:\\Users\\me\\x.py";
        for (const message of [raw, `Lemonade ${raw}`, `timeout ${raw}`, `fetch ${raw}`, `context window ${raw}`]) {
            const { text } = classifyChatError(new Error(message));
            expect(text).not.toContain('secret_field');
            expect(text).not.toContain('Traceback');
            expect(text.startsWith('Error:')).toBe(false);
        }
    });

    it('speaks the chosen language', () => {
        const { kind: k, text } = classifyChatError(new Error('Failed to fetch'), translator('ar'));
        expect(k).toBe('connection');
        expect(text).toContain('انقطع الاتصال');
    });

    it('says GAIA stopped, and offers a retry, when the last reconnect could not reach it (#6588)', () => {
        const err = new StreamDroppedError(new TypeError('Failed to fetch'), true);
        const { kind: k, text } = classifyChatError(err);
        expect(k).toBe('stream');
        expect(text).toContain('GAIA stopped before it finished this answer');
        expect(text).not.toContain('may still finish');
        expect(mayStillFinish(err)).toBe(false);
        expect(classifyChatError(err, translator('ar')).text).toContain('توقّف GAIA');
    });

    it('keeps "may still finish" when GAIA answered the reconnect (#6588)', () => {
        const err = new StreamDroppedError(new TypeError('network error'));
        expect(classifyChatError(err).text).toContain('The answer may still finish');
        expect(mayStillFinish(err)).toBe(true);
        expect(classifyChatError(err, translator('ar')).text).toContain('قد تكتمل الإجابة');
    });

    it('every kind tells the user what to do next', () => {
        for (const message of ['context size exceeded', 'Lemonade down', 'Failed to fetch', 'timed out', 'boom']) {
            expect(classifyChatError(new Error(message)).text).toMatch(/try again|ask again|Settings → Model|Start a new chat/i);
        }
    });
});
