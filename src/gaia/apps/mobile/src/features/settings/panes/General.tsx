// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { View } from 'react-native';

import { LOCALES, localeOptionLabel, type MessageKey } from '@webui/i18n/registry';
import { DEFAULT_CUSTOM, PALETTES, isHexColor } from '@webui/utils/palette.core';

import { DirectionNotice } from '@/features/language/DirectionNotice';
import { useLocaleCode, useT } from '@/i18n/useT';
import { notifyDirectionChange } from '@/state/direction';
import { useLocaleStore } from '@/state/locale';
import { Button, Disclosure, Icon, Pressable, Row, SegmentedControl, Select, Stack, Switch, Text, TextField, useAppearance, useTheme } from '@/ui';
import { Check } from '@/ui/icons';
import { themeColors, type AppearancePaletteId, type Scheme } from '@/ui/theme/appearance';

import { SettingsAnchor } from '../targets';

const SCHEMES: { value: 'light' | 'dark' | 'system'; label: MessageKey }[] = [
    { value: 'light', label: 'settings.theme.light' },
    { value: 'dark', label: 'settings.theme.dark' },
    { value: 'system', label: 'settings.theme.system' },
];

const PALETTE_NAME: Record<string, MessageKey> = {
    default: 'palette.default',
    stone: 'palette.stone',
    marble: 'palette.marble',
    glacier: 'palette.glacier',
    forest: 'palette.forest',
    sage: 'palette.sage',
    basalt: 'palette.basalt',
    gold: 'palette.gold',
    dusk: 'palette.dusk',
    custom: 'settings.colors.custom',
};

const PRESETS = [...new Set([...PALETTES.map((p) => p.swatch[1]), DEFAULT_CUSTOM.base])];

function PaletteTile({ id, scheme, selected, onPress }: { id: AppearancePaletteId; scheme: Scheme; selected: boolean; onPress: () => void }) {
    const t = useT();
    const { radius, space } = useTheme();
    const custom = useAppearance((s) => s.custom);
    const own = themeColors(scheme, id, custom);
    const label = t(PALETTE_NAME[id]);
    return (
        <View style={{ flexBasis: '47%', flexGrow: 1 }}>
            <Pressable onPress={onPress} accessibilityRole="radio" accessibilityLabel={label} accessibilityState={{ checked: selected }}>
                <View
                    style={{
                        minHeight: 48,
                        flexDirection: 'row',
                        alignItems: 'center',
                        gap: space.sm,
                        padding: space.sm,
                        borderRadius: radius.md,
                        borderWidth: selected ? 2 : 1,
                        borderColor: selected ? own.accentSolid : own.border,
                        backgroundColor: own.bgPrimary,
                    }}
                >
                    <View style={{ width: 20, height: 20, borderRadius: radius.full, backgroundColor: own.accentSolid }} />
                    <View style={{ flex: 1, minWidth: 0 }}>
                        <Text role="label" weight="medium">
                            {label}
                        </Text>
                    </View>
                    {selected ? <Icon icon={Check} size="sm" tone="accent" /> : null}
                </View>
            </Pressable>
        </View>
    );
}

