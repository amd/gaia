// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { LOCALES, localeOptionLabel } from '@webui/i18n/registry';

import { useLocaleCode, useT } from '@/i18n/useT';
import { notifyDirectionChange } from '@/state/direction';
import { useLocaleStore } from '@/state/locale';
import { Chip, Row, Stack, Text } from '@/ui';

/** The interface language from the first screen on: one chip per locale, English and Arabic. */
export function LanguagePicker() {
    const t = useT();
    const current = useLocaleCode();
    const setLocale = useLocaleStore((s) => s.setLocale);
    return (
        <Stack gap="xs" accessibilityRole="radiogroup">
            <Text role="caption" tone="secondary">{t('settings.language')}</Text>
            <Row gap="sm" wrap>
                {LOCALES.map(({ meta }) => (
                    <Chip
                        key={meta.code}
                        label={localeOptionLabel(meta)}
                        selected={meta.code === current}
                        onPress={() => {
                            setLocale(meta.code);
                            notifyDirectionChange(meta.dir);
                        }}
                    />
                ))}
            </Row>
        </Stack>
    );
}
