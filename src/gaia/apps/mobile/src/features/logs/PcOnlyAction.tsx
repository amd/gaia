// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { useT } from '@/i18n/useT';
import { Button, Notice, Stack, Text } from '@/ui';
import type { LucideIcon } from '@/ui/icons';

/** A web button whose route refuses a phone: it stays, and pressing it shows why (never fake data). */
export function PcOnlyAction({ label, icon, note, detail, testID }: {
    label: string;
    icon: LucideIcon;
    /** The web's note under the button. */
    note?: string;
    /** The phone-perspective line shown with the refusal. */
    detail: string;
    testID: string;
}) {
    const t = useT();
    const [pressed, setPressed] = useState(false);
    const refusal = t('mobileApp.pcOnly.refusal');
    const generic = t('mobileApp.settings.pcOnly');
    return (
        <Stack gap="sm">
            <Button full wrap label={label} icon={icon} onPress={() => setPressed(true)} testID={testID} />
            {note ? <Text role="caption" tone="muted">{note}</Text> : null}
            {pressed ? (
                <Notice tone="info" message={detail === generic ? refusal : `${refusal} ${detail}`} testID={`${testID}-refusal`} />
            ) : null}
        </Stack>
    );
}
