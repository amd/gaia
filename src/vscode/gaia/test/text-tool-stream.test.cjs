/*
Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { test } = require('node:test');

// Load the compiled production provider and utilities. Only the VSCode output
// objects are substituted; parsing and state transitions execute unchanged.
class LanguageModelTextPart {
    constructor(value) { this.value = value; }
}
class LanguageModelToolCallPart {
    constructor(callId, name, input) { Object.assign(this, { callId, name, input }); }
}
const vscode = { LanguageModelTextPart, LanguageModelToolCallPart };
const modules = new Map();
function loadCompiled(filename) {
    if (modules.has(filename)) { return modules.get(filename).exports; }
    const module = { exports: {} };
    modules.set(filename, module);
    const nativeRequire = createRequire(filename);
    const injectedRequire = (name) => {
        if (name === 'vscode') { return vscode; }
        if (name.startsWith('.')) { return loadCompiled(nativeRequire.resolve(name)); }
        return nativeRequire(name);
    };
    const wrapper = vm.runInThisContext(
        `(function(require, module, exports) {\n${fs.readFileSync(filename, 'utf8')}\n})`,
        { filename },
    );
    wrapper(injectedRequire, module, module.exports);
    return module.exports;
}
const { GaiaChatModelProvider } = loadCompiled(path.resolve(__dirname, '../out/provider.js'));

const BEGIN = '<|tool_call_begin|>';
const ARG = '<|tool_call_argument_begin|>';
const END = '<|tool_call_end|>';
const invocation = `${BEGIN}read_file:0${ARG}{"path":"README.md"}${END}`;

function parse(chunks) {
    const provider = new GaiaChatModelProvider({}, 'parser-regression');
    const parts = [];
    const progress = { report: (part) => parts.push(part) };
    for (const chunk of chunks) { provider.processTextContent(chunk, progress); }
    return {
        text: parts.filter((part) => part instanceof LanguageModelTextPart)
            .map((part) => part.value).join(''),
        calls: parts.filter((part) => part instanceof LanguageModelToolCallPart)
            .map(({ name, input }) => ({ name, input })),
        pending: provider._textToolParserBuffer,
        active: provider._textToolActive,
    };
}

const fixtures = [
    { name: 'single invocation', input: invocation, text: '',
        calls: [{ name: 'read_file', input: { path: 'README.md' } }] },
    { name: 'surrounding prose', input: `Before. ${invocation} After.`, text: 'Before.  After.',
        calls: [{ name: 'read_file', input: { path: 'README.md' } }] },
    { name: 'multiple invocations', input: `${invocation} then ${BEGIN}list_files:1${END} Done.`,
        text: ' then  Done.', calls: [
            { name: 'read_file', input: { path: 'README.md' } },
            { name: 'list_files', input: {} },
        ] },
    { name: 'nested and escaped arguments',
        input: `${BEGIN}write_file${ARG}${JSON.stringify({ path: 'a.txt', content: 'a "quote" < and é', nested: { ok: true } })}${END} Saved.`,
        text: ' Saved.', calls: [{ name: 'write_file', input: {
            path: 'a.txt', content: 'a "quote" < and é', nested: { ok: true },
        } }] },
    { name: 'ordinary angle brackets', input: 'Use <x> and 2 < 3.', text: 'Use <x> and 2 < 3.', calls: [] },
];

for (const fixture of fixtures) {
    const expected = { text: fixture.text, calls: fixture.calls, pending: '', active: undefined };
    test(`${fixture.name}: unsplit`, () => assert.deepEqual(parse([fixture.input]), expected));
    test(`${fixture.name}: every two-chunk boundary`, () => {
        for (let at = 0; at <= fixture.input.length; at++) {
            assert.deepEqual(parse([fixture.input.slice(0, at), fixture.input.slice(at)]), expected, `split at ${at}`);
        }
    });
    test(`${fixture.name}: every fixed chunk width`, () => {
        for (let width = 1; width <= fixture.input.length; width++) {
            const chunks = [];
            for (let at = 0; at < fixture.input.length; at += width) {
                chunks.push(fixture.input.slice(at, at + width));
            }
            assert.deepEqual(parse(chunks), expected, `chunk width ${width}`);
        }
    });
}

test('complete arguments emit before the terminator, exactly once', () => {
    const provider = new GaiaChatModelProvider({}, 'parser-regression');
    const parts = [];
    const progress = { report: (part) => parts.push(part) };
    provider.processTextContent(`${BEGIN}read_file${ARG}{"path":"README.md"}`, progress);
    assert.equal(parts.filter((part) => part instanceof LanguageModelToolCallPart).length, 1);
    for (const char of END) { provider.processTextContent(char, progress); }
    assert.equal(parts.filter((part) => part instanceof LanguageModelToolCallPart).length, 1);
    assert.equal(provider._textToolActive, undefined);
});

test('SSE content deltas dispatch a split invocation and subsequent answer', async (t) => {
    t.mock.method(console, 'log', () => {});
    const provider = new GaiaChatModelProvider({}, 'parser-regression');
    const parts = [];
    const progress = { report: (part) => parts.push(part) };
    const chunks = ['Before. <|tool_', 'call_begin|>read_',
        'file:0<|tool_call_argument_', 'begin|>{"path":"README.md"}<|tool_call_',
        'end|> After.'];
    for (const content of chunks) {
        await provider.processDelta({ choices: [{ delta: { content } }] }, progress);
    }
    await provider.processDelta({ choices: [{ delta: {}, finish_reason: 'stop' }] }, progress);
    assert.deepEqual(parts.filter((part) => part instanceof LanguageModelToolCallPart)
        .map(({ name, input }) => ({ name, input })),
    [{ name: 'read_file', input: { path: 'README.md' } }]);
    assert.equal(parts.filter((part) => part instanceof LanguageModelTextPart)
        .map((part) => part.value).join(''), 'Before.  After.');
});

async function streamResponse(chunks, ending, {
    token = { isCancellationRequested: false }, fail = false, keepOpen = false, cancelError = false,
} = {}) {
    const provider = new GaiaChatModelProvider({}, 'stream-completion-regression');
    const parts = [];
    const frames = chunks.map((content) => `data: ${JSON.stringify({ choices: [{ delta: { content } }] })}\n\n`);
    if (ending === 'stop') {
        frames.push('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n');
    }
    if (ending === 'done') { frames.push('data: [DONE]\n\n'); }
    let index = 0;
    let cancelled = false;
    const body = new ReadableStream({
        pull(controller) {
            if (index < frames.length) { controller.enqueue(new TextEncoder().encode(frames[index++])); }
            else if (fail) { controller.error(new Error('stream disconnected')); }
            else if (keepOpen) { return new Promise(() => {}); }
            else { controller.close(); }
        },
        cancel() {
            cancelled = true;
            if (cancelError) { throw new Error('cleanup failed'); }
        },
    });
    let error;
    try {
        await provider.processStreamingResponse(body, { report: (part) => parts.push(part) }, token);
    } catch (caught) { error = caught; }
    assert.equal(body.locked, false);
    assert.equal(provider._textToolParserBuffer, '');
    assert.equal(provider._controlTokenBuffer, '');
    assert.equal(provider._textToolActive, undefined);
    return {
        text: parts.filter((part) => part instanceof LanguageModelTextPart).map((part) => part.value).join(''),
        calls: parts.filter((part) => part instanceof LanguageModelToolCallPart),
        error,
        cancelled,
    };
}

for (const ending of ['eof', 'stop', 'done']) {
    test(`full SSE ${ending} completion preserves visible partial marker suffixes`, async (t) => {
        t.mock.method(console, 'log', () => {});
        for (const chunks of [['Compare a ', '<'], ['done <|tool'], ['literal <function=x'], ['<function=x', '<|tool']]) {
            const result = await streamResponse(chunks, ending);
            assert.equal(result.error, undefined);
            assert.equal(result.text, chunks.join(''));
            assert.equal(result.calls.length, 0);
        }
    });
}

test('full SSE completion does not expose active tool arguments or terminator prefixes', async (t) => {
    t.mock.method(console, 'log', () => {});
    for (const ending of ['eof', 'done']) {
        for (const args of ['{"path":"README.md"}', '{"path":']) {
            const result = await streamResponse([`${BEGIN}read_file${ARG}${args}<|tool_call_`], ending);
            assert.equal(result.error, undefined);
            assert.equal(result.text, '');
            assert.equal(result.calls.length, args.endsWith('}') ? 1 : 0);
        }
    }
});

test('full SSE response emits tool call and final partial text exactly once', async (t) => {
    t.mock.method(console, 'log', () => {});
    const result = await streamResponse([invocation, ' Compare <'], 'done');
    assert.equal(result.error, undefined);
    assert.equal(result.text, ' Compare <');
    assert.equal(result.calls.length, 1);
});

test('stream errors do not flush a pending suffix as successful completion', async (t) => {
    t.mock.method(console, 'log', () => {});
    const result = await streamResponse(['Compare <'], 'eof', { fail: true });
    assert.match(result.error.message, /stream disconnected/);
    assert.equal(result.text, 'Compare ');
});

test('[DONE] completes without waiting for a later transport error', async (t) => {
    t.mock.method(console, 'log', () => {});
    t.mock.method(console, 'error', () => {});
    const result = await streamResponse(['Compare <'], 'done', { fail: true });
    assert.equal(result.error, undefined);
    assert.equal(result.text, 'Compare <');
});

test('[DONE] cancels the undrained response body', async (t) => {
    t.mock.method(console, 'log', () => {});
    const result = await streamResponse(['Compare <'], 'done', { keepOpen: true });
    assert.equal(result.error, undefined);
    assert.equal(result.text, 'Compare <');
    assert.equal(result.cancelled, true);
});

test('[DONE] reports cleanup errors without failing a completed response', async (t) => {
    t.mock.method(console, 'log', () => {});
    const logged = t.mock.method(console, 'error', () => {});
    const result = await streamResponse(['Compare <'], 'done', { keepOpen: true, cancelError: true });
    assert.equal(result.error, undefined);
    assert.equal(result.text, 'Compare <');
    assert.equal(result.cancelled, true);
    assert.equal(logged.mock.callCount(), 1);
    assert.match(logged.mock.calls[0].arguments[0], /Failed to close completed stream/);
});

test('cancellation cleans up pending suffixes without completion output', async (t) => {
    t.mock.method(console, 'log', () => {});
    let checks = 0;
    const token = { get isCancellationRequested() { return checks++ > 0; } };
    const result = await streamResponse(['Compare <'], 'eof', { token });
    assert.equal(result.error, undefined);
    assert.equal(result.text, 'Compare ');
});
