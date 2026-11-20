// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { snapValue } from './sliderMath';
import { segmentsFit } from './segments';

describe('snapValue', () => {
    it('snaps to the step grid from min and clamps to the range', () => {
        expect(snapValue(52, 0, 100, 5)).toBe(50);
        expect(snapValue(3, 1, 10, 2)).toBe(3);
        expect(snapValue(4.2, 1, 10, 2)).toBe(5);
        expect(snapValue(-8, 0, 100, 5)).toBe(0);
        expect(snapValue(999, 0, 100, 5)).toBe(100);
        expect(snapValue(98, 0, 100, 15)).toBe(100);
    });

    it('keeps fractional steps free of float noise', () => {
        expect(snapValue(0.30000000004, 0, 1, 0.1)).toBe(0.3);
    });
});

describe('segmentsFit', () => {
    it('is false when the widest label overflows its segment', () => {
        expect(segmentsFit([60, 70], 390)).toBe(true);
        expect(segmentsFit([60, 190], 390)).toBe(false);
    });

    it('fits "Match system" beside two short labels at phone width', () => {
        expect(segmentsFit([92, 40, 40], 342)).toBe(true);
    });
});
