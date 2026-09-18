/* Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
 * SPDX-License-Identifier: MIT */
const assert = require('node:assert/strict');
const { test } = require('node:test');
const Module = require('node:module');
const commands = new Map();
let input;
let inputOptions;
const vscode = {
  extensions: { getExtension: () => ({ packageJSON: { version: 'test' } }) },
  lm: { registerLanguageModelChatProvider() {} },
  version: 'test',
  workspace: {},
  LanguageModelTextPart: class {},
  commands: { registerCommand: (name, fn) => { commands.set(name, fn); return {}; } },
  window: {
    showInputBox: async (options) => { inputOptions = options; return input; },
    showInformationMessage() {},
  },
};
const originalLoad = Module._load;
Module._load = function(name, parent, main) {
  if (name === 'vscode') return vscode;
  if (name === './utils') return {
    convertMessages: () => [], convertTools: () => ({}), validateRequest() {},
  };
  return originalLoad.call(this, name, parent, main);
};
const { activate } = require('../out/extension');
const { GaiaChatModelProvider } = require('../out/provider');
Module._load = originalLoad;
function storage() {
  const values = new Map();
  return { values, get: async key => values.get(key), store: async (key,value) => values.set(key,value), delete: async key => values.delete(key) };
}

test('API key command stores securely, cancel preserves, and blank clears', async () => {
  const secrets = storage();
  activate({ secrets, subscriptions: [] });
  input = ' test-private-key ';
  await commands.get('gaia.setApiKey')();
  assert.equal(inputOptions.password, true);
  assert.equal(secrets.values.get('gaia.apiKey'), 'test-private-key');
  assert.equal(inputOptions.value, undefined);
  input = undefined;
  await commands.get('gaia.setApiKey')();
  assert.equal(secrets.values.get('gaia.apiKey'), 'test-private-key');
  input = '';
  await commands.get('gaia.setApiKey')();
  assert.equal(secrets.values.has('gaia.apiKey'), false);
});

test('model discovery and chat send the saved API key, keyless stays compatible', async t => {
  const secrets = storage();
  const provider = new GaiaChatModelProvider(secrets, 'test');
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ data: [] }), body: new ReadableStream({ start(controller) { controller.close(); } }) };
  });
  t.mock.method(console, 'log', () => {});
  await provider.fetchModels();
  assert.equal(requests[0].options.headers.Authorization, 'Bearer gaia');
  await secrets.store('gaia.apiKey', 'custom-private-key');
  await provider.fetchModels();
  await provider.provideLanguageModelChatResponse({ id: 'model', maxInputTokens: 1000 }, [], {}, { report() {} }, { isCancellationRequested: false });
  assert.equal(requests[1].options.headers.Authorization, 'Bearer custom-private-key');
  assert.equal(requests[2].options.headers.Authorization, 'Bearer custom-private-key');
  assert.equal(requests[2].options.method, 'POST');
  assert.match(requests[2].url, /\/v1\/chat\/completions$/);
});
