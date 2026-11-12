// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Fragment, type ReactNode } from 'react';
import { useT } from '../i18n/useT';

/** The gated tools of the bundled calendar skill (`hub/skills/calendar`), whose arguments a person must see to approve. */
export const CALENDAR_WRITE_TOOLS = new Set(['calendar/create_calendar_event', 'calendar/update_calendar_event', 'calendar/cancel_calendar_event']);

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
 * written, and so does a variation selector where Unicode defines it: FE0E/FE0F after
 * an emoji or in a keycap, E0100-E01EF after a CJK ideograph. A Python test keeps
 * these lists equal to that file's.
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
const EXTENDED_PICTOGRAPHIC: ReadonlyArray<readonly [number, number]> = [
    [0x00A9, 0x00A9], [0x00AE, 0x00AE], [0x203C, 0x203C], [0x2049, 0x2049], [0x2122, 0x2122],
    [0x2139, 0x2139], [0x2194, 0x2199], [0x21A9, 0x21AA], [0x231A, 0x231B], [0x2328, 0x2328],
    [0x23CF, 0x23CF], [0x23E9, 0x23F3], [0x23F8, 0x23FA], [0x24C2, 0x24C2], [0x25AA, 0x25AB],
    [0x25B6, 0x25B6], [0x25C0, 0x25C0], [0x25FB, 0x25FE], [0x2600, 0x2604], [0x260E, 0x260E],
    [0x2611, 0x2611], [0x2614, 0x2615], [0x2618, 0x2618], [0x261D, 0x261D], [0x2620, 0x2620],
    [0x2622, 0x2623], [0x2626, 0x2626], [0x262A, 0x262A], [0x262E, 0x262F], [0x2638, 0x263A],
    [0x2640, 0x2640], [0x2642, 0x2642], [0x2648, 0x2653], [0x265F, 0x2660], [0x2663, 0x2663],
    [0x2665, 0x2666], [0x2668, 0x2668], [0x267B, 0x267B], [0x267E, 0x267F], [0x2692, 0x2697],
    [0x2699, 0x2699], [0x269B, 0x269C], [0x26A0, 0x26A1], [0x26A7, 0x26A7], [0x26AA, 0x26AB],
    [0x26B0, 0x26B1], [0x26BD, 0x26BE], [0x26C4, 0x26C5], [0x26C8, 0x26C8], [0x26CE, 0x26CF],
    [0x26D1, 0x26D1], [0x26D3, 0x26D4], [0x26E9, 0x26EA], [0x26F0, 0x26F5], [0x26F7, 0x26FA],
    [0x26FD, 0x26FD], [0x2702, 0x2702], [0x2705, 0x2705], [0x2708, 0x270D], [0x270F, 0x270F],
    [0x2712, 0x2712], [0x2714, 0x2714], [0x2716, 0x2716], [0x271D, 0x271D], [0x2721, 0x2721],
    [0x2728, 0x2728], [0x2733, 0x2734], [0x2744, 0x2744], [0x2747, 0x2747], [0x274C, 0x274C],
    [0x274E, 0x274E], [0x2753, 0x2755], [0x2757, 0x2757], [0x2763, 0x2764], [0x2795, 0x2797],
    [0x27A1, 0x27A1], [0x27B0, 0x27B0], [0x27BF, 0x27BF], [0x2934, 0x2935], [0x2B05, 0x2B07],
    [0x2B1B, 0x2B1C], [0x2B50, 0x2B50], [0x2B55, 0x2B55], [0x3030, 0x3030], [0x303D, 0x303D],
    [0x3297, 0x3297], [0x3299, 0x3299], [0x1F004, 0x1F004], [0x1F02C, 0x1F02F], [0x1F094, 0x1F09F],
    [0x1F0AF, 0x1F0B0], [0x1F0C0, 0x1F0C0], [0x1F0CF, 0x1F0D0], [0x1F0F6, 0x1F0FF], [0x1F170, 0x1F171],
    [0x1F17E, 0x1F17F], [0x1F18E, 0x1F18E], [0x1F191, 0x1F19A], [0x1F1AE, 0x1F1E5], [0x1F201, 0x1F20F],
    [0x1F21A, 0x1F21A], [0x1F22F, 0x1F22F], [0x1F232, 0x1F23A], [0x1F23C, 0x1F23F], [0x1F249, 0x1F25F],
    [0x1F266, 0x1F321], [0x1F324, 0x1F393], [0x1F396, 0x1F397], [0x1F399, 0x1F39B], [0x1F39E, 0x1F3F0],
    [0x1F3F3, 0x1F3F5], [0x1F3F7, 0x1F3FA], [0x1F400, 0x1F4FD], [0x1F4FF, 0x1F53D], [0x1F549, 0x1F54E],
    [0x1F550, 0x1F567], [0x1F56F, 0x1F570], [0x1F573, 0x1F57A], [0x1F587, 0x1F587], [0x1F58A, 0x1F58D],
    [0x1F590, 0x1F590], [0x1F595, 0x1F596], [0x1F5A4, 0x1F5A5], [0x1F5A8, 0x1F5A8], [0x1F5B1, 0x1F5B2],
    [0x1F5BC, 0x1F5BC], [0x1F5C2, 0x1F5C4], [0x1F5D1, 0x1F5D3], [0x1F5DC, 0x1F5DE], [0x1F5E1, 0x1F5E1],
    [0x1F5E3, 0x1F5E3], [0x1F5E8, 0x1F5E8], [0x1F5EF, 0x1F5EF], [0x1F5F3, 0x1F5F3], [0x1F5FA, 0x1F64F],
    [0x1F680, 0x1F6C5], [0x1F6CB, 0x1F6D2], [0x1F6D5, 0x1F6E5], [0x1F6E9, 0x1F6E9], [0x1F6EB, 0x1F6F0],
    [0x1F6F3, 0x1F6FF], [0x1F7DA, 0x1F7FF], [0x1F80C, 0x1F80F], [0x1F848, 0x1F84F], [0x1F85A, 0x1F85F],
    [0x1F888, 0x1F88F], [0x1F8AE, 0x1F8AF], [0x1F8BC, 0x1F8BF], [0x1F8C2, 0x1F8CF], [0x1F8D9, 0x1F8FF],
    [0x1F90C, 0x1F93A], [0x1F93C, 0x1F945], [0x1F947, 0x1F9FF], [0x1FA58, 0x1FA5F], [0x1FA6E, 0x1FAFF],
    [0x1FC00, 0x1FFFD],
];
const CJK_IDEOGRAPHS: ReadonlyArray<readonly [number, number]> = [
    [0x3400, 0x4DBF], [0x4E00, 0x9FFF], [0xF900, 0xFAFF], [0x20000, 0x2A6DF], [0x2A700, 0x2B73F],
    [0x2B740, 0x2B81F], [0x2B820, 0x2CEAF], [0x2CEB0, 0x2EBEF], [0x2EBF0, 0x2EE5F], [0x2F800, 0x2FA1F],
    [0x30000, 0x3134F], [0x31350, 0x323AF],
];
const KEYCAP_BASES = new Set([...'0123456789#*']);
const HIDDEN_CATEGORY = /^[\p{Cf}\p{Cc}\p{Zl}\p{Zp}]$/u;

