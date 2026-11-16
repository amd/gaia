// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { ScreenHeader } from '@/features/nav/ScreenHeader';
import { describeError, useT } from '@/i18n/useT';
import { useConnectionStore } from '@/state/connection';
import { ActionSheet, Badge, Button, EmptyState, ErrorState, Icon, IconButton, ListRow, ListSkeleton, Notice, Row, Screen, Sheet, Stack, Text } from '@/ui';
import { Cpu, Ellipsis, Plus, Trash2 } from '@/ui/icons';

import { isCurrentPc, useForgetPc, usePairedPcs, useUsePc } from './pairedPcs';
import type { PairedPc } from './service';

/** The PCs this phone is paired with: switch to one, forget one after a confirmation, or pair another. */
export function PairedPcsScreen({ onAdd }: { onAdd: () => void }) {
    const t = useT();
    const pcs = usePairedPcs();
    const baseUrl = useConnectionStore((s) => s.connection?.baseUrl);
    const use = useUsePc();
    const forget = useForgetPc();
    const [target, setTarget] = useState<{ pc: PairedPc; mode: 'menu' | 'confirm' } | null>(null);
    const close = () => setTarget(null);
    const pc = target?.pc;
    const failure = use.error ?? forget.error;

    return (
        <Screen scroll header={<ScreenHeader title={t('mobileApp.pairing.pcs.title')} />}>
            <Stack gap="md" paddingY="md">
                {failure ? (
                    <Stack paddingX="lg">
                        <Notice tone="danger" message={describeError(failure, t)} />
                    </Stack>
                ) : null}
                {pcs.isPending ? <ListSkeleton rows={3} /> : null}
                {pcs.error ? <ErrorState title={describeError(pcs.error, t)} onRetry={() => void pcs.refetch()} /> : null}
                {pcs.data?.length === 0 ? <EmptyState title={t('mobileApp.pairing.pcs.empty')} /> : null}
                {pcs.data?.map((item) => {
                    const current = isCurrentPc(item, baseUrl);
                    return (
                        <ListRow
                            key={item.id}
                            title={item.name}
                            selected={current}
                            leading={<Icon icon={Cpu} size="md" tone="secondary" />}
                            onPress={current ? undefined : () => use.mutate(item)}
                            trailing={
                                <Row gap="xs">
                                    {current ? <Badge tone="success" label={t('mobileApp.pairing.pcs.inUse')} /> : null}
                                    <IconButton
                                        icon={Ellipsis}
                                        size="sm"
                                        label={t('mobileApp.pairing.pcs.more', { name: item.name })}
                                        onPress={() => setTarget({ pc: item, mode: 'menu' })}
                                        testID={`pc-row-${item.id}-more`}
                                    />
                                </Row>
                            }
                        />
                    );
                })}
                <Stack paddingX="lg">
                    <Button label={t('mobileApp.pairing.pcs.add')} icon={Plus} variant="secondary" onPress={onAdd} />
                </Stack>
            </Stack>
            <ActionSheet
                open={target?.mode === 'menu'}
                onClose={close}
                title={pc?.name}
                actions={[
                    ...(pc && !isCurrentPc(pc, baseUrl) ? [{ label: t('mobileApp.pairing.pcs.use'), onPress: () => use.mutate(pc) }] : []),
                    { label: t('mobileApp.pairing.pcs.forget'), icon: Trash2, destructive: true, onPress: () => pc && setTarget({ pc, mode: 'confirm' }) },
                ]}
            />
            <Sheet open={target?.mode === 'confirm'} onClose={close} title={t('mobileApp.pairing.pcs.forgetTitle', { name: pc?.name ?? '' })}>
                <Stack gap="md">
                    <Text tone="secondary">{t('mobileApp.pairing.pcs.forgetBody')}</Text>
                    <Row gap="sm">
                        <Button label={t('mobileApp.pairing.waiting.cancel')} variant="secondary" onPress={close} />
                        <Button
                            label={t('mobileApp.pairing.pcs.forgetConfirm', { name: pc?.name ?? '' })}
                            variant="danger"
                            testID="pc-forget-confirm"
                            onPress={() => {
                                close();
                                if (pc) forget.mutate(pc);
                            }}
                        />
                    </Row>
                </Stack>
            </Sheet>
        </Screen>
    );
}
