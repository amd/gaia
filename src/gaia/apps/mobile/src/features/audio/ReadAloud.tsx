// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError, GaiaConfigError, GaiaError } from '@/api/errors';
import { useGaiaClient } from '@/data';
import { describeError, technicalDetail, useT, type Translate } from '@/i18n/useT';
import { IconButton, Notice, Row, Spinner } from '@/ui';
import { Square, Volume2 } from '@/ui/icons';

import { useReadAloudStore } from './readAloudStore';

export interface ReadAloudControl {
    /** This reply is the one being read, or being fetched to read. */
    active: boolean;
    /** The speech is still being made (or the voice model downloaded). */
    loading: boolean;
    label: string;
    toggle(): void;
}

/** Read or stop one reply; `messageId` is `<session>:<message>`, as the web keys it. */
export function useReadAloud(messageId: string, content: string): ReadAloudControl {
    const t = useT();
    const client = useGaiaClient();
    const active = useReadAloudStore((s) => s.messageId === messageId);
    const status = useReadAloudStore((s) => (s.messageId === messageId ? s.status : null));
    return {
        active,
        loading: status === 'loading' || status === 'downloading',
        label: active ? t('readAloud.stop') : t('readAloud.read'),
        toggle: () => {
            const store = useReadAloudStore.getState();
            if (active) {
                store.stop();
                return;
            }
            if (!client) {
                useReadAloudStore.setState({ error: { messageId, kind: 'other', cause: new GaiaConfigError('mobileApp.error.noConnection') } });
                return;
            }
            void store.read(client, messageId, content);
        },
    };
}

/** The speaker button; while the speech is being made it spins beside a Stop that still works. */
export function ReadAloudButton({ control, testID }: { control: ReadAloudControl; testID?: string }) {
    return (
        <Row gap="xs">
            {control.loading ? <Spinner size="sm" /> : null}
            <IconButton
                icon={control.active ? Square : Volume2}
                label={control.label}
                size="sm"
                variant={control.active ? 'filled' : 'ghost'}
                onPress={control.toggle}
                testID={testID}
            />
        </Row>
    );
}

/** While the first read downloads the voice model, says so under the reply. */
export function ReadAloudNote({ messageId }: { messageId: string }) {
    const t = useT();
    const downloading = useReadAloudStore((s) => s.messageId === messageId && s.status === 'downloading');
    const size = useReadAloudStore((s) => s.downloadGb);
    if (!downloading) return null;
    const text = t('readAloud.downloading');
    return <Notice tone="info" message={size != null ? t.hint(text, `~${size} GB`) : text} />;
}

/** The PC's or the phone's own words for what failed, shown under the translated line. */
function detailOf(cause: unknown, t: Translate): string | undefined {
    if (cause == null) return undefined;
    if (cause instanceof GaiaApiError) return cause.detail || undefined;
    return cause instanceof GaiaError ? describeError(cause, t) : technicalDetail(cause);
}

/** Why reading this reply aloud stopped, until dismissed. */
export function ReadAloudErrorNotice({ messageId }: { messageId: string }) {
    const t = useT();
    const error = useReadAloudStore((s) => (s.error?.messageId === messageId ? s.error : null));
    const clear = useReadAloudStore((s) => s.clearError);
    if (!error) return null;
    if (error.kind === 'unsupported') return <Notice tone="danger" title={t('mobileApp.readAloud.unsupported')} onDismiss={clear} />;
    return <Notice tone="danger" title={t(`readAloud.error.${error.kind}`)} message={detailOf(error.cause, t)} onDismiss={clear} />;
}
