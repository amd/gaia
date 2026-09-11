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
