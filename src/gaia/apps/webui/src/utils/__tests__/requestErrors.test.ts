// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { ApiError } from '../../services/api';
import { requestErrorParam, requestErrorText } from '../requestErrors';

describe('requestErrorText', () => {
    it('maps the CLI-only Local only remedy to the Agent UI Settings guidance', () => {
        const error = new ApiError(
            'Local only is on',
            409,
            "Can't switch to a cloud model: Local only is on. To allow cloud models again, run `gaia config set network_policy cloud_allowed`.",
            null,
        );

        expect(requestErrorParam(error)).toEqual({ ref: 'settings.privacy.localOnlyBlocked' });
        expect(requestErrorText(error)).toBe('Blocked by Local only. Turn it off in Settings to use cloud models.');
    });
});