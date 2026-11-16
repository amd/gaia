// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Share, View } from 'react-native';

import type { FeaturedLibrary, LinkPreview, LinkSkill } from '@/api/client.types';
import { useGaiaClient } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Badge, Button, Disclosure, Divider, Ltr, Notice, Row, Sheet, Stack, TextField, Text } from '@/ui';

import { ChipButton } from '../schedules/ChipButton';

/** Only the PC adds a skill (its files are saved there), so the phone reads and audits the link, then hands over to the PC. */
export function AddSkillsSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
    const t = useT();
    const client = useGaiaClient();
    const [url, setUrl] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [preview, setPreview] = useState<LinkPreview | null>(null);
    const [featured, setFeatured] = useState<FeaturedLibrary[]>([]);
    const [featuredError, setFeaturedError] = useState<string | null>(null);

    useEffect(() => {
        if (!open || !client) return;
        const controller = new AbortController();
        client
            .listFeaturedSkillLibraries({ signal: controller.signal })
            .then((r) => setFeatured(r.libraries))
            .catch((err) => {
                if (!controller.signal.aborted) setFeaturedError(t('settings.skills.link.featuredFailed', { error: describeError(err, t) }));
            });
        return () => controller.abort();
    }, [open, client, t]);

    const read = async (link: string) => {
        const trimmed = link.trim();
        if (!trimmed || !client) return;
        setBusy(true);
        setError(null);
        setPreview(null);
        try {
            setPreview(await client.previewSkillLink(trimmed));
        } catch (err) {
            setError(describeError(err, t));
        } finally {
            setBusy(false);
        }
    };

    const close = () => {
        setPreview(null);
        setError(null);
        onClose();
    };

    const hasCode = preview?.skills.some((s) => s.has_code && !s.error) ?? false;
    const shareable = preview?.skills.some((s) => !s.error && (!s.installed || s.update)) ?? false;

    return (
        <Sheet
            open={open}
            onClose={close}
            title={t('settings.skills.link.title')}
            footer={
                preview && shareable ? (
                    <Button
                        label={t('mobileApp.skills.shareLink')}
                        full
                        onPress={() => void Share.share({ message: preview.link })}
                    />
                ) : (
                    <Button label={t('settings.skills.link.cancel')} variant="secondary" full onPress={close} />
                )
            }
        >
            <Stack gap="md">
                {featured.length > 0 ? (
                    <Row gap="sm" wrap>
                        {featured.map((f) => (
                            <ChipButton
                                key={f.id}
                                variant="plain"
                                label={t('settings.skills.link.get', { name: f.name })}
                                disabled={busy}
                                onPress={() => {
                                    setUrl(f.url);
                                    void read(f.url);
                                }}
                            />
                        ))}
                    </Row>
                ) : null}
                {featuredError ? <Text role="label" tone="danger">{featuredError}</Text> : null}
                <TextField
                    value={url}
                    onChangeText={setUrl}
                    url
                    placeholder={t.bare('settings.skills.link.placeholder')}
                    accessibilityLabel={t('settings.skills.link.aria')}
                    hint={t('settings.skills.link.hint')}
                    returnKeyType="go"
                    onSubmitEditing={() => void read(url)}
                    disabled={busy}
                />
                <Button
                    label={t(busy ? 'settings.skills.link.reading' : 'settings.skills.link.read')}
                    variant="secondary"
                    full
                    disabled={!url.trim() || busy}
                    onPress={() => void read(url)}
                />
                {error ? <Notice tone="danger" message={error} /> : null}
                {preview ? (
                    <Stack gap="md">
                        <Notice tone="warning" title={t('settings.skills.link.trust')} message={t('mobileApp.skills.trustBody')} />
                        <Text role="ui" tone="secondary">
                            {t('settings.skills.link.found', { n: preview.skills.length, source: preview.source })}
                        </Text>
                        <View>
                            {preview.skills.map((s, i) => (
                                <View key={s.path}>
                                    {i > 0 ? <Divider /> : null}
                                    <PreviewRow skill={s} />
                                </View>
                            ))}
                        </View>
                        {shareable ? (
                            <Notice
                                tone="info"
                                title={t('mobileApp.skills.pcTitle')}
                                message={`${t('mobileApp.skills.pcBody')}${hasCode ? ` ${t('mobileApp.skills.pcBodyCode')}` : ''}`}
                            />
                        ) : null}
                    </Stack>
                ) : null}
            </Stack>
        </Sheet>
    );
}

function PreviewRow({ skill: s }: { skill: LinkSkill }) {
    const t = useT();
    const tag = s.error
        ? t(s.verdict === 'BLOCK' ? 'settings.skills.link.tagBlocked' : 'settings.skills.link.tagUnreadable')
        : s.installed && !s.update
          ? t('settings.skills.link.tagElsewhere')
          : s.update
            ? t('settings.skills.link.tagUpdate')
            : null;
    const why = s.error ?? (s.installed && !s.update ? t('settings.skills.link.whyElsewhere') : s.update ? t('settings.skills.link.whyUpdate') : null);
    const [open, setOpen] = useState(false);
    const hasDetails = !!why || s.findings.length > 0 || s.has_code;
    return (
        <View style={{ paddingVertical: 10 }}>
            <Stack gap="xs">
                <Row gap="sm" wrap>
                    <Text role="ui" weight="medium">{s.name}</Text>
                    {tag ? <Badge label={tag} tone={s.error ? 'danger' : 'neutral'} /> : null}
                    {s.has_code ? <Badge label={t('settings.skills.link.tagCode')} /> : null}
                    {s.verdict === 'REVIEW' ? <Badge label={t('settings.skills.link.tagReview')} tone="warning" /> : null}
                </Row>
                {s.description ? <Text role="label" tone="secondary">{s.description}</Text> : null}
                {s.permissions.length > 0 ? (
                    <Stack gap="xs" accessibilityRole="list">
                        <Text role="label" tone="secondary">{t('settings.skills.link.can')}</Text>
                        {s.permissions.map((p) => (
                            <Text key={p.grant} role="label">
                                {p.text} <Ltr>{p.grant}</Ltr>
                            </Text>
                        ))}
                    </Stack>
                ) : (
                    <Text role="label" tone="secondary">
                        {t(s.permissions_unreadable ? 'settings.skills.link.permissionsUnreadable' : 'settings.skills.link.noPermissions')}
                    </Text>
                )}
                {hasDetails ? (
                    <Disclosure title={t('settings.skills.link.details')} open={open} onToggle={() => setOpen(!open)}>
                        <Stack gap="xs">
                            {why ? <Text role="label">{why}</Text> : null}
                            {s.has_code ? <Text role="label">{t('mobileApp.skills.pcBodyCode')}</Text> : null}
                            {s.findings.map((f) => <Text key={f} role="label">{f}</Text>)}
                        </Stack>
                    </Disclosure>
                ) : null}
            </Stack>
        </View>
    );
}