function among(ranges: ReadonlyArray<readonly [number, number]>, char: string): boolean {
    const point = char.codePointAt(0) ?? -1;
    return ranges.some(([low, high]) => low <= point && point <= high);
}

const variationSelector = (char: string) => among(VARIATION_SELECTORS, char);

function hidden(char: string, kept: ReadonlySet<string>): boolean {
    if (kept.has(char) || variationSelector(char)) return false;
    return BLANK_LOOKING.has(char) || HIDDEN_CATEGORY.test(char) || among(DEFAULT_IGNORABLE, char);
}

/** Mirrors `_selector_fits` (`person_approval.py`). */
function selectorFits(chars: string[], at: number): boolean {
    const char = chars[at];
    const previous = at > 0 ? chars[at - 1] : '';
    if (!previous) return false;
    if (char === '\uFE0E' || char === '\uFE0F') {
        if (among(EXTENDED_PICTOGRAPHIC, previous)) return true;
        return char === '\uFE0F' && KEYCAP_BASES.has(previous) && chars[at + 1] === '\u20E3';
    }
    const point = char.codePointAt(0) ?? 0;
    return point >= 0xE0100 && point <= 0xE01EF && among(CJK_IDEOGRAPHS, previous);
}

/**
 * `[U+202E]` in place of each, so what is approved reads as what is saved. `kept` is what
 * stays as written: prose keeps `KEPT_AS_WRITTEN`, a file preview keeps less.
 */
