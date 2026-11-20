// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { LucideIcon } from 'lucide-react-native';
import { useT } from '@/i18n/useT';
import { Button } from '../controls/Button';
import { Icon } from '../controls/Icon';
import { Pressable } from '../controls/Pressable';
import { Row, Stack } from '../layout/layout';
import { Text } from '../text/Text';
import { Sheet } from './Sheet';

export interface SheetAction {
    label: string;
    icon?: LucideIcon;
    destructive?: boolean;
    disabled?: boolean;
    onPress: () => void;
}

export interface ActionSheetProps {
    open: boolean;
    onClose: () => void;
    actions: SheetAction[];
    title?: string;
}

// Picking an action closes the sheet first, then runs it.
export function ActionSheet({ open, onClose, actions, title }: ActionSheetProps) {
    const t = useT();
    return (
        <Sheet open={open} onClose={onClose} title={title}>
            <Stack gap="xs">
                {actions.map((a) => (
                    <Pressable
                        key={a.label}
                        accessibilityLabel={a.label}
                        accessibilityState={{ disabled: !!a.disabled }}
                        disabled={a.disabled}
                        onPress={() => {
                            onClose();
                            a.onPress();
                        }}
                    >
                        <Row gap="md">
                            {a.icon ? <Icon icon={a.icon} size="md" tone={a.destructive ? 'danger' : 'secondary'} /> : null}
                            <Text role="body" tone={a.disabled ? 'muted' : a.destructive ? 'danger' : 'primary'} numberOfLines={2}>
                                {a.label}
                            </Text>
                        </Row>
                    </Pressable>
                ))}
                <Button label={t('modelMenu.cancel')} variant="secondary" full onPress={onClose} />
            </Stack>
        </Sheet>
    );
}