function ColorField({ label, hint, value, onCommit }: { label: string; hint: string; value: string; onCommit: (hex: string) => void }) {
    const t = useT();
    const { radius, space } = useTheme();
    const [draft, setDraft] = useState(value);
    const [seen, setSeen] = useState(value);
    if (seen !== value) {
        setSeen(value);
        setDraft(value);
    }
    const normalized = draft.startsWith('#') ? draft : `#${draft}`;
    const invalid = draft.length > 0 && !isHexColor(normalized);
    return (
        <Stack gap="sm">
            <TextField
                label={label}
                hint={hint}
                value={draft}
                onChangeText={(text) => {
                    setDraft(text);
                    const hex = text.startsWith('#') ? text : `#${text}`;
                    if (isHexColor(hex)) onCommit(hex.toUpperCase());
                }}
                error={invalid ? t('mobileApp.settings.colors.invalidHex') : undefined}
                placeholder={DEFAULT_CUSTOM.accent}
            />
            <Row gap="sm" wrap>
                {PRESETS.map((hex) => (
                    <Pressable key={hex} onPress={() => onCommit(hex)} accessibilityLabel={`${label} ${hex}`} accessibilityRole="radio" accessibilityState={{ checked: hex === value.toUpperCase() }}>
                        <View
                            style={{
                                width: 36,
                                height: 36,
                                borderRadius: radius.full,
                                backgroundColor: hex,
                                alignItems: 'center',
                                justifyContent: 'center',
                                borderWidth: 1,
                                borderColor: 'transparent',
                                margin: space.xs / 2,
                            }}
                        >
                            {hex === value.toUpperCase() ? <Icon icon={Check} size="sm" tone="inverse" /> : null}
                        </View>
                    </Pressable>
                ))}
            </Row>
        </Stack>
    );
}

export default function General() {
    const t = useT();
    const { scheme } = useTheme();
    const locale = useLocaleCode();
    const setLocale = useLocaleStore((s) => s.setLocale);
    const { scheme: preference, palette, custom, showResources, setScheme, setPalette, setCustom, setShowResources } = useAppearance();
    const [colorsOpen, setColorsOpen] = useState(palette === 'custom');

    return (
        <Stack gap="lg" padding="lg">
            <SettingsAnchor name="ui-language">
                <Stack gap="xs">
                    <Select
                        label={t('settings.language')}
                        value={locale}
                        options={LOCALES.map(({ meta }) => ({ value: meta.code, label: localeOptionLabel(meta) }))}
                        onChange={(code) => {
                            setLocale(code);
                            const picked = LOCALES.find(({ meta }) => meta.code === code);
                            if (picked) notifyDirectionChange(picked.meta.dir);
                        }}
                    />
                    <Text role="label" tone="muted">
                        {t('settings.languageHint')}
                    </Text>
                    <DirectionNotice />
                </Stack>
            </SettingsAnchor>

            <Stack gap="xs">
                <Text role="label" weight="medium" tone="secondary" accessibilityRole="header">
                    {t('settings.appearance')}
                </Text>
                <SegmentedControl
                    options={SCHEMES.map((s) => ({ value: s.value, label: t.bare(s.label), accessibilityLabel: t(s.label) }))}
                    value={preference}
                    onChange={(v) => setScheme(v as typeof preference)}
                    accessibilityLabel={t('settings.appearance')}
                />
            </Stack>

            <Switch label={t('resources.settings')} description={t('mobileApp.resources.settingsHint')} value={showResources} onValueChange={setShowResources} />

            <Disclosure title={t('settings.colors')} open={colorsOpen} onToggle={() => setColorsOpen((o) => !o)}>
                <Stack gap="md">
                    <View accessibilityRole="radiogroup" accessibilityLabel={t('settings.colors')} style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
                        {[...PALETTES.map((p) => p.id), 'custom' as const].map((id) => (
                            <PaletteTile key={id} id={id} scheme={scheme} selected={palette === id} onPress={() => setPalette(id)} />
                        ))}
                    </View>
                    {palette === 'custom' ? (
                        <Stack gap="md">
                            <ColorField label={t('settings.colors.accent')} hint={t('settings.colors.accentHint')} value={custom.accent} onCommit={(accent) => setCustom({ ...custom, accent })} />
                            <ColorField label={t('settings.colors.base')} hint={t('settings.colors.baseHint')} value={custom.base} onCommit={(base) => setCustom({ ...custom, base })} />
                            <Stack gap="xs" align="start">
                                <Button variant="secondary" size="sm" label={t('settings.colors.reset')} onPress={() => setCustom(DEFAULT_CUSTOM)} />
                                <Text role="label" tone="muted">
                                    {t('settings.colors.hint')}
                                </Text>
                            </Stack>
                        </Stack>
                    ) : null}
                </Stack>
            </Disclosure>
        </Stack>
    );
}
