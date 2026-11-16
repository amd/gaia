// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';

import type { StartScreen } from '@webui/types';

import { useT } from '@/i18n/useT';
import { Icon, Row, Text } from '@/ui';
import { Folder } from '@/ui/icons';

import { ChipMeasure, useChipCollapsed } from './ChipFitContext';

/** Where the new chat's agent works. Choosing another folder is done on the PC. */
export function FolderChip({ folder }: { folder: StartScreen['working_folder'] | undefined }) {
    const t = useT();
    const iconOnly = useChipCollapsed('folder');
    if (!folder) return null;
    // GAIA's own folder is not named: a person never chose it.
    const named = !folder.gaia_folder;
    if (!named && folder.choosable) return null;
    const where = folder.gaia_folder ? t('folderChip.worksInOwn', { path: folder.path }) : t('folderChip.worksIn', { path: folder.path });
    const name = named ? folder.name : t('folderChip.gaiaFolder');
    const content = (compact: boolean) => (
        <Row gap="xs">
            <Icon icon={Folder} size="sm" tone="secondary" label={compact ? where : undefined} />
            {compact ? null : (
                <Text role="label" tone="secondary" numberOfLines={1} compact dir="auto">
                    {name}
                </Text>
            )}
        </Row>
    );
    return (
        <View testID="folder-chip" accessible accessibilityLabel={where} style={{ flexShrink: 0 }}>
            <ChipMeasure id="folder">{content(false)}</ChipMeasure>
            {content(iconOnly)}
        </View>
    );
}
