// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import {
    archiveCommand,
    formatSender,
    gmailUrl,
    isPreScanPayload,
    replyCommand,
    resolvePreScanPayload,
    type PreScanItem,
    type PreScanPayload,
} from '@webui/utils/emailPreScan.core';
import { useEffect, useRef, useState } from 'react';
import { Linking, View } from 'react-native';

import { useT } from '@/i18n/useT';
import { Button, Icon, IconButton, Row, Stack, Surface, Text, useTheme } from '@/ui';
import { Archive, CircleCheck, ExternalLink, Inbox, Mail, SquarePen, TriangleAlert, X } from '@/ui/icons';

import { sendToOpenChat } from '../sendToOpenChat';
import { UnsupportedCard } from './UnsupportedCard';

type Intent = 'urgent' | 'actionable' | 'archive';

/** How long one row's action locks the others, in case the reply never ends the wait. */
const ACTION_LOCK_MS = 5000;

/** The inbox pre-scan: rows to reply to, archive or open, then the session preferences it applied. */
export function EmailPreScanCard({ data }: { data: unknown }) {
    const payload = resolvePreScanPayload(data);
    if (!isPreScanPayload(payload)) return <UnsupportedCard variant="invalid" render="email_pre_scan" data={data} />;
    return <PreScan payload={payload} />;
}

function PreScan({ payload }: { payload: PreScanPayload }) {
    const t = useT();
    const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
    const [pending, setPending] = useState<string | null>(null);
    const [notice, setNotice] = useState(false);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(
        () => () => {
            if (timer.current) clearTimeout(timer.current);
        },
        [],
    );

    const act = (item: PreScanItem, command: string) => {
        if (pending) return;
        if (!sendToOpenChat(command)) {
            setNotice(true);
            return;
        }
        setNotice(false);
        setPending(item.message_id);
        timer.current = setTimeout(() => setPending(null), ACTION_LOCK_MS);
    };
    const open = (item: PreScanItem) => {
        const url = gmailUrl(item);
        if (url) Linking.openURL(url).catch((error: unknown) => console.error(`Opening ${url} failed`, error));
    };
    const dismiss = (item: PreScanItem) => setDismissed((prev) => new Set(prev).add(item.message_id));

    const live = (items: PreScanItem[]) => items.filter((i) => !dismissed.has(i.message_id));
    const sections: { key: Intent; title: string; icon: typeof Mail; items: PreScanItem[] }[] = [
        { key: 'urgent', title: t('mobileApp.emailPreScan.urgent'), icon: TriangleAlert, items: live(payload.urgent) },
        { key: 'actionable', title: t('mobileApp.emailPreScan.actionable'), icon: Mail, items: live(payload.actionable) },
        { key: 'archive', title: t('mobileApp.emailPreScan.archives'), icon: Archive, items: live(payload.suggested_archives) },
    ];
    const visible = sections.reduce((n, s) => n + s.items.length, 0);
    const totals = [
        t('mobileApp.emailPreScan.surfaced', { n: visible }),
        ...(payload.informational_count > 0 ? [t('mobileApp.emailPreScan.informational', { n: payload.informational_count })] : []),
    ].join(' · ');

    return (
        <Surface tone="card" radius="md" border padding="md" testID="card-email-pre-scan">
            <Stack gap="md">
                <Stack gap="xs">
                    <Row gap="sm">
                        <Icon icon={Inbox} size="sm" tone="primary" />
                        <Text role="ui" weight="semibold" accessibilityRole="header">
                            {t('mobileApp.emailPreScan.title')}
                        </Text>
                    </Row>
                    <Text role="caption" tone="muted">
                        {totals}
                    </Text>
                </Stack>
                {sections.map((s) =>
                    s.items.length > 0 ? (
                        <Section key={s.key} intent={s.key} title={s.title} icon={s.icon} count={s.items.length}>
                            {s.items.map((item, i) => (
                                <PreScanRow
                                    key={item.message_id}
                                    item={item}
                                    intent={s.key}
                                    first={i === 0}
                                    locked={pending !== null}
                                    onAct={act}
                                    onOpen={open}
                                    onDismiss={dismiss}
                                />
                            ))}
                        </Section>
                    ) : null,
                )}
                {visible === 0 ? (
                    <Row gap="sm" align="start">
                        <Icon icon={CircleCheck} size="sm" tone="muted" />
                        <Stack flex={1}>
                            <Text role="label" tone="muted">
                                {t('mobileApp.emailPreScan.empty')}
                            </Text>
                        </Stack>
                    </Row>
                ) : null}
                {notice ? (
                    <Text role="caption" tone="danger" accessibilityRole="alert">
                        {t('mobileApp.emailPreScan.noChat')}
                    </Text>
                ) : null}
                <Preferences applied={payload.preferences_applied} />
            </Stack>
        </Surface>
    );
}

