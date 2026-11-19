// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RunActivity } from '../RunActivity';
import type { SetupRunStatus } from '../../../types';

const RUN = { state: 'running', percent: 100 } as SetupRunStatus;

describe('RunActivity', () => {
    it('draws its own bar by default', () => {
        render(<RunActivity run={RUN} idle="Getting ready" />);
        expect(document.querySelector('progress')).not.toBeNull();
    });

    it('keeps to one line under a screen that already has a bar (#7711)', () => {
        render(<RunActivity run={RUN} idle="Getting ready" bar={false} />);
        expect(screen.getByRole('status')).toHaveTextContent('Getting ready');
        expect(document.querySelector('progress')).toBeNull();
    });
});
