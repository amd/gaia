// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { stripIsolates, translateBare, type Say } from '../../../i18n/registry';
import { tEn } from '../../../i18n/useT';
import { DownloadMeter, formatEta, formatSize, formatSpeed, formatWholeGb, spokenEta } from '../format';
import { copyableCommand } from '../GpuMemoryPanel';
import { driveName } from '../ModelChecklist';

const tAr: Say = (key, params) => stripIsolates(translateBare('ar', key, params));

describe('sizes, speeds and times', () => {
    it('shows decimal GB to one place, and MB under 1 GB', () => {
        expect(formatSize(23.3)).toBe('23.3 GB');
        expect(formatSize(0.3)).toBe('300 MB');
        expect(formatSize(0)).toBe('0.0 GB');
        expect(formatSize(0.0004)).toBe('1 MB');
        expect(formatSize(999.9)).toBe('999.9 GB');
    });

    it('shows 1000 GB and more in TB, to one place', () => {
        expect(formatSize(1794.4)).toBe('1.8 TB');
        expect(formatSize(1788.1)).toBe('1.8 TB');
        expect(formatSize(1000)).toBe('1.0 TB');
        expect(formatSize(999.96)).toBe('1.0 TB');
        expect(formatSize(12_345)).toBe('12.3 TB');
        expect(formatWholeGb(251.4)).toBe('251 GB');
        expect(formatWholeGb(999.4)).toBe('999 GB');
        expect(formatWholeGb(1794.4)).toBe('1.8 TB');
    });

    it('shows speed in MB/s', () => {
        expect(formatSpeed(18_000_000)).toBe('18 MB/s');
        expect(formatSpeed(2_500_000)).toBe('2.5 MB/s');
    });

    it('rounds the ETA as the spec says', () => {
        expect(formatEta(40, tEn)).toBe('about 40 sec');
        expect(formatEta(13 * 60, tEn)).toBe('about 13 min');
    });

    it('gives a figure in hours past two hours, up to a day', () => {
        const H = 3600;
        expect(formatEta(2 * H, tEn)).toBe('about 120 min');
        expect(formatEta(2 * H + 1, tEn)).toBe('about 2 hours');
        expect(formatEta(2.4 * H, tEn)).toBe('about 2 hours');
        expect(formatEta(2.5 * H, tEn)).toBe('about 3 hours');
        expect(formatEta(16 * H, tEn)).toBe('about 16 hours');
        expect(formatEta(24 * H, tEn)).toBe('about 24 hours');
        expect(formatEta(24 * H + 1, tEn)).toBe('more than a day');
        expect(formatEta(30 * H, tEn)).toBe('more than a day');
        expect(formatEta(400 * H, tEn)).toBe('more than a day');
    });

    it('speaks the hours in full', () => {
        const H = 3600;
        expect(spokenEta(13 * 60, tEn)).toBe('about 13 minutes left');
        expect(spokenEta(2.4 * H, tEn)).toBe('about 2 hours left');
        expect(spokenEta(16 * H, tEn)).toBe('about 16 hours left');
        expect(spokenEta(30 * H, tEn)).toBe('more than a day left');
    });

    it('says the hours in Arabic, with the noun in the form the count takes', () => {
        const H = 3600;
        expect(formatEta(2.5 * H, tAr)).toBe('نحو 3 س');
        expect(formatEta(16 * H, tAr)).toBe('نحو 16 س');
        expect(formatEta(30 * H, tAr)).toBe('أكثر من يوم');
        expect(spokenEta(2.4 * H, tAr)).toBe('يتبقى نحو ساعتين');
        expect(spokenEta(2.5 * H, tAr)).toBe('يتبقى نحو 3 ساعات');
        expect(spokenEta(10 * H, tAr)).toBe('يتبقى نحو 10 ساعات');
        expect(spokenEta(16 * H, tAr)).toBe('يتبقى نحو 16 ساعة');
        expect(spokenEta(30 * H, tAr)).toBe('يتبقى أكثر من يوم');
    });

    it('names the drive, or the folder when there is no drive letter', () => {
        expect(driveName('C:\\Users\\me\\.cache\\huggingface\\hub')).toBe('C:');
        expect(driveName('/home/me/.cache/huggingface/hub')).toBe('/home/me/.cache/huggingface/hub');
        expect(driveName(null)).toBeNull();
    });

    it('copies the command a guided step asks the user to run', () => {
        expect(copyableCommand("Run: echo 'options ttm pages_limit=1' | sudo tee /etc/modprobe.d/ttm.conf"))
            .toBe("echo 'options ttm pages_limit=1' | sudo tee /etc/modprobe.d/ttm.conf");
        expect(copyableCommand('Restart your PC, then come back here.')).toBeNull();
    });
});

