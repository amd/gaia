// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';
import { useEffect } from 'react';

import { useGoalStoreError, useQueryFailure, useSystemStatus } from '@/data';
import { useT } from '@/i18n/useT';
import { openChat } from '@/features/nav/navigation';
import { useAnnounce } from '@/platform/useAnnounce';
import { useBannerDismissedStore } from '@/state/bannerDismissed';
import { useChatAlertBannerStore, type ChatAlertBanner } from '@/state/chatAlertBanner';
import { Notice, ProgressLine, Stack } from '@/ui';

import { pickBanner } from './pickBanner';

function ChatAlertNotice({ alert }: { alert: ChatAlertBanner }) {
    const t = useT();
    const router = useRouter();
    const dismiss = useChatAlertBannerStore((s) => s.dismiss);
    return (
        <Notice
            tone={alert.tone}
            title={alert.title}
            message={alert.message}
            onDismiss={dismiss}
            action={{
                label: t('mobileApp.banner.openChat'),
                onPress: () => {
                        dismiss();
                        openChat(router, alert.sessionId);
                    },
                }}
            testID="banner-chat-alert"
        />
    );
}

/** The banner for the top of a `Screen`: a chat alert, then connection trouble; nothing while all is well or no PC is connected. */
export function ConnectionBanner() {
    const t = useT();
    const status = useSystemStatus();
    const { data, refetch } = status;
    const { error } = useQueryFailure(status);
    const goalStoreError = useGoalStoreError().data;
    const picked = pickBanner(data, error, t, goalStoreError);
    const { dismissed, dismiss, reconcile } = useBannerDismissedStore();
    useEffect(() => reconcile(picked), [picked?.id, picked?.dismissible, reconcile]); // eslint-disable-line react-hooks/exhaustive-deps
    const banner = picked && picked.id === dismissed ? null : picked;
    const alert = useChatAlertBannerStore((s) => s.alert);
    useAnnounce([alert?.title, alert?.message, banner?.title, banner?.message].filter(Boolean).join('. '));
    if (!banner && !alert) return null;
    return (
        <Stack gap="sm">
            {alert ? <ChatAlertNotice alert={alert} /> : null}
            {banner ? (
                <Stack gap="sm" testID={`banner-${banner.id}`}>
                    <Notice
                        tone={banner.tone}
                        title={banner.title}
                        message={banner.message}
                        onDismiss={banner.dismissible ? () => dismiss(banner.id) : undefined}
                        action={banner.retry ? { label: t('settings.key.checkAgain'), onPress: () => void refetch() } : undefined}
                    />
                    {banner.progress ? <ProgressLine value={banner.progress.value} label={banner.progress.label} /> : null}
                </Stack>
            ) : null}
        </Stack>
    );
}
