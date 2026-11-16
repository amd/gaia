// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef, useState } from 'react';

import type { CustomModelReport } from '@/api/client.types';
import { useAddCustomModel, useCheckCustomModel } from '@/data';
import { useT } from '@/i18n/useT';
import { Button, ChoiceList, Disclosure, Icon, Notice, Row, SettingsGroup, Stack, Text, TextField } from '@/ui';
import { Check, TriangleAlert, X } from '@/ui/icons';

import { errorText, isolate, refusedOnPhone } from './modelText';

const EXAMPLE_LINK = 'https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF';

export interface AddCustomModelProps {
    open: boolean;
    onToggle: () => void;
    onClose: () => void;
    /** Called with the new model's name once it is on the list. */
    onAdded: (name: string) => void;
}

/** Paste a Hugging Face link, see what GAIA checked, and add the model to the list. */
export function AddCustomModel({ open, onToggle, onClose, onAdded }: AddCustomModelProps) {
    const t = useT();
    const check = useCheckCustomModel();
    const add = useAddCustomModel();
    const [url, setUrl] = useState('');
    // A quantization's first file: unique even when two variants share a quant name.
    const [file, setFile] = useState('');
    const [report, setReport] = useState<CustomModelReport | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [passedOpen, setPassedOpen] = useState(false);
    // The link the newest check was for; an older answer is dropped.
    const asked = useRef('');
    const busy = check.isPending || add.isPending;

    const fail = (err: unknown) => setError(refusedOnPhone(err) ? t('mobileApp.settings.pcOnly') : errorText(err, t));

    const runCheck = (quant: string) => {
        const link = url.trim();
        asked.current = link;
        setError(null);
        check.mutate({ url: link, quant }, {
            onSuccess: (r) => {
                if (asked.current !== link) return;
                setReport(r);
                setFile(r.file ?? quant);
            },
            onError: (err) => {
                if (asked.current !== link) return;
                setReport(null);
                fail(err);
            },
        });
    };

    const runAdd = () => {
        setError(null);
        add.mutate({ url: url.trim(), quant: file }, {
            onSuccess: ({ report: added }) => {
                setUrl('');
                setFile('');
                setReport(null);
                onAdded(added.display_name ?? added.model_id ?? t('settings.custom.theModel'));
                onClose();
            },
            onError: fail,
        });
    };

    const checks = report ? (
        <Stack gap="xs">
            {report.ok ? null : <Text role="caption" weight="semibold" tone="secondary" accessibilityRole="header">{t('settings.custom.checks')}</Text>}
            {report.checks.map((c) => (
                <Row key={c.id} gap="sm" align="start">
                    <Icon icon={c.ok ? Check : X} size="sm" tone={c.ok ? 'success' : 'danger'} label={c.ok ? t('settings.custom.passed') : t('settings.custom.failed')} />
                    <Stack flex={1}>
                        <Text role="caption"><Text role="caption" weight="semibold">{c.label}</Text>{` — ${c.detail}`}</Text>
                    </Stack>
                </Row>
            ))}
        </Stack>
    ) : null;

    return (
        <SettingsGroup title={t('settings.custom.title')} open={open} onToggle={onToggle}>
            <Stack gap="md" paddingY="sm">
                <Text role="caption" tone="muted">{t('mobileApp.settings.custom.hint')}</Text>
                <TextField
                    value={url}
                    onChangeText={(v) => { setUrl(v); setReport(null); setFile(''); setError(null); asked.current = ''; }}
                    label={t('settings.custom.title')}
                    placeholder={EXAMPLE_LINK}
                    returnKeyType="go"
                    onSubmitEditing={() => { if (url.trim() && !busy) runCheck(''); }}
                    url
                    disabled={busy}
                    testID="custom-model-url"
                />
                <Button
                    label={t.bare('settings.custom.check')}
                    accessibilityLabel={t('settings.custom.check')}
                    onPress={() => runCheck('')}
                    busy={check.isPending}
                    disabled={busy || !url.trim()}
                    testID="custom-model-check"
                />
                {report && report.ok ? (
                    <Disclosure title={t('settings.custom.allPassed', { n: report.checks.length })} open={passedOpen} onToggle={() => setPassedOpen((o) => !o)}>
                        {checks}
                    </Disclosure>
                ) : checks}
                {report && report.warnings.length > 0 ? (
                    <Stack gap="xs">
                        <Text role="caption" weight="semibold" tone="secondary" accessibilityRole="header">{t('settings.custom.warnings')}</Text>
                        {report.warnings.map((w) => (
                            <Row key={w} gap="sm" align="start">
                                <Icon icon={TriangleAlert} size="sm" tone="warning" label={t('settings.custom.warning')} />
                                <Stack flex={1}>
                                    <Text role="caption">{w}</Text>
                                </Stack>
                            </Row>
                        ))}
                    </Stack>
                ) : null}
                {report && report.variants.length > 1 ? (
                    <ChoiceList
                        accessibilityLabel={t('settings.custom.quantization')}
                        options={report.variants.map((v) => ({
                            value: v.file,
                            label: `${v.quant} · ${isolate(`${v.size_gb} GB`)}${v.fits ? '' : ` · ${t.bare('mobileApp.settings.custom.tooLarge')}`}`,
                        }))}
                        value={file}
                        onChange={(v) => { if (!busy) runCheck(v as string); }}
                    />
                ) : null}
                {report?.ok ? (
                    <Stack gap="xs">
                        <Button
                            variant="primary"
                            label={t('settings.custom.add', { name: report.display_name ?? '' })}
                            onPress={runAdd}
                            busy={add.isPending}
                            disabled={busy}
                            full
                            wrap
                            testID="custom-model-add"
                        />
                        <Text role="caption" tone="muted">{t('settings.model.sizeDownload', { size: isolate(`${report.size_gb} GB`) })}</Text>
                    </Stack>
                ) : null}
                {error ? <Notice tone={error === t('mobileApp.settings.pcOnly') ? 'info' : 'danger'} message={error} /> : null}
            </Stack>
        </SettingsGroup>
    );
}
