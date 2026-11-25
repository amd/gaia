// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Sizes, speeds and times for setup, in decimal units (1 GB = 1000 MB), as the backend counts. */

import { getLocale, isolate, type Say } from '../../i18n/registry';

const GB = 1e9;

/** "1.8 TB" once the GB figure shown would reach 1000; null below that. */
function asTb(gb: number, shownGb: number): string | null {
    return shownGb >= 1000 ? `${(gb / 1000).toFixed(1)} TB` : null;
}

/** "23.3 GB", "300 MB" under 1 GB, "1.8 TB" from 1000 GB. */
export function formatSize(gb: number): string {
    if (gb > 0 && gb < 1) return `${Math.max(1, Math.round(gb * 1000))} MB`;
    return asTb(gb, Number(gb.toFixed(1))) ?? `${gb.toFixed(1)} GB`;
}

/** "251 GB", or "1.8 TB" from 1000 GB. */
export function formatWholeGb(gb: number): string {
    return asTb(gb, Math.round(gb)) ?? `${Math.round(gb)} GB`;
}

/** Lemonade's "GB" of installed memory are binary (GiB); setup shows decimal GB throughout. */
const GB_PER_GIB = 1024 ** 3 / GB;

/**
 * Installed memory, from Lemonade's report ("128.00 GB", in GiB) in decimal GB like the
 * GPU memory pane. The OS figure is the fallback: on a PC that sets memory aside for its
 * GPU, it is the smaller amount the OS can use.
 */
export function totalMemory(reported: string | null | undefined, ramGb: number | null | undefined): string | null {
    const gib = reported ? Number.parseFloat(reported) : Number.NaN;
    if (Number.isFinite(gib) && reported?.trim().toUpperCase().endsWith('GB')) return formatSize(gib * GB_PER_GIB);
    if (reported) return reported;
    return ramGb != null ? formatSize(ramGb) : null;
}

/**
 * A memory-fit number from the plan: a model's need or this PC's memory for
 * models. The plan reports both in decimal GB, like the GPU memory pane, so
 * every memory number on a screen carries this one label.
 */
export function formatFitGb(gb: number): string {
    return `${gb.toFixed(1)} GB`;
}

export function formatBytes(bytes: number): string {
    return formatSize(bytes / GB);
}

/** "18 MB/s"; one decimal under 10 MB/s. */
export function formatSpeed(bps: number): string {
    const mb = bps / 1e6;
    return `${mb >= 10 ? Math.round(mb) : mb.toFixed(1)} MB/s`;
}

/**
 * A number with its unit ("10.4 GB / 23.3 GB", "859 MB/s") kept whole inside a
 * right-to-left line; unchanged in a left-to-right one.
 */
export function ltrIn(locale: string, text: string): string {
    return getLocale(locale).meta.dir === 'rtl' ? isolate(text, 'ltr') : text;
}

const HOUR_S = 3600;
const DAY_S = 24 * HOUR_S;

/** "about 40 sec", "about 13 min", "about 16 hours", "more than a day"; `t` is a translator or a compose `say`. */
export function formatEta(seconds: number, t: Say): string {
    if (seconds < 90) return t('setup.eta.sec', { n: String(Math.max(1, Math.round(seconds))) });
    if (seconds <= 2 * HOUR_S) return t('setup.eta.min', { n: String(Math.round(seconds / 60)) });
    if (seconds <= DAY_S) return t('setup.eta.hours', { n: String(Math.round(seconds / HOUR_S)) });
    return t('setup.eta.day');
}

/** Spoken ETA for `aria-valuetext`, never abbreviated. */
export function spokenEta(seconds: number, t: Say): string {
    if (seconds < 90) return t('setup.eta.secSpoken', { n: String(Math.max(1, Math.round(seconds))) });
    if (seconds <= 2 * HOUR_S) return t('setup.eta.minSpoken', { n: String(Math.round(seconds / 60)) });
    if (seconds > DAY_S) return t('setup.eta.daySpoken');
    // Hours run 2 to 24, so the noun takes one of three forms in languages that count that way.
    const n = Math.round(seconds / HOUR_S);
    const form = n === 2 ? 'two' : n <= 10 ? 'few' : 'many';
    return t(`setup.eta.hoursSpoken.${form}`, { n: String(n) });
}

