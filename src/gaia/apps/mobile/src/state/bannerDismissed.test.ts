// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Banner } from '@/features/banners/pickBanner';

import { useBannerDismissedStore } from './bannerDismissed';

const banner = (id: Banner['id'], dismissible = true): Banner => ({ id, tone: 'warning', title: id, dismissible });
const { dismiss, reconcile } = useBannerDismissedStore.getState();
const dismissed = () => useBannerDismissedStore.getState().dismissed;

describe('bannerDismissed', () => {
    beforeEach(() => useBannerDismissedStore.setState({ dismissed: null }));

    it('keeps a dismissal while its banner stays, drops it when another kind or none shows', () => {
        dismiss('serverStopped');
        reconcile(banner('serverStopped'));
        expect(dismissed()).toBe('serverStopped');

        reconcile(banner('contextTooSmall'));
        expect(dismissed()).toBeNull();

        dismiss('serverStopped');
        reconcile(null);
        expect(dismissed()).toBeNull();
    });

    it('does not hold a dismissal for a banner that can no longer be dismissed', () => {
        dismiss('download');
        reconcile(banner('download', false));
        expect(dismissed()).toBeNull();
    });
});
