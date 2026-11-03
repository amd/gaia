// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Fragment, type ReactNode } from 'react';
import { useT } from '../i18n/useT';

/** The gated calendar tools (`calendar_tools.py`), whose arguments a person must see to approve. */
export const CALENDAR_WRITE_TOOLS = new Set(['create_calendar_event', 'update_calendar_event', 'cancel_calendar_event']);

const BARE_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const DAY = { year: 'numeric', month: 'short', day: 'numeric' } as const;
const CLOCK = { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' } as const;

interface Formats { time: Intl.DateTimeFormat; timeOnly: Intl.DateTimeFormat; date: Intl.DateTimeFormat }
const FORMATS = new Map<string, Formats>();

/** Dates in the UI's language, built once per locale. */
function formatsFor(locale: string): Formats {
    let formats = FORMATS.get(locale);
    if (!formats) {
        formats = {
            time: new Intl.DateTimeFormat(locale, { ...DAY, ...CLOCK }),
            timeOnly: new Intl.DateTimeFormat(locale, CLOCK),
            date: new Intl.DateTimeFormat(locale, DAY),
        };
        FORMATS.set(locale, formats);
    }
    return formats;
}

/*
 * Shown as a code, exactly as `person_approval.py` does: every format, control and
 * line or paragraph separator character, every Default_Ignorable_Code_Point, and
 * letters that render blank. Layout and the marks RTL and Indic writing need stay as
 * written, and so does one variation selector right after a visible character, as
 * emoji use it. A Python test keeps these lists equal to that file's.
 */
const KEPT_AS_WRITTEN = new Set(['\u0009', '\u000A', '\u200C', '\u200D', '\u200E', '\u200F']);
const BLANK_LOOKING = new Set(['\u034F', '\u115F', '\u1160', '\u2800', '\u3164', '\uFFA0']);
const DEFAULT_IGNORABLE: ReadonlyArray<readonly [number, number]> = [
    [0x00AD, 0x00AD],
    [0x034F, 0x034F],
    [0x061C, 0x061C],
    [0x115F, 0x1160],
    [0x17B4, 0x17B5],
    [0x180B, 0x180F],
    [0x200B, 0x200F],
    [0x202A, 0x202E],
    [0x2060, 0x206F],
    [0x3164, 0x3164],
    [0xFE00, 0xFE0F],
    [0xFEFF, 0xFEFF],
    [0xFFA0, 0xFFA0],
    [0xFFF0, 0xFFF8],
    [0x1BCA0, 0x1BCA3],
    [0x1D173, 0x1D17A],
    [0xE0000, 0xE0FFF],
];
const VARIATION_SELECTORS: ReadonlyArray<readonly [number, number]> = [
    [0xFE00, 0xFE0F],
    [0xE0100, 0xE01EF],
];
const HIDDEN_CATEGORY = /^[\p{Cf}\p{Cc}\p{Zl}\p{Zp}]$/u;

function among(ranges: ReadonlyArray<readonly [number, number]>, char: string): boolean {
    const point = char.codePointAt(0) ?? -1;
    return ranges.some(([low, high]) => low <= point && point <= high);
}

const variationSelector = (char: string) => among(VARIATION_SELECTORS, char);

function hidden(char: string): boolean {
    if (KEPT_AS_WRITTEN.has(char) || variationSelector(char)) return false;
    return BLANK_LOOKING.has(char) || HIDDEN_CATEGORY.test(char) || among(DEFAULT_IGNORABLE, char);
}

/** `[U+202E]` in place of each, so what is approved reads as what is saved. */
function visibleText(value: string, multiline: boolean): string {
    let previous = '';
    let shown = '';
    for (const char of value.replace(/\r\n/g, '\n')) {
        const isHidden = variationSelector(char)
            ? !previous || /^\s$/u.test(previous) || KEPT_AS_WRITTEN.has(previous) || hidden(previous) || variationSelector(previous)
            : hidden(char);
        shown += isHidden ? `[U+${char.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}]` : char;
        previous = char;
    }
    return multiline ? shown : shown.replace(/\n/g, '[U+000A]');
}

const FENCE_OPEN = '<<<UNTRUSTED_TEXT>>>';
const FENCE_CLOSE = '<<<END_UNTRUSTED_TEXT>>>';

function fenceSide(char: string): string {
    if (char === '<' || char === '>') return char;
    const folded = char.normalize('NFKC');
    return folded === '<' || folded === '>' ? folded : '';
}

/** Mirrors `strip_untrusted_field_markers` (`untrusted_text.py`): no fence can be spelled from what is left. */
function plain(value: unknown): string {
    if (typeof value !== 'string') return '';
    let text = value;
    let previous = '';
    while (previous !== text) {
        previous = text;
        text = text.split(FENCE_OPEN).join('').split(FENCE_CLOSE).join('');
    }
    const chars = [...text];
    let out = '';
    let start = 0;
    while (start < chars.length) {
        const side = fenceSide(chars[start]);
        if (!side) {
            out += chars[start];
            start += 1;
            continue;
        }
        let count = 0;
        let last = start;
        for (let at = start; at < chars.length; at++) {
            if (fenceSide(chars[at]) === side) {
                count += 1;
                last = at;
            } else if (!hidden(chars[at])) break;
        }
        out += count >= 3 ? side + side : chars.slice(start, last + 1).join('');
        start = last + 1;
    }
    return out;
}

/** GAIA sends the checked values (addresses as notified), so this only makes them visible. */
function text(value: unknown, multiline = false): string {
    return visibleText(plain(value), multiline);
}

/** Local midnight, so an all-day date can't slide to a neighbouring day. */
function bareDate(value: string): Date | null {
    const m = BARE_DATE.exec(value);
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

function When({ start, end }: { start: string; end: string }) {
    const t = useT();
    const { time, timeOnly, date } = formatsFor(t.locale);
    const firstDay = bareDate(start);
    if (firstDay) {
        const lastDay = bareDate(end);
        const range = lastDay && lastDay > firstDay
            ? `${date.format(firstDay)} – ${date.format(lastDay)}`
            : date.format(firstDay);
        return <><bdi dir="auto">{range}</bdi> ({t('permission.calendar.allDay')})</>;
    }
    const begins = new Date(start);
    const ends = new Date(end);
    if (Number.isNaN(begins.getTime())) return <bdi dir="ltr">{start}</bdi>;
    let shown = time.format(begins);
    if (!Number.isNaN(ends.getTime()) && ends > begins) {
        const sameDay = date.format(begins) === date.format(ends);
        shown += ` – ${(sameDay ? timeOnly : time).format(ends)}`;
    }
    return <bdi dir="auto">{shown}</bdi>;
}

function Name({ value }: { value: string }) {
    return <bdi dir="auto">{value}</bdi>;
}

/** The title, time, account and people a calendar approval turns on, with no click needed. */
export function CalendarApproval({ tool, args }: { tool: string; args: Record<string, unknown> }) {
    const t = useT();
    const people = Array.isArray(args.notify_attendees) ? args.notify_attendees.map((value) => text(value)).filter(Boolean) : [];
    const rows: Array<[string, ReactNode]> = [];
    const named = (label: string, value: unknown, multiline = false) => {
        const shown = text(value, multiline);
        if (shown) rows.push([label, <Name value={shown} />]);
    };

    if (tool === 'create_calendar_event') {
        named(t('permission.calendar.title'), args.title);
        rows.push([t('permission.calendar.when'), <When start={text(args.start)} end={text(args.end)} />]);
        named(t('permission.calendar.account'), args.calendar_account);
        named(t('permission.calendar.location'), args.location);
        named(t('permission.calendar.description'), args.description, true);
    } else {
        named(t('permission.calendar.event'), args.expected_title);
        rows.push([t('permission.calendar.when'), <When start={text(args.expected_start)} end="" />]);
        named(t('permission.calendar.newTitle'), args.new_title);
        if (text(args.new_start)) {
            rows.push([t('permission.calendar.newTime'), <When start={text(args.new_start)} end={text(args.new_end)} />]);
        }
        named(t('permission.calendar.newLocation'), args.new_location);
        named(t('permission.calendar.newDescription'), args.new_description, true);
    }

    return (
        <dl className="perm-card-facts" data-testid="perm-calendar">
            {rows.map(([label, value]) => (
                <Fragment key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                </Fragment>
            ))}
            <dt>{people.length ? t.plural('permission.calendar.notifies', people.length) : t('permission.calendar.noOne')}</dt>
            <dd>
                {people.length > 0 && (
                    <div className="perm-card-people" tabIndex={0}>
                        {people.map((person) => <Name key={person} value={person} />)}
                    </div>
                )}
            </dd>
        </dl>
    );
}