/** Seconds of data before a speed or ETA is shown. */
export const ESTIMATE_AFTER_S = 20;
/** The window the shown speed is averaged over. */
const SPEED_TAU_S = 5;
/** The window the rate behind the ETA is averaged over. */
const ETA_WINDOW_S = 45;

export interface Estimate {
    /** Smoothed bytes per second; null until there are ESTIMATE_AFTER_S of data. */
    speed: number | null;
    /** Seconds left; null while estimating or when nothing is moving. */
    eta: number | null;
    /** Seconds since the byte count last moved. */
    stalledFor: number;
}

interface Sample {
    at: number;
    bytes: number;
}

/**
 * Speed as a 5 s exponential moving average of the byte count. The ETA comes from
 * the average rate over the last 45 s, so one slow or fast poll does not swing it,
 * and eases toward that estimate in both directions, so it never jumps.
 */
export class DownloadMeter {
    private samples: Sample[] = [];
    private lastAt = 0;
    private lastBytes = 0;
    private movedAt = 0;
    private speed = 0;
    private shownEta: number | null = null;
    private shownAt = 0;

    private restart(bytesDone: number, now: number): void {
        this.samples = [{ at: now, bytes: bytesDone }];
        this.lastAt = now;
        this.lastBytes = bytesDone;
        this.movedAt = now;
    }

    /** Feed the latest byte count at `now` (ms); returns what to show. */
    update(bytesDone: number, bytesTotal: number, now: number): Estimate {
        if (this.samples.length === 0 || bytesDone < this.lastBytes) {
            this.restart(bytesDone, now);
            this.speed = 0;
            this.shownEta = null;
            return { speed: null, eta: null, stalledFor: 0 };
        }
        if (this.lastBytes === 0 && bytesDone > 0) {
            // A resume's first poll can read 0 before the bytes already on disk
            // show up: measure from the first real figure, never the jump to it.
            this.restart(bytesDone, now);
            return { speed: null, eta: null, stalledFor: 0 };
        }
        const dt = (now - this.lastAt) / 1000;
        if (dt > 0) {
            const instant = (bytesDone - this.lastBytes) / dt;
            const alpha = 1 - Math.exp(-dt / SPEED_TAU_S);
            this.speed = this.speed === 0 ? instant : this.speed + alpha * (instant - this.speed);
            if (bytesDone > this.lastBytes) this.movedAt = now;
            this.lastAt = now;
            this.lastBytes = bytesDone;
            this.samples.push({ at: now, bytes: bytesDone });
            while (this.samples.length > 2 && this.samples[1].at <= now - ETA_WINDOW_S * 1000) this.samples.shift();
        }
        const stalledFor = (now - this.movedAt) / 1000;
        const first = this.samples[0];
        const span = (now - first.at) / 1000;
        if (span < ESTIMATE_AFTER_S) return { speed: null, eta: null, stalledFor };
        const rate = (bytesDone - first.bytes) / span;
        if (this.speed <= 0 || rate <= 0) {
            this.shownEta = null;
            return { speed: Math.max(0, this.speed), eta: null, stalledFor };
        }
        const raw = Math.max(0, bytesTotal - bytesDone) / rate;
        if (this.shownEta === null) {
            this.shownEta = raw;
        } else {
            const elapsed = (now - this.shownAt) / 1000;
            const floor = this.shownEta - elapsed;
            // Never a jump: down by the clock or half the gap, whichever is more,
            // so an estimate that was too high catches up within a few seconds;
            // up a quarter of the way per update.
            this.shownEta = raw < floor ? this.shownEta - Math.max(elapsed, (this.shownEta - raw) / 2)
                : raw > this.shownEta ? this.shownEta + (raw - this.shownEta) * 0.25 : raw;
        }
        this.shownAt = now;
        return { speed: this.speed, eta: Math.max(0, this.shownEta), stalledFor };
    }
}

/** Seconds without new bytes before the row says so, and before it offers a retry. */
export const STALL_NOTICE_S = 15;
export const STALL_STUCK_S = 60;
