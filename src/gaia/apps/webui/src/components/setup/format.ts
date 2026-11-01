// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Sizes, speeds and times for setup, in decimal units (1 GB = 1000 MB), as the backend counts. */

import { getLocale, isolate, type Say } from '../../i18n/registry';

const GB = 1e9;

/** "23.3 GB", or "300 MB" under 1 GB. */
export function formatSize(gb: number): string {
    if (gb > 0 && gb < 1) return `${Math.max(1, Math.round(gb * 1000))} MB`;
    return `${gb.toFixed(1)} GB`;
}

/**
 * A number from the memory-fit calculation: a model's need or this PC's memory
 * for models. Both come from the same model_fit math in the same unit, so they
 * share this one label and a sentence never compares two units.
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

/** "about 40 sec", "about 13 min", "over 2 hours"; `t` is a translator or a compose `say`. */
export function formatEta(seconds: number, t: Say): string {
    if (seconds < 90) return t('setup.eta.sec', { n: String(Math.max(1, Math.round(seconds))) });
    if (seconds <= 2 * 3600) return t('setup.eta.min', { n: String(Math.round(seconds / 60)) });
    return t('setup.eta.long');
}

/** Spoken ETA for `aria-valuetext`, never abbreviated. */
export function spokenEta(seconds: number, t: Say): string {
    if (seconds < 90) return t('setup.eta.secSpoken', { n: String(Math.max(1, Math.round(seconds))) });
    if (seconds <= 2 * 3600) return t('setup.eta.minSpoken', { n: String(Math.round(seconds / 60)) });
    return t('setup.eta.long');
}

/** Seconds of data before a speed or ETA is shown. */
export const ESTIMATE_AFTER_S = 10;
/** The window the speed is averaged over. */
const SPEED_TAU_S = 5;

export interface Estimate {
    /** Smoothed bytes per second; null until there are ESTIMATE_AFTER_S of data. */
    speed: number | null;
    /** Seconds left; null while estimating or when nothing is moving. */
    eta: number | null;
    /** Seconds since the byte count last moved. */
    stalledFor: number;
}

/**
 * Speed as a 5 s exponential moving average of the byte count, and an ETA that
 * eases toward the estimate in both directions, so it never jumps.
 */
export class DownloadMeter {
    private start: number | null = null;
    private lastAt = 0;
    private lastBytes = 0;
    private movedAt = 0;
    private speed = 0;
    private shownEta: number | null = null;
    private shownAt = 0;

    /** Feed the latest byte count at `now` (ms); returns what to show. */
    update(bytesDone: number, bytesTotal: number, now: number): Estimate {
        if (this.start === null || bytesDone < this.lastBytes) {
            this.start = now;
            this.lastAt = now;
            this.lastBytes = bytesDone;
            this.movedAt = now;
            this.speed = 0;
            this.shownEta = null;
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
        }
        const stalledFor = (now - this.movedAt) / 1000;
        if ((now - this.start) / 1000 < ESTIMATE_AFTER_S) return { speed: null, eta: null, stalledFor };
        if (this.speed <= 0) {
            this.shownEta = null;
            return { speed: 0, eta: null, stalledFor };
        }
        const raw = Math.max(0, bytesTotal - bytesDone) / this.speed;
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