describe('DownloadMeter', () => {
    it('estimates nothing for the first 20 seconds', () => {
        const m = new DownloadMeter();
        m.update(1e6, 1e9, 0);
        for (let s = 1; s < 20; s++) expect(m.update(1e6 + s * 5e6, 1e9, s * 1000)).toMatchObject({ speed: null, eta: null });
        const e = m.update(101e6, 1e9, 20_000);
        expect(e.speed).toBeGreaterThan(0);
        expect(e.eta).toBeGreaterThan(0);
    });

    // A real first run: about 2 MB/s in bursts for 200 s, then 45 MB/s to the end.
    it('holds a steady estimate through a slow, bursty start, then follows the speed-up', () => {
        const m = new DownloadMeter();
        const total = 7e9;
        let bytes = 0;
        const etas: (number | null)[] = [];
        for (let s = 0; bytes < total; s++) {
            etas[s] = m.update(bytes, total, s * 1000).eta;
            const burst = Math.floor(s / 5) % 2 ? 3.5e6 : 0.5e6;
            bytes = Math.min(total, bytes + (s < 200 ? burst : 45e6));
        }
        // The first figure arrives at 1 s, so 20 s of data is in at 21 s.
        expect(etas.slice(0, 21).every((e) => e === null)).toBe(true);
        const slow = etas.slice(21, 200) as number[];
        // About 57 min left at 2 MB/s; one burst or lull must not swing it.
        expect(Math.min(...slow)).toBeGreaterThan(50 * 60);
        expect(Math.max(...slow) / Math.min(...slow)).toBeLessThan(1.2);
        // A minute after the speed-up the estimate is close to the true 84 s left.
        expect(etas[260]).toBeGreaterThan(60);
        expect(etas[260]).toBeLessThan(120);
    });

    it('eases down to a shorter estimate instead of dropping at once', () => {
        const m = new DownloadMeter();
        let bytes = 0;
        for (let s = 0; s <= 20; s++) {
            m.update(bytes, 10e9, s * 1000);
            bytes += 10e6;
        }
        const before = m.update(bytes, 10e9, 21_000).eta!;
        bytes += 2e9;
        const after = m.update(bytes, 10e9, 22_000).eta!;
        expect(after).toBeLessThan(before - 1);
        expect(after).toBeGreaterThan(before / 3);
    });

    it('catches up within seconds when an earlier estimate was far too high', () => {
        const m = new DownloadMeter();
        m.update(0, 30e9, 0);
        // Twenty seconds at a crawl while the next pull starts: a huge estimate.
        for (let s = 1; s <= 20; s++) m.update(s * 1e6, 30e9, s * 1000);
        const high = m.update(21e6, 30e9, 21_000).eta!;
        // Then 900 MB/s with about 7 GB to go.
        let bytes = 23e9;
        let eta = high;
        for (let s = 22; s <= 30; s++) {
            bytes += 0.9e9;
            eta = m.update(bytes, 30e9 + 1e9, s * 1000).eta!;
        }
        expect(high).toBeGreaterThan(600);
        expect(eta).toBeLessThan(60);
    });

    it('rises gently when the speed drops, instead of jumping', () => {
        const m = new DownloadMeter();
        let bytes = 0;
        for (let s = 0; s <= 20; s++) {
            m.update(bytes, 10e9, s * 1000);
            bytes += 50e6;
        }
        const before = m.update(bytes, 10e9, 21_000).eta!;
        const after = m.update(bytes + 1e6, 10e9, 22_000).eta!;
        expect(after).toBeGreaterThan(before - 1);
        expect(after).toBeLessThan(before * 3);
    });

    it('counts the seconds with no new bytes', () => {
        const m = new DownloadMeter();
        m.update(0, 1e9, 0);
        m.update(10e6, 1e9, 1_000);
        expect(m.update(10e6, 1e9, 17_000).stalledFor).toBe(16);
    });

    it('starts over when a download restarts from fewer bytes', () => {
        const m = new DownloadMeter();
        m.update(500e6, 1e9, 0);
        m.update(600e6, 1e9, 12_000);
        expect(m.update(0, 1e9, 13_000)).toEqual({ speed: null, eta: null, stalledFor: 0 });
    });
});

describe('DownloadMeter after a resume', () => {
    it('measures from the first real figure, not the jump from 0 to it', () => {
        const m = new DownloadMeter();
        m.update(0, 23.3e9, 0);
        // The partial file shows up: 15 GB in one second is not a speed.
        expect(m.update(15e9, 23.3e9, 1_000)).toMatchObject({ speed: null, eta: null });
        let e = m.update(15e9, 23.3e9, 1_000);
        for (let s = 2; s <= 22; s++) e = m.update(15e9 + (s - 1) * 150e6, 23.3e9, s * 1000);
        expect(e.speed).toBeCloseTo(150e6, -6);
        // 8.3 GB left - 3.15 GB done since = 5.15 GB at 150 MB/s, about 34 s.
        expect(e.eta).toBeGreaterThan(30);
        expect(e.eta).toBeLessThan(40);
    });
});
