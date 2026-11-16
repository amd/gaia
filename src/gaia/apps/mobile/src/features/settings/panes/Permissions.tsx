// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { ENDPOINTS } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';
import { useDeviceRefused } from '@/api/routeAccess';
import type { SessionPermissions } from '@/api/client.types';
import { useAllPermissions, usePermissionActions, useSessions } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import {
    ActionSheet, Badge, Button, Card, Disclosure, Divider, ErrorState, IconButton, ListSkeleton, Ltr, Notice, Row, Sheet, Stack, Switch, Text,
} from '@/ui';
import { Ellipsis } from '@/ui/icons';

import { isPcRefusal, PcOnlyNotice } from '../PcOnlyNotice';
import { SettingsAnchor } from '../targets';

/** Settings > Permissions: the default mode for new chats, and every chat holding full access or "always allow" grants. */
export default function Permissions() {
    const t = useT();
    const all = useAllPermissions();
    const sessions = useSessions();
    const { setDefault, setChatMode, revoke } = usePermissionActions();
    const defaultRefused = useDeviceRefused('PUT', ENDPOINTS.chatPermissionsDefault);
    const [confirming, setConfirming] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [howOpen, setHowOpen] = useState(false);
    const [chatsOpen, setChatsOpen] = useState(true);
    const [menuFor, setMenuFor] = useState<SessionPermissions | null>(null);

    const fail = (err: unknown) => setError(err instanceof GaiaApiError && err.status === 403 ? t('mobileApp.settings.pcOnly') : describeError(err, t));
    const run = (pending: Promise<unknown>) => {
        setError(null);
        pending.catch(fail);
    };

    const titleOf = (id: string) => sessions.data?.sessions.find((s) => s.id === id)?.title ?? id;
    const defaultMode = all.data?.default_mode ?? null;
    const rows = all.data?.sessions ?? [];

    return (
        <SettingsAnchor name="pane">
            <Stack gap="lg" paddingX="lg" paddingY="md">
                <Text role="ui" tone="secondary">{t('settings.permissions.lede')}</Text>
                <Disclosure title={t('settings.model.howItWorks')} open={howOpen} onToggle={() => setHowOpen((v) => !v)}>
                    <Text role="caption" tone="muted">{t('settings.permissions.perChat')}</Text>
                </Disclosure>

                <Card padding="lg">
                    <Stack gap="sm">
                        {defaultRefused ? (
                            <>
                                <Text role="ui" weight="medium">{t('settings.permissions.fullAccess')}</Text>
                                {defaultMode === null ? null : (
                                    <Text role="caption" tone="muted">{defaultMode === 'full_access' ? t('settings.permissions.on') : t('settings.permissions.off')}</Text>
                                )}
                                <PcOnlyNotice testID="permissions-default-pc-only" />
                            </>
                        ) : (
                            <Switch
                                label={t('settings.permissions.fullAccess')}
                                description={defaultMode === null ? undefined : defaultMode === 'full_access' ? t('settings.permissions.on') : t('settings.permissions.off')}
                                value={defaultMode === 'full_access'}
                                disabled={defaultMode === null || setDefault.isPending}
                                onValueChange={(on) => (on ? setConfirming(true) : run(setDefault.mutateAsync('ask')))}
                                testID="permissions-default-switch"
                            />
                        )}
                    </Stack>
                </Card>

                {error ? <Notice tone={error === t('mobileApp.settings.pcOnly') ? 'info' : 'danger'} message={error} testID="permissions-error" /> : null}

                {all.isError && isPcRefusal(all.error) ? (
                    <PcOnlyNotice testID="permissions-pc-only" />
                ) : all.isError ? (
                    <ErrorState title={describeError(all.error, t)} onRetry={() => void all.refetch()} />
                ) : all.isPending ? (
                    <ListSkeleton rows={2} />
                ) : rows.length === 0 ? (
                    <Text role="caption" tone="muted">{t('settings.permissions.none')}</Text>
                ) : (
                    <Disclosure
                        title={t('settings.permissions.chats')}
                        open={chatsOpen}
                        onToggle={() => setChatsOpen((v) => !v)}
                        trailing={<Badge shape="pill" label={String(rows.length)} />}
                    >
                        <Stack gap="md">
                            {rows.map((r) => (
                                <Card key={r.session_id} padding="md">
                                    <Stack gap="sm">
                                        <Row gap="sm">
                                            <Stack flex={1} gap="xs">
                                                <Text role="label" weight="medium" dir="auto" numberOfLines={2}>{titleOf(r.session_id)}</Text>
                                                <Row><Badge label={t.bare(r.mode === 'full_access' ? 'perm.full' : 'perm.ask')} accessibilityLabel={t(r.mode === 'full_access' ? 'perm.full' : 'perm.ask')} tone={r.mode === 'full_access' ? 'warning' : 'neutral'} /></Row>
                                            </Stack>
                                            {r.mode === 'full_access' || r.grants.length > 1 ? (
                                                <IconButton
                                                    icon={Ellipsis}
                                                    variant="ghost"
                                                    label={t('row.moreActions', { title: titleOf(r.session_id) })}
                                                    onPress={() => setMenuFor(r)}
                                                />
                                            ) : null}
                                        </Row>
                                        {r.grants.map((g) => (
                                            <Stack key={g.key} gap="xs">
                                                <Divider />
                                                <Row gap="sm">
                                                    <Stack flex={1}><Text code numberOfLines={2}><Ltr>{g.label}</Ltr></Text></Stack>
                                                    <Button
                                                        label={t.bare('settings.permissions.revoke')}
                                                        accessibilityLabel={t('settings.permissions.revokeAria', { name: g.label })}
                                                        variant="ghost"
                                                        size="sm"
                                                        onPress={() => run(revoke.mutateAsync({ sessionId: r.session_id, key: g.key }))}
                                                    />
                                                </Row>
                                            </Stack>
                                        ))}
                                    </Stack>
                                </Card>
                            ))}
                        </Stack>
                    </Disclosure>
                )}
            </Stack>

            <Sheet
                open={confirming}
                onClose={() => setConfirming(false)}
                title={t('settings.permissions.fullAccess')}
                footer={
                    <Stack gap="sm">
                        <Button
                            label={t('settings.permissions.turnOn')}
                            variant="danger"
                            full
                            onPress={() => {
                                setConfirming(false);
                                run(setDefault.mutateAsync('full_access'));
                            }}
                        />
                        <Button label={t('settings.key.cancel')} variant="secondary" full onPress={() => setConfirming(false)} />
                    </Stack>
                }
            >
                <Text role="body">{t('mobileApp.settings.permissionsWarning')}</Text>
            </Sheet>

            <ActionSheet
                open={menuFor !== null}
                onClose={() => setMenuFor(null)}
                title={menuFor ? titleOf(menuFor.session_id) : undefined}
                actions={[
                    ...(menuFor?.mode === 'full_access'
                        ? [{ label: t('settings.permissions.turnOff'), onPress: () => run(setChatMode.mutateAsync({ sessionId: menuFor.session_id, mode: 'ask' })) }]
                        : []),
                    ...(menuFor && menuFor.grants.length > 1
                        ? [{ label: t('settings.permissions.revokeAll'), destructive: true, onPress: () => run(revoke.mutateAsync({ sessionId: menuFor.session_id })) }]
                        : []),
                ]}
            />
        </SettingsAnchor>
    );
}
