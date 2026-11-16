// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';
import { useT } from '@/i18n/useT';
import {
    Disclosure,
    Icon,
    ProgressLine,
    Screen,
    ScrollBox,
    SearchField,
    SegmentedControl,
    Select,
    Slider,
    Stack,
    Switch,
    Text,
    TextField,
} from '@/ui';
import { Wrench } from '@/ui/icons';

function Section({ title, children }: { title: string; children: ReactNode }) {
    return (
        <Stack gap="sm" paddingY="md">
            <Text role="caption" weight="semibold" tone="muted">
                {title}
            </Text>
            {children}
        </Stack>
    );
}

export function FormsGallery() {
    const t = useT();
    const [on, setOn] = useState(true);
    const [off, setOff] = useState(false);
    const [seg, setSeg] = useState('a');
    const [segLong, setSegLong] = useState('a');
    const [pick, setPick] = useState<string | undefined>(undefined);
    const [pick2, setPick2] = useState('b');
    const [level, setLevel] = useState(40);
    const [speed, setSpeed] = useState(100);
    const [query, setQuery] = useState('');
    const [debounced, setDebounced] = useState('');
    const [secret, setSecret] = useState('abc123');
    const [open, setOpen] = useState(true);
    const long = t('chatError.context');
    const short = [
        { value: 'a', label: t('modelMenu.ready') },
        { value: 'b', label: t('env.offline') },
        { value: 'c', label: t('row.status.running') },
    ];
    const longOptions = [
        { value: 'a', label: long },
        { value: 'b', label: t('chatError.connection') },
    ];
    const selectOptions = [
        { value: 'a', label: t('modelMenu.ready'), description: t('logs.hint.service') },
        { value: 'b', label: long },
        { value: 'c', label: t('env.offline') },
    ];

    return (
        <Screen scroll>
            <Stack paddingX="lg" paddingY="lg" gap="md">
                <Section title="Switch">
                    <Switch value={on} onValueChange={setOn} label={t('modelStatus.model')} description={t('logs.hint.service')} />
                    <Switch value={off} onValueChange={setOff} label={long} />
                    <Switch value={true} onValueChange={() => {}} label={t('env.offline')} disabled />
                    <Switch value={false} onValueChange={() => {}} label={t('env.offline')} disabled />
                </Section>

                <Section title="SegmentedControl">
                    <SegmentedControl options={short} value={seg} onChange={setSeg} accessibilityLabel={t('modelStatus.model')} />
                    <SegmentedControl options={longOptions} value={segLong} onChange={setSegLong} accessibilityLabel={t('modelStatus.model')} />
                </Section>

                <Section title="Select">
                    <Select label={t('modelStatus.model')} value={pick} options={selectOptions} onChange={setPick} placeholder={t('modelMenu.ready')} />
                    <Select label={long} value={pick2} options={selectOptions} onChange={setPick2} />
                    <Select label={t('modelStatus.model')} value="a" options={selectOptions} onChange={() => {}} disabled />
                </Section>

                <Section title="Slider">
                    <Slider label={t('modelStatus.model')} value={level} onChange={setLevel} min={0} max={100} step={5} formatValue={(v) => `${v}%`} />
                    <Slider label={long} value={speed} onChange={setSpeed} min={50} max={200} step={25} formatValue={(v) => `${v / 100}x`} />
                    <Slider label={t('env.offline')} value={30} onChange={() => {}} disabled />
                </Section>

                <Section title="SearchField">
                    <SearchField value={query} onChangeText={setQuery} placeholder={t('fileBrowser.searchAria')} />
                    <SearchField value={debounced} onChangeText={setDebounced} placeholder={t('fileBrowser.searchAria')} debounceMs={300} />
                    <Text role="caption" tone="muted">{`${query} | ${debounced}`}</Text>
                </Section>

                <Section title="TextField (revealable)">
                    <TextField label={t('modelStatus.model')} value={secret} onChangeText={setSecret} secureTextEntry revealable />
                </Section>

                <Section title="ProgressLine">
                    <ProgressLine value={0.4} label={t('row.status.running')} />
                </Section>

                <Section title="Disclosure leading, ScrollBox">
                    <Disclosure title={t('modelStatus.model')} open={open} onToggle={() => setOpen((o) => !o)} leading={<Icon icon={Wrench} size="sm" tone="muted" />}>
                        <ScrollBox maxHeight={120}>
                            <Text role="caption" mono>
                                {Array.from({ length: 12 }, (_, i) => `Log line ${i + 1}: ${long}`).join('\n')}
                            </Text>
                        </ScrollBox>
                    </Disclosure>
                </Section>
            </Stack>
        </Screen>
    );
}
