// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { useT } from '@/i18n/useT';
import { ActionSheet, IconButton } from '@/ui';
import { Camera, FileText, Image, Plus } from '@/ui/icons';

import { CameraDeniedError, pickCamera, pickDocuments, pickPhotos, type PickedFile } from './pickFiles';

/** The + button: a menu to attach a photo or a file. */
export function AttachButton({ onPick, onError, disabled }: { onPick(files: PickedFile[]): void; onError(name: string, message: string): void; disabled?: boolean }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    return (
        <>
            <IconButton
                icon={Plus}
                label={t('composer.attach')}
                onPress={() => setOpen(true)}
                disabled={disabled}
                testID="composer-attach"
            />
            <ActionSheet
                open={open}
                onClose={() => setOpen(false)}
                title={t('composer.attach')}
                actions={[
                    {
                        label: t('mobileApp.composer.attachCamera'),
                        icon: Camera,
                        onPress: () =>
                            void pickCamera().then(onPick, (err: unknown) => {
                                if (!(err instanceof CameraDeniedError)) throw err;
                                onError(t('mobileApp.composer.attachCamera'), t('mobileApp.composer.cameraOff'));
                            }),
                    },
                    { label: t('composer.attachPhoto'), icon: Image, onPress: () => void pickPhotos().then(onPick) },
                    { label: t('composer.attachFile'), icon: FileText, onPress: () => void pickDocuments().then(onPick) },
                ]}
            />
        </>
    );
}
