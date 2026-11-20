// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * How a schedule reads: when it runs and when it runs next. Kept apart from
 * the Scheduled tasks dialog so the session panel can word a schedule without
 * loading the dialog.
 */

import type { ScheduleDescription } from '../types';
import { isMessageKey, type MessageKey, type Say } from '../i18n/registry';
import { dateLocale, tEn, translator, type Translate } from '../i18n/useT';

/** Format an ISO date string to a relative or short time; `locale` formats dates (undefined: the PC's). */
export function formatTime(iso: string | null, say: Say, now?: Date, locale?: string): string {
    if (!iso) return say('schedules.time.never');
    const d = new Date(iso);
    const current = now || new Date();
    const diffMs = current.getTime() - d.getTime();
    const diffSecs = Math.floor(diffMs / 1000);

    if (diffSecs < 0) {
        // Future time
        const futureSecs = Math.abs(diffSecs);
        if (futureSecs < 60) return say('schedules.time.inSeconds', { n: futureSecs });
        const futureMins = Math.floor(futureSecs / 60);
        if (futureMins < 60) return say('schedules.time.inMinutes', { n: futureMins });
        const hrs = Math.floor(futureMins / 60);
        if (hrs < 24) return say('schedules.time.inHours', { n: hrs });
        return say('schedules.time.inDays', { n: Math.floor(hrs / 24) });
    }
    if (diffSecs < 60) return say('time.justNow');
    const mins = Math.floor(diffSecs / 60);
    if (mins < 60) return say('time.minutesAgo', { n: mins });
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return say('time.hoursAgo', { n: hrs });
    return d.toLocaleDateString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** Next run as the user's local clock time, plus how far away it is. */
export function formatNextRun(iso: string | null, now?: Date, say: Say = tEn.bare, locale?: string): string {
    if (!iso) return say('schedules.time.never');
    const relative = formatTime(iso, say, now, locale);
    const d = new Date(iso);
    if (d.getTime() <= (now || new Date()).getTime()) return relative;
    const clock = d.toLocaleString(locale, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
    return say('schedules.time.clockIn', { clock, relative });
}

/** Returns true if the ISO timestamp is less than 60 seconds in the future. */
export function isCountdownRange(iso: string | null): boolean {
    if (!iso) return false;
    const diffMs = new Date(iso).getTime() - Date.now();
    return diffMs > 0 && diffMs < 60000;
}

/** A clock time as the UI language writes it, 12h or 24h. */
export function formatClock(t: Translate, hour: number, minute = 0): string {
    const at = new Date(2000, 0, 1, hour, minute);
    return new Intl.DateTimeFormat(dateLocale(t), { hour: 'numeric', minute: '2-digit' }).format(at);
}

export function formatTimeOfDay(time: string, t: Translate): string {
    const [h, m] = time.split(':').map(Number);
    return formatClock(t, h, m);
}

/** `base.<CLDR plural form>` for `n` in the UI language; null when that form has no message. */
function countedKey(base: string, n: number, t: Translate): MessageKey | null {
    const key = `${base}.${new Intl.PluralRules(t.locale).select(n)}`;
    return isMessageKey(key) ? key : null;
}

const isWeekdays = (days: number[]) => days.length === 5 && days.every((d) => d < 5);
const isWeekends = (days: number[]) => days.length === 2 && days.includes(5) && days.includes(6);

/** Days after an interval or window: "weekdays", "Monday, Wednesday". */
function dayPhrase(days: number[], t: Translate, say: Say): string | null {
    if (isWeekdays(days)) return say('schedules.desc.days.weekdays');
    if (isWeekends(days)) return say('schedules.desc.days.weekends');
    if (days.length === 7) return say('schedules.desc.days.every');
    const key = countedKey('schedules.desc.dayList', days.length, t);
    if (!key) return null;
    const names = days.map((d) => say(`schedules.dayName.${d}` as MessageKey)).join(say('schedules.desc.dayJoin'));
    return say(key, { days: names });
}

/**
 * One language's wording of `parts`; null when a shape has no wording here.
 * `t` picks the plural form (the UI language's), `clock` writes the times.
 */
function schedulePhrase(parts: ScheduleDescription, t: Translate, say: Say, clock: Translate): string | null {
    const { kind, count, unit, days, time, start_hour, end_hour } = parts;
    const at = (when: string) => (time ? say('schedules.desc.at', { when, time: formatTimeOfDay(time, clock) }) : when);
    if (kind === 'every' || kind === 'window') {
        const key = count && unit ? countedKey(`schedules.span.${unit}`, count, t) : null;
        const on = days ? dayPhrase(days, t, say) : undefined;
        if (!key || on === null) return null;
        const span = say(key, { n: count! });
        if (kind === 'every') {
            return on ? say('schedules.desc.everyOn', { span, days: on }) : say('schedules.desc.every', { span });
        }
        if (start_hour == null || end_hour == null) return null;
        const hours = say('schedules.hours', { start: formatClock(clock, start_hour), end: formatClock(clock, end_hour) });
        return on ? say('schedules.desc.windowOn', { span, hours, days: on }) : say('schedules.desc.window', { span, hours });
    }
    if (kind === 'daily') return time ? at(say('schedules.desc.daily')) : null;
    if (kind !== 'days' || !days?.length) return null;
    if (days.length === 7) return at(say('schedules.desc.daily'));
    if (isWeekdays(days)) return at(say('schedules.desc.weekdays'));
    if (isWeekends(days)) return at(say('schedules.desc.weekends'));
    const on = dayPhrase(days, t, say);
    return on && at(say('schedules.desc.onDays', { days: on }));
}

/**
 * When a schedule runs, worded in the UI language from the backend's parts;
 * null when a shape has no wording here, so the caller shows the English.
 */
export function describeSchedule(parts: ScheduleDescription | null | undefined, t: Translate): string | null {
    if (!parts || schedulePhrase(parts, t, t.bare, t) === null) return null;
    return t.compose((say, code) => schedulePhrase(parts, t, say, translator(code)) ?? '');
}

