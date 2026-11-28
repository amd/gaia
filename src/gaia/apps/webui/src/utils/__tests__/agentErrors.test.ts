// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';
import { agentErrorParam } from '../agentErrors';
import { enMessages } from '../../i18n/locales/en';
import { stripIsolates, translate, type Param } from '../../i18n/registry';

const START_FAILED = enMessages['agentError.startFailed'];
const said = (code: string, p: Param) => (typeof p === 'object' ? stripIsolates(translate(code, p.ref, p.params)) : p);

describe('agentErrorParam', () => {
    it("words the agent-start error in Arabic, keeping the log folder", () => {
        const p = agentErrorParam(`${START_FAILED} The details are in this log folder: /Users/me/.gaia/agents/gaia/logs`);
        const ar = said('ar', p);
        expect(ar).toContain('تعذّر على GAIA تشغيل وكيله');
        expect(ar).toContain('/Users/me/.gaia/agents/gaia/logs');
        expect(said('en', p)).toBe(`${START_FAILED} The details are in this log folder: /Users/me/.gaia/agents/gaia/logs`);
    });

    it('words it without a log folder too', () => {
        expect(said('en', agentErrorParam(START_FAILED))).toBe(START_FAILED);
        expect(said('ar', agentErrorParam(START_FAILED))).toMatch(/^تعذّر على GAIA/);
    });

    it('leaves an error it has no wording for as sent', () => {
        expect(agentErrorParam('Something else broke.')).toBe('Something else broke.');
    });

    it("matches the backend's own wording, so it never silently falls back to English", () => {
        const profiles = readFileSync(resolve(__dirname, '../../../../../ui/sidecar/profiles.py'), 'utf8');
        const block = /start_failed_message=\(([\s\S]*?)\),/.exec(profiles);
        expect(block).not.toBeNull();
        const python = [...block![1].matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]).join('');
        expect(python).toBe(START_FAILED);
        expect(profiles).toContain('f"{message} The details are in this log folder: {log_dir}"');
    });
});
