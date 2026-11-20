// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The wording rules of webui's ScheduleManager.tsx, which is a component and can't be imported.

import type { ScheduleDescription } from '@webui/types';
import { DEFAULT_LOCALE, isMessageKey, type MessageKey, type Say } from '@webui/i18n/registry';
import { dateLocale, translator, type Translate } from '@webui/i18n/translator';

/** The locale for dates in one half of a composed line; the English half of a bracketed line is English, not the phone's region. */
export function composeDateLocale(code: string, ui: Translate): string | undefined {
    return code === DEFAULT_LOCALE && ui.locale !== DEFAULT_LOCALE ? DEFAULT_LOCALE : dateLocale(translator(code));
}

/** When a past or future time is, as the card's "Last:" line says it. */
export function formatTime(iso: string | null, say: Say, now: Date, locale?: string): string {
    if (!iso) return say('schedules.time.never');
    const d = new Date(iso);
    const diffSecs = Math.floor((now.getTime() - d.getTime()) / 1000);

    if (diffSecs < 0) {
        const futureSecs = -diffSecs;
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

/** The local clock time of a future run, in its own text so the "Next:" line carries one gloss and no bracket inside it; null when the run is due. */
export function formatNextClock(iso: string | null, now: Date, locale?: string): string | null {
    if (!iso) return null;
    const d = new Date(iso);
    if (d.getTime() <= now.getTime()) return null;
    return d.toLocaleString(locale, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
}

/** True while the next run is under a minute away, when the card counts down by the second. */
export function isCountdownRange(iso: string | null, now: Date): boolean {
    if (!iso) return false;
    const diffMs = new Date(iso).getTime() - now.getTime();
    return diffMs > 0 && diffMs < 60_000;
}

/** A clock time as the UI language writes it, 12h or 24h. */
function formatClock(locale: string | undefined, hour: number, minute = 0): string {
    const at = new Date(2000, 0, 1, hour, minute);
    return new Intl.DateTimeFormat(locale, { hour: 'numeric', minute: '2-digit' }).format(at);
}

function formatTimeOfDay(time: string, locale: string | undefined): string {
    const [h, m] = time.split(':').map(Number);
    return formatClock(locale, h!, m);
}

/** `base.<CLDR plural form>` for `n` in the UI language; null when that form has no message. */
function countedKey(base: string, n: number, t: Translate): MessageKey | null {
    const key = `${base}.${new Intl.PluralRules(t.locale).select(n)}`;
    return isMessageKey(key) ? key : null;
}

const isWeekdays = (days: number[]) => days.length === 5 && days.every((d) => d < 5);
const isWeekends = (days: number[]) => days.length === 2 && days.includes(5) && days.includes(6);

function dayPhrase(days: number[], t: Translate, say: Say): string | null {
    if (isWeekdays(days)) return say('schedules.desc.days.weekdays');
    if (isWeekends(days)) return say('schedules.desc.days.weekends');
    if (days.length === 7) return say('schedules.desc.days.every');
    const key = countedKey('schedules.desc.dayList', days.length, t);
    if (!key) return null;
    const names = days.map((d) => say(`schedules.dayName.${d}` as MessageKey)).join(say('schedules.desc.dayJoin'));
    return say(key, { days: names });
}

// `t` picks the plural form (the UI language's), `clock` writes the times.
function schedulePhrase(parts: ScheduleDescription, t: Translate, say: Say, clock: Translate): string | null {
    const clockLocale = composeDateLocale(clock.locale, t);
    const { kind, count, unit, days, time, start_hour, end_hour } = parts;
    const at = (when: string) => (time ? say('schedules.desc.at', { when, time: formatTimeOfDay(time, clockLocale) }) : when);
    if (kind === 'every' || kind === 'window') {
        const key = count && unit ? countedKey(`schedules.span.${unit}`, count, t) : null;
        const on = days ? dayPhrase(days, t, say) : undefined;
        if (!key || on === null) return null;
        const span = say(key, { n: count! });
        if (kind === 'every') {
            return on ? say('schedules.desc.everyOn', { span, days: on }) : say('schedules.desc.every', { span });
        }
        if (start_hour == null || end_hour == null) return null;
        const hours = say('schedules.hours', { start: formatClock(clockLocale, start_hour), end: formatClock(clockLocale, end_hour) });
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

/** When a schedule runs, worded in the UI language; null when a shape has no wording, so the caller shows the English. */
export function describeSchedule(parts: ScheduleDescription | null | undefined, t: Translate): string | null {
    if (!parts || schedulePhrase(parts, t, t.bare, t) === null) return null;
    return t.compose((say, code) => schedulePhrase(parts, t, say, translator(code)) ?? '');
}
