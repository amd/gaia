// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '@/i18n/useT';
import { Eye, EyeOff } from '@/ui/icons';

import { ControlChip } from './ControlChip';

/** New-chat toggle: the chat is created private, so not even its first message is remembered. */
export function PrivateChip({ on, onChange, disabled }: { on: boolean; onChange: (on: boolean) => void; disabled?: boolean }) {
    const t = useT();
    return (
        <ControlChip
            label={t.bare(on ? 'composer.private' : 'composer.memoryOn')}
            icon={on ? EyeOff : Eye}
            chip="private"
            warning={false}
            disabled={disabled}
            onPress={() => onChange(!on)}
            accessibilityLabel={on ? t('composer.privateOff') : t('composer.privateOn')}
            testID="private-chip"
        />
    );
}
