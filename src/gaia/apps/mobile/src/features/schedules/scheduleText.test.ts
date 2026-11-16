// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { tEn, translator } from '@webui/i18n/translator';

import { composeDateLocale, describeSchedule, formatNextRun, formatTime, isCountdownRange } from './scheduleText';

const NOW = new Date('2026-11-14T12:00:00Z');
const at = (seconds: number) => new Date(NOW.getTime() + seconds * 1000).toISOString();

describe('formatTime', () => {
    it('says never without a time', () => {
        expect(formatTime(null, tEn.bare, NOW)).toBe('never');
    });

    it('counts a future time up to days', () => {
        expect(formatTime(at(30), tEn.bare, NOW)).toBe('in 30s');
        expect(formatTime(at(5 * 60), tEn.bare, NOW)).toBe('in 5m');
        expect(formatTime(at(3 * 3600), tEn.bare, NOW)).toBe('in 3h');
        expect(formatTime(at(2 * 86400), tEn.bare, NOW)).toBe('in 2d');
    });

    it('counts a past time as ago, then falls back to a date', () => {
        expect(formatTime(at(-10), tEn.bare, NOW)).toBe('just now');
        expect(formatTime(at(-5 * 60), tEn.bare, NOW)).toBe('5m ago');
        expect(formatTime(at(-3 * 3600), tEn.bare, NOW)).toBe('3h ago');
        expect(formatTime(at(-3 * 86400), tEn.bare, NOW)).not.toMatch(/ago/);
    });
});

describe('formatNextRun', () => {
    it('adds the clock time to a future run only', () => {
        expect(formatNextRun(at(600), tEn.bare, NOW)).toMatch(/\(in 10m\)$/);
        expect(formatNextRun(at(-600), tEn.bare, NOW)).toBe('10m ago');
    });
});

describe('isCountdownRange', () => {
    it('is true only in the last minute before a run', () => {
        expect(isCountdownRange(at(30), NOW)).toBe(true);
        expect(isCountdownRange(at(90), NOW)).toBe(false);
        expect(isCountdownRange(at(-5), NOW)).toBe(false);
        expect(isCountdownRange(null, NOW)).toBe(false);
    });
});

describe('describeSchedule', () => {
    const none = { count: null, unit: null, days: null, time: null, start_hour: null, end_hour: null } as const;

    it('words an interval, with its days', () => {
        expect(describeSchedule({ ...none, kind: 'every', count: 30, unit: 'minute' }, tEn)).toBe('Every 30 minutes');
        expect(describeSchedule({ ...none, kind: 'every', count: 2, unit: 'hour', days: [0, 1, 2, 3, 4] }, tEn)).toBe(
            'Every 2 hours on weekdays',
        );
    });

    it('words named days as Weekdays or Daily', () => {
        expect(describeSchedule({ ...none, kind: 'days', days: [0, 1, 2, 3, 4], time: '08:00' }, tEn)).toMatch(/^Weekdays at /);
        expect(describeSchedule({ ...none, kind: 'daily', time: '21:00' }, tEn)).toMatch(/^Daily at /);
    });

    it('returns null for a shape it cannot word, so the English description shows', () => {
        expect(describeSchedule({ ...none, kind: 'window' }, tEn)).toBeNull();
        expect(describeSchedule(null, tEn)).toBeNull();
    });
});

describe('composeDateLocale', () => {
    it('writes the English half of an Arabic line in English, not the phone region', () => {
        expect(composeDateLocale('en', translator('ar'))).toBe('en');
        expect(composeDateLocale('ar', translator('ar'))).toBe('ar-u-nu-latn');
        expect(composeDateLocale('en', tEn)).toBeUndefined();
    });
});
