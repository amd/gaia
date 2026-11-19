// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { tEn } from '../../../i18n/useT';
import { DownloadMeter, formatEta, formatSize, formatSpeed } from '../format';
import { copyableCommand } from '../GpuMemoryPanel';
import { driveName } from '../ModelChecklist';

describe('sizes, speeds and times', () => {
    it('shows decimal GB to one place, and MB under 1 GB', () => {
        expect(formatSize(23.3)).toBe('23.3 GB');
        expect(formatSize(0.3)).toBe('300 MB');
        expect(formatSize(0)).toBe('0.0 GB');
        expect(formatSize(0.0004)).toBe('1 MB');
    });

    it('shows speed in MB/s', () => {
        expect(formatSpeed(18_000_000)).toBe('18 MB/s');
        expect(formatSpeed(2_500_000)).toBe('2.5 MB/s');
    });

    it('rounds the ETA as the spec says', () => {
        expect(formatEta(40, tEn)).toBe('about 40 sec');
        expect(formatEta(13 * 60, tEn)).toBe('about 13 min');
        expect(formatEta(3 * 3600, tEn)).toBe('over 2 hours');
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
    it('estimates nothing for the first 10 seconds', () => {
        const m = new DownloadMeter();
        m.update(0, 100e6, 0);
        expect(m.update(20e6, 100e6, 5_000)).toMatchObject({ speed: null, eta: null });
        expect(m.update(40e6, 100e6, 9_000).eta).toBeNull();
        const e = m.update(50e6, 100e6, 10_000);
        expect(e.speed).toBeGreaterThan(0);
        expect(e.eta).toBeGreaterThan(0);
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
        // Ten seconds at a crawl while the next pull starts: a huge estimate.
        for (let s = 1; s <= 10; s++) m.update(s * 1e6, 30e9, s * 1000);
        const high = m.update(11e6, 30e9, 11_000).eta!;
        // Then 900 MB/s with about 7 GB to go.
        let bytes = 23e9;
        let eta = high;
        for (let s = 12; s <= 20; s++) {
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
        for (let s = 2; s <= 12; s++) e = m.update(15e9 + (s - 1) * 150e6, 23.3e9, s * 1000);
        expect(e.speed).toBeCloseTo(150e6, -6);
        // 8.3 GB left - 1.65 GB done since = 6.65 GB at 150 MB/s, about 44 s.
        expect(e.eta).toBeGreaterThan(40);
        expect(e.eta).toBeLessThan(50);
    });
});
