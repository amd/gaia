// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { isServerDown } from '@webui/utils/serverDown';

import { useContextChoice, useSetContextChoice } from '@/data';
import { useT } from '@/i18n/useT';
import { Disclosure, Notice, Row, Select, Spinner, Stack, Text } from '@/ui';

import { isPcRefusal, PcOnlyNotice } from '../../PcOnlyNotice';
import { SettingsAnchor } from '../../targets';
import { AUTOMATIC, contextOutcome, ctxLabel, errorText, isolate } from './modelText';

/** Context size for one local model: Automatic, or a size up to the most that fits this PC. */
export function ContextSize({ model }: { model: string }) {
    const t = useT();
    const choice = useContextChoice(model, true);
    const save = useSetContextChoice();
    const [notice, setNotice] = useState<string | null>(null);
    const [aboutOpen, setAboutOpen] = useState(false);
    const view = choice.data;

    const apply = (value: string) => {
        setNotice(null);
        save.mutate(
            { model, ctxSize: value === AUTOMATIC ? null : Number(value) },
            { onSuccess: (next) => setNotice(contextOutcome(next, t)) },
        );
    };

    if (!view) {
        const down = choice.error && isServerDown(errorText(choice.error, t));
        return (
            <SettingsAnchor name="ctx-size">
                {isPcRefusal(choice.error) ? (
                    <PcOnlyNotice testID="ctx-size-pc-only" />
                ) : choice.error ? (
                    down ? (
                        <Text role="caption" tone="muted">{t('settings.ctx.serverDown')}</Text>
                    ) : (
                        <Notice tone="danger" message={errorText(choice.error, t)} />
                    )
                ) : (
                    <Row gap="sm">
                        <Spinner />
                        <Text role="caption" tone="muted">{t('settings.ctx.checking')}</Text>
                    </Row>
                )}
            </SettingsAnchor>
        );
    }

    const options = [
        {
            value: AUTOMATIC,
            label: view.automatic
                ? t.bare('settings.ctx.automaticAt', { size: isolate(ctxLabel(view.automatic)) })
                : t.bare('settings.ctx.automatic'),
        },
        ...view.options.map((o) => ({ value: String(o), label: t.bare('settings.ctx.tokens', { size: isolate(ctxLabel(o)) }) })),
    ];
    const loadedDiffers = view.chosen != null && view.loaded_ctx != null && view.loaded_ctx !== view.chosen;

    return (
        <SettingsAnchor name="ctx-size">
            <Stack gap="sm">
                <Select
                    label={t('settings.ctx.label')}
                    value={view.chosen != null ? String(view.chosen) : AUTOMATIC}
                    options={options}
                    onChange={apply}
                    disabled={save.isPending || view.options.length === 0}
                />
                {view.note ? <Text role="caption" tone="muted">{view.note}</Text> : null}
                {!view.note && view.max_fit ? (
                    <Disclosure title={t('settings.ctx.about')} open={aboutOpen} onToggle={() => setAboutOpen((o) => !o)}>
                        <Text role="caption" tone="muted">
                            {t('mobileApp.settings.ctx.explain', {
                                size: isolate(ctxLabel(view.max_fit)),
                                name: view.display_name,
                                memory: view.memory_source,
                            })}
                        </Text>
                    </Disclosure>
                ) : null}
                {loadedDiffers ? (
                    <Text role="caption" tone="muted">{t('settings.ctx.loadedAt', { size: isolate(ctxLabel(view.loaded_ctx)) })}</Text>
                ) : null}
                {save.isPending ? (
                    <Row gap="sm">
                        <Spinner />
                        <Text role="caption" tone="muted">{t('settings.ctx.applying')}</Text>
                    </Row>
                ) : null}
                {notice ? <Notice tone="success" message={notice} /> : null}
                {isPcRefusal(save.error) ? <PcOnlyNotice testID="ctx-size-pc-only" /> : save.error ? <Notice tone="danger" message={errorText(save.error, t)} /> : null}
            </Stack>
        </SettingsAnchor>
    );
}