export function visibleWith(kept: ReadonlySet<string>, value: string, multiline: boolean): string {
    const chars = [...value.replace(/\r\n/g, '\n')];
    const shown = chars.map((char, at) => {
        const isHidden = variationSelector(char) ? !selectorFits(chars, at) : hidden(char, kept);
        return isHidden ? `[U+${char.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}]` : char;
    }).join('');
    return multiline ? shown : shown.replace(/\n/g, '[U+000A]');
}

/** `HIDDEN_NOTE` plus what `calendar_tools.py` adds to it; a Python test keeps them equal. */
const UNSEEN_NOTE = "This text holds characters that don't show. Those that hide or reorder text " +
    'appear as [U+XXXX]; ideograph variant marks are kept as written. They are saved as written.';

/** Mirrors `has_unseen`: a code would show, or an ideograph variant mark is kept as written. */
export function unseenWith(kept: ReadonlySet<string>, value: unknown): boolean {
    if (typeof value !== 'string') return false;
    const chars = [...value.replace(/\r\n/g, '\n')];
    return chars.some((char, at) => {
        const point = char.codePointAt(0) ?? 0;
        if (point >= 0xE0100 && point <= 0xE01EF) return true;
        return variationSelector(char) ? !selectorFits(chars, at) : hidden(char, kept);
    });
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
            } else if (!hidden(chars[at], KEPT_AS_WRITTEN)) break;
        }
        out += count >= 3 ? side + side : chars.slice(start, last + 1).join('');
        start = last + 1;
    }
    return out;
}

/** GAIA sends the checked values (addresses as notified), so this only makes them visible. */
function text(value: unknown, multiline = false): string {
    return visibleWith(KEPT_AS_WRITTEN, plain(value), multiline);
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

    if (tool === 'calendar/create_calendar_event') {
        named(t('permission.calendar.title'), args.title);
        rows.push([t('permission.calendar.when'), <When start={text(args.start)} end={text(args.end)} />]);
        named(t('permission.calendar.account'), args.calendar_account);
        named(t('permission.calendar.location'), args.location);
        named(t('permission.calendar.description'), args.description, true);
    } else {
        named(t('permission.calendar.event'), args.expected_title);
        rows.push([t('permission.calendar.when'), <When start={text(args.expected_start)} end="" />]);
        named(t('permission.calendar.account'), args.calendar_account);
        named(t('permission.calendar.newTitle'), args.new_title);
        if (text(args.new_start)) {
            rows.push([t('permission.calendar.newTime'), <When start={text(args.new_start)} end={text(args.new_end)} />]);
        }
        named(t('permission.calendar.newLocation'), args.new_location);
        named(t('permission.calendar.newDescription'), args.new_description, true);
    }

    // GAIA's own note covers this when it sent one; otherwise the card says it.
    const values = [...Object.values(args), ...(Array.isArray(args.notify_attendees) ? args.notify_attendees : [])];
    const note = typeof args._risk_note !== 'string' && values.some((value) => unseenWith(KEPT_AS_WRITTEN, value)) ? UNSEEN_NOTE : '';

    return (
        <>
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
        {note && <p className="perm-card-risk" role="note" data-testid="perm-calendar-unseen">{note}</p>}
        </>
    );
}
