// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SetupCatalogEntry, SetupRunStatus, SetupTest } from '@webui/types';

import { expectedTests, rerunOptions, testView, whyKey } from './testView';

const test = (over: Partial<SetupTest>): SetupTest => ({ job: 'chat', subject: 'm1', state: 'failed', error: 'boom', kind: null, seconds: null, ...over });
const entry = (id: string, roles: SetupCatalogEntry['roles']): SetupCatalogEntry => ({ id, roles, support: 'verified' } as SetupCatalogEntry);
const stopped = (tests: SetupTest[], over: Partial<SetupRunStatus> = {}): SetupRunStatus => ({ state: 'failed', tests, ...over });

describe('which explanation a failed test shows', () => {
    it('a model file that did not load names the user\'s own server, unless GAIA can delete it', () => {
        expect(whyKey(test({ kind: 'broken' }), false)).toBe('setup.test.why.brokenYours');
        expect(whyKey(test({ kind: 'broken' }), true)).toBe('setup.test.why.broken');
        expect(testView(stopped([test({ kind: 'broken' })]), [], true).broken).toEqual(['m1']);
        expect(testView(stopped([test({ kind: 'broken' })]), [], false).broken).toEqual([]);
    });

    it('an unknown kind gets the general reason', () => {
        expect(whyKey(test({ kind: null }), true)).toBe('setup.test.why.other');
    });

    it('memory, cancelled and the rest each get their own lead; an extra\'s failure does not count', () => {
        expect(testView(stopped([test({ kind: 'memory' })]), [], true).lead).toBe('setup.test.failedMemory');
        expect(testView(stopped([], { state: 'cancelled' }), [], true).lead).toBe('setup.test.cancelled');
        expect(testView(stopped([test({ kind: 'stuck' })]), [], true).lead).toBe('setup.test.failedHelp');
        const voice = [entry('m1', ['voice'])];
        const view = testView(stopped([test({ job: 'voice', kind: 'memory' })]), voice, true);
        expect(view.lead).toBe('setup.test.failedHelp');
        expect(view.modelAtFault).toBe(false);
    });

    it('a missing assistant is not a model\'s fault, and a failed install says so on its waiting row', () => {
        const view = testView(stopped([test({ job: 'agent', subject: null, kind: 'missing' })], { components: { agent: 'failed' } }), [], true);
        expect(view.modelAtFault).toBe(false);
        expect(view.agentNotInstalled).toBe(true);
    });
});

describe('the progress total', () => {
    it('counts the jobs still waiting, so the bar has a true end', () => {
        const run: SetupRunStatus = { state: 'verifying', tests: [test({ state: 'passed', job: 'server', subject: null })], models: [{ id: 'm1', roles: ['chat', 'embedding'] } as never] };
        expect(expectedTests(run)).toEqual(['server', 'chat', 'embedding', 'agent']);
        const view = testView(run, [], true);
        expect([view.finished, view.total, view.percent]).toEqual([1, 4, 25]);
        expect(view.startingJob).toBe('chat');
    });
});

describe('Test again', () => {
    it('keeps the optional extras the run had', () => {
        const run: SetupRunStatus = { state: 'failed', models: [{ id: 'chat1', roles: ['chat'] }, { id: 'tts', roles: ['voice'] }] as never };
        const catalog = [entry('chat1', ['chat']), { ...entry('tts', ['voice']), support: null }];
        expect(rerunOptions(run, catalog).extraModels).toEqual(['tts']);
    });
});
