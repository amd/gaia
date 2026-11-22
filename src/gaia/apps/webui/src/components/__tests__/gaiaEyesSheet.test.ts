// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// @vitest-environment node

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import committedMeta from '../gaiaEyesSheet.ts?raw';
import eyesCss from '../GaiaEyes.css?raw';
import { EYES_ANIMATIONS, EYES_CELL, EYES_SHEET_HEIGHT, EYES_SHEET_WIDTH, EYES_VARIANTS } from '../gaiaEyesSheet';
import { EYES_MOODS, MATCHED_MOODS, asEyesMood } from '../../utils/gaiaEyes';
import { REGENERATE_HINT, SHEET_PATH, buildSheet, decodePng, encodePng, metaSource } from '../../../scripts/gen-gaia-eyes.mjs';

const stale = `GAIA's eye sprite is stale. Regenerate it: ${REGENERATE_HINT}`;

describe("GAIA's eye sprite sheet", () => {
    const sheet = buildSheet();

    it('gaia-eyes.png holds exactly the frames the script draws', () => {
        const committed = decodePng(readFileSync(SHEET_PATH));
        expect([committed.width, committed.height], stale).toEqual([sheet.width, sheet.height]);
        expect(Buffer.compare(Buffer.from(committed.pixels), Buffer.from(sheet.pixels)) === 0, stale).toBe(true);
    });

    it('gaiaEyesSheet.ts matches a fresh generation', () => {
        expect(committedMeta.replace(/\r\n/g, '\n') === metaSource(sheet), stale).toBe(true);
    });

    it('survives a round trip through its own PNG encoder', () => {
        const back = decodePng(encodePng(sheet));
        expect(Buffer.compare(Buffer.from(back.pixels), Buffer.from(sheet.pixels))).toBe(0);
    });

    it('fits every animation inside the sheet, with a real still for reduced motion', () => {
        expect(EYES_SHEET_HEIGHT).toBe(Object.keys(EYES_ANIMATIONS).length * EYES_CELL);
        for (const [name, anim] of Object.entries(EYES_ANIMATIONS)) {
            expect(anim.frames * EYES_CELL, name).toBeLessThanOrEqual(EYES_SHEET_WIDTH);
            expect(anim.pose, name).toBeLessThan(anim.frames);
        }
    });

    it('draws something in every frame, so the eyes never vanish mid-animation', () => {
        for (const [name, anim] of Object.entries(EYES_ANIMATIONS)) {
            for (let frame = 0; frame < anim.frames; frame++) {
                let lit = 0;
                for (let y = 0; y < EYES_CELL; y++) {
                    for (let x = 0; x < EYES_CELL; x++) {
                        lit += sheet.pixels[(anim.row * EYES_CELL + y) * sheet.width + frame * EYES_CELL + x];
                    }
                }
                expect(lit, `${name} frame ${frame}`).toBeGreaterThan(0);
            }
        }
    });

    it('loops only the states where GAIA is working', () => {
        const loops = Object.entries(EYES_VARIANTS)
            .filter(([, names]) => names.some((name) => EYES_ANIMATIONS[name].loop))
            .map(([state]) => state);
        expect(loops).toEqual(['think', 'read', 'tool']);
        for (const names of Object.values(EYES_VARIANTS)) {
            expect(new Set(names.map((name) => EYES_ANIMATIONS[name].loop)).size).toBe(1);
        }
    });

    it('files every animation under exactly one state', () => {
        expect(Object.values(EYES_VARIANTS).flat().sort()).toEqual(Object.keys(EYES_ANIMATIONS).sort());
    });

    it('gives the long-running states more than one variation, so a wait does not repeat', () => {
        for (const state of ['think', 'read', 'tool'] as const) {
            expect(EYES_VARIANTS[state].length, state).toBeGreaterThan(1);
        }
    });

    it('mirrors the eyes in right-to-left layouts, so they read the way the text runs', () => {
        expect(eyesCss).toMatch(/:root\[dir="rtl"\] \.gaia-eyes\s*\{[^}]*scale:\s*-1 1/);
    });

    it('can reach every mood it draws from a message', () => {
        const drawn = Object.keys(EYES_VARIANTS).filter((state) => !['think', 'read', 'tool', 'wait', 'brain'].includes(state));
        expect([...MATCHED_MOODS].sort()).toEqual(drawn.sort());
    });

    it('draws exactly the moods the agent lets GAIA name for her own reply', () => {
        const source = readFileSync(new URL('../../../../../agents/base/mood.py', import.meta.url), 'utf8');
        const list = /^MOODS: Tuple\[str, \.\.\.\] = \(([^)]*)\)/m.exec(source);
        expect(list, 'MOODS in src/gaia/agents/base/mood.py').not.toBeNull();
        const named = [...list![1].matchAll(/"([a-z]+)"/g)].map((m) => m[1]);
        expect(named.sort()).toEqual([...EYES_MOODS].sort());
    });

    it('accepts only a mood it can draw', () => {
        expect(asEyesMood('proud')).toBe('proud');
        expect(asEyesMood('think')).toBeNull();
        expect(asEyesMood('calm')).toBeNull();
        expect(asEyesMood(undefined)).toBeNull();
    });
});