function Section({ intent, title, icon, count, children }: { intent: Intent; title: string; icon: typeof Mail; count: number; children: React.ReactNode }) {
    const { colors, radius } = useTheme();
    const iconTone = intent === 'urgent' ? 'danger' : intent === 'actionable' ? 'accent' : 'secondary';
    return (
        <View
            style={{
                borderWidth: 1,
                borderColor: colors.borderLight,
                borderRadius: radius.md,
                overflow: 'hidden',
            }}
        >
            <View style={{ backgroundColor: colors.bgSecondary, paddingHorizontal: 12, paddingVertical: 8 }}>
                <Row gap="sm">
                    <Icon icon={icon} size="sm" tone={iconTone} />
                    <Stack flex={1}>
                        <Text role="caption" weight="semibold" tone="secondary" accessibilityRole="header">
                            {title}
                        </Text>
                    </Stack>
                    <View style={{ backgroundColor: colors.bgTertiary, borderRadius: radius.full, paddingHorizontal: 8, paddingVertical: 1 }}>
                        <Text role="caption" weight="semibold" tone="secondary">
                            {String(count)}
                        </Text>
                    </View>
                </Row>
            </View>
            {children}
        </View>
    );
}

function PreScanRow({
    item,
    intent,
    first,
    locked,
    onAct,
    onOpen,
    onDismiss,
}: {
    item: PreScanItem;
    intent: Intent;
    first: boolean;
    locked: boolean;
    onAct: (item: PreScanItem, command: string) => void;
    onOpen: (item: PreScanItem) => void;
    onDismiss: (item: PreScanItem) => void;
}) {
    const t = useT();
    const { colors } = useTheme();
    const reason = item.reason ?? item.why ?? '';
    const sender = item.sender ? formatSender(item.sender) : t('mobileApp.emailPreScan.unknownSender');
    const subject = item.subject || t('mobileApp.emailPreScan.noSubject');
    const archive = intent === 'archive';
    return (
        <View style={{ padding: 12, borderTopWidth: first ? 0 : 1, borderTopColor: colors.border }}>
            <Stack gap="sm">
                <Stack gap="xs">
                    <Text role="label" weight="semibold" dir="auto" numberOfLines={1}>
                        {sender}
                    </Text>
                    <Text role="label" dir="auto" numberOfLines={2}>
                        {subject}
                    </Text>
                    {reason ? (
                        <Text role="caption" tone="muted" dir="auto" numberOfLines={2}>
                            {reason}
                        </Text>
                    ) : null}
                </Stack>
                <Row gap="sm" wrap>
                    <Button
                        variant="primary"
                        size="sm"
                        icon={archive ? Archive : SquarePen}
                        label={archive ? t('mobileApp.emailPreScan.archive') : t('mobileApp.emailPreScan.reply')}
                        accessibilityLabel={t.bare(archive ? 'mobileApp.emailPreScan.archiveFor' : 'mobileApp.emailPreScan.replyFor', { subject })}
                        disabled={locked}
                        onPress={() => onAct(item, archive ? archiveCommand(item) : replyCommand(item))}
                    />
                    <Button
                        size="sm"
                        icon={ExternalLink}
                        label={t('mobileApp.emailPreScan.open')}
                        accessibilityLabel={t.bare('mobileApp.emailPreScan.openFor', { subject })}
                        onPress={() => onOpen(item)}
                    />
                    <IconButton size="sm" icon={X} label={t.bare('mobileApp.emailPreScan.dismissFor', { subject })} onPress={() => onDismiss(item)} />
                </Row>
            </Stack>
        </View>
    );
}

function Preferences({ applied }: { applied: PreScanPayload['preferences_applied'] }) {
    const t = useT();
    const { colors, radius } = useTheme();
    const priority = applied?.priority_senders?.length ?? 0;
    const low = applied?.low_priority_senders?.length ?? 0;
    const defaults = Object.entries(applied?.category_defaults ?? {});
    if (priority === 0 && low === 0 && defaults.length === 0) return null;
    const chips = [
        ...(priority > 0 ? [t.plural('mobileApp.emailPreScan.priority', priority)] : []),
        ...(low > 0 ? [t.plural('mobileApp.emailPreScan.lowPriority', low)] : []),
        ...defaults.map(([category, action]) => `${category} → ${action}`),
    ];
    return (
        <View style={{ borderTopWidth: 1, borderTopColor: colors.border, borderStyle: 'dashed', paddingTop: 8 }}>
            <Stack gap="xs" accessibilityLabel={`${t('mobileApp.emailPreScan.prefs')} ${chips.join(', ')}`}>
                <Text role="caption" weight="medium" tone="muted">
                    {t('mobileApp.emailPreScan.prefs')}
                </Text>
                <Row gap="xs" wrap>
                    {chips.map((chip) => (
                        <View key={chip} style={{ backgroundColor: colors.bgTertiary, borderRadius: radius.full, paddingHorizontal: 10, paddingVertical: 2 }}>
                            <Text role="caption" tone="secondary" dir="auto">
                                {chip}
                            </Text>
                        </View>
                    ))}
                </Row>
            </Stack>
        </View>
    );
}
