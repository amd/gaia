// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as memoryApi from '../../services/memoryApi';
import { useGoalHealthStore } from '../goalHealthStore';
import { useNotificationStore } from '../notificationStore';

vi.mock('../../services/memoryApi');

const BROKEN = 'Could not read approved goals from goals.db: file is not a database.';

function serve(error: string | null) {
    vi.mocked(memoryApi.getGoalStoreHealth).mockResolvedValueOnce({ error });
}

const errors = () => useNotificationStore.getState().notifications.filter((n) => n.type === 'error');

beforeEach(() => {
    vi.mocked(memoryApi.getGoalStoreHealth).mockReset();
    useGoalHealthStore.setState({ error: null });
    useNotificationStore.setState({ notifications: [] });
});

describe('unreadable goal store', () => {
    it('raises one error notification carrying the backend message', async () => {
        serve(BROKEN);
        await useGoalHealthStore.getState().check();
        serve(BROKEN);
        await useGoalHealthStore.getState().check();
        expect(errors()).toHaveLength(1);
        expect(errors()[0].message).toBe(BROKEN);
        expect(errors()[0].title).toBe("Approved goals aren't running");
        expect(useGoalHealthStore.getState().error).toBe(BROKEN);
    });

    it('clears once the store opens, and notifies again if it breaks again', async () => {
        serve(BROKEN);
        await useGoalHealthStore.getState().check();
        serve(null);
        await useGoalHealthStore.getState().check();
        expect(useGoalHealthStore.getState().error).toBeNull();
        serve(BROKEN);
        await useGoalHealthStore.getState().check();
        expect(errors()).toHaveLength(2);
    });

    it('notifies again when the failure changes to a different reason', async () => {
        serve(BROKEN);
        await useGoalHealthStore.getState().check();
        serve('Could not read approved goals from goals.db: database is locked.');
        await useGoalHealthStore.getState().check();
        expect(errors()).toHaveLength(2);
    });

    it('keeps the known failure and raises nothing when the backend is unreachable', async () => {
        serve(BROKEN);
        await useGoalHealthStore.getState().check();
        vi.mocked(memoryApi.getGoalStoreHealth).mockRejectedValueOnce(new Error('fetch failed'));
        await useGoalHealthStore.getState().check();
        expect(useGoalHealthStore.getState().error).toBe(BROKEN);
        expect(errors()).toHaveLength(1);
    });

    it('raises nothing while the store reads', async () => {
        serve(null);
        await useGoalHealthStore.getState().check();
        expect(errors()).toHaveLength(0);
    });
});
