// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// @vitest-environment node

import { describe, expect, it } from 'vitest';
import schemas from '../api.schemas.json';
import committed from '../api.gen.ts?raw';
import { generateApiTypes, REGENERATE_HINT } from '../../../scripts/gen-api-types.mjs';

const normalize = (text: string) => text.replace(/\r\n/g, '\n');

describe('generated API types', () => {
    it('api.gen.ts matches a fresh generation from api.schemas.json', async () => {
        const generated = await generateApiTypes(schemas);
        expect(
            normalize(committed) === normalize(generated),
            `src/types/api.gen.ts is stale. Regenerate from the repo root: ${REGENERATE_HINT}`,
        ).toBe(true);
    });

    it('covers the backend models the hand-written types are checked against', () => {
        for (const name of ['SystemStatus', 'SettingsResponse', 'AgentInfo', 'ScheduleResponse']) {
            expect(committed).toContain(`export interface ${name} {`);
        }
    });
});
