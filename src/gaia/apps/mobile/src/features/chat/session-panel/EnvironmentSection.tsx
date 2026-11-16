// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';

import type { ActiveModel, LocalModelStatus, PermissionMode, ScopeEntry, Session, SessionEnvironment } from '@/api/client.types';
import { sessionEnvironmentPath } from '@/api/constants';
import { useDeviceRefused } from '@/api/routeAccess';
import { useActiveModel, useEnvironmentActions, useLocalModelStatus, usePermissionMode, useSession, useSessionEnvironment } from '@/data';
import { isPcRefusal, PcOnlyNotice } from '@/features/settings/PcOnlyNotice';
import { useT, type Translate } from '@/i18n/useT';
import { Button, Icon, IconButton, Notice, Row, SegmentedControl, Stack, Switch, Text } from '@/ui';
import { Cloud, FileText, Folder, FolderPlus, Monitor, X } from '@/ui/icons';
import { isolate } from '@webui/i18n/registry';

import { failureReason, failureReasonWith } from '../header/failureText';
import { FolderPicker } from './FolderPicker';
import { useSessionPanelStore } from './panelStore';
import type { SectionProps } from './sections';
import type { MessageKey } from '@webui/i18n/registry';

const SOURCE_LABEL: Record<ScopeEntry['source'], MessageKey> = {
    added: 'env.source.added',
    gaia_documents: 'env.source.gaia_documents',
    scratch: 'env.source.scratch',
    document: 'env.source.document',
    working_folder: 'env.source.working_folder',
    approved: 'env.source.approved',
    allowed_paths: 'env.source.allowed_paths',
};

const FILES_SHOWN = 3;

function baseName(path: string): string {
    const parts = path.split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] ?? path;
}

export function EnvironmentSection({ sessionId, live }: SectionProps) {
    const { data: env, error } = useSessionEnvironment(sessionId, { enabled: true, live });
    return (
        <Stack gap="lg">
            <Folders sessionId={sessionId} env={env} error={error} />
            <Permission sessionId={sessionId} />
            <Internet sessionId={sessionId} env={env} />
            <RunsOn sessionId={sessionId} />
        </Stack>
    );
}

function Group({ label, children }: { label: string; children: ReactNode }) {
    return (
        <Stack gap="sm">
            <Text weight="semibold" accessibilityRole="header">
                {label}
            </Text>
            {children}
        </Stack>
    );
}

function Folders({ sessionId, env, error }: { sessionId: string; env: SessionEnvironment | undefined; error: unknown }) {
    const t = useT();
    const session = useSession(sessionId).data;
    const { addFolder, removeFolder } = useEnvironmentActions(sessionId);
    const refused = useDeviceRefused('POST', sessionEnvironmentPath(sessionId, 'folderRemove'));
    const addRefused = useDeviceRefused('POST', sessionEnvironmentPath(sessionId, 'folders'));
    const picking = useSessionPanelStore((s) => s.envPicking);
    const setPicking = useSessionPanelStore((s) => s.setEnvPicking);
    const envConfirm = useSessionPanelStore((s) => s.envConfirm);
    const setEnvConfirm = useSessionPanelStore((s) => s.setEnvConfirm);
    const confirming = envConfirm?.kind === 'remove' ? envConfirm.path : null;
    const [allFiles, setAllFiles] = useState(false);

    const locked = !env || !env.enforced;
    const label = env && !env.enforced ? t('env.reach') : t('env.readWrite');
    if (isPcRefusal(error)) {
        return (
            <Group label={label}>
                <PcOnlyNotice testID="environment-pc-only" />
            </Group>
        );
    }
    const folders = env?.folders.filter((f) => f.kind === 'folder') ?? [];
    const files = env?.folders.filter((f) => f.kind === 'file') ?? [];
    const shownFiles = allFiles ? files : files.slice(0, FILES_SHOWN);
    const confirm = (path: string) => {
        setEnvConfirm(null);
        removeFolder.mutate(path);
    };
    const row = (entry: ScopeEntry) => (
        <FolderRow
            key={entry.path}
            entry={entry}
            t={t}
            canRemove={entry.removable && !locked && !refused}
            busy={removeFolder.isPending}
            confirming={confirming === entry.path}
            onAsk={() => setEnvConfirm({ kind: 'remove', path: entry.path })}
            onCancel={() => setEnvConfirm(null)}
            onConfirm={() => confirm(entry.path)}
        />
    );
    return (
        <Group label={label}>
            {!env && !error ? <Text tone="muted">{t('sp.checking')}</Text> : null}
            {env && !env.enforced ? <Text role="caption" tone="muted">{t(session?.agent_type === 'gaia' ? 'env.flagshipScope' : 'env.agentScope')}</Text> : null}
            {error ? <Notice tone="danger" message={t.compose((say) => say('env.foldersError', { error: failureReasonWith(error, say) }))} /> : null}
            {env ? (
                <>
                    {env.folders.length === 0 ? <Text tone="secondary">{t('env.nothing')}</Text> : null}
                    <Stack gap="md" accessibilityRole="list">
                        {folders.map(row)}
                        {shownFiles.map(row)}
                    </Stack>
                    {files.length > FILES_SHOWN ? (
                        <Button
                            variant="ghost"
                            size="sm"
                            label={allFiles ? t('env.fewerFiles') : t('env.allFiles', { n: files.length })}
                            onPress={() => setAllFiles((v) => !v)}
                        />
                    ) : null}
                    {env.removed.length > 0 ? (
                        <Text role="caption" tone="muted">
                            {t('env.removed', { names: isolate(env.removed.map(baseName).join(', '), 'ltr') })}
                        </Text>
                    ) : null}
                    {picking ? (
                        <FolderPicker
                            sessionId={sessionId}
                            busy={addFolder.isPending}
                            error={isPcRefusal(addFolder.error) ? undefined : addFolder.error}
                            onClose={() => setPicking(false)}
                            onPick={(path) => addFolder.mutate(path, { onSuccess: () => setPicking(false) })}
                        />
                    ) : !locked && !addRefused ? (
                        <Button
                            variant="secondary"
                            size="sm"
                            icon={FolderPlus}
                            label={t('env.addFolder')}
                            onPress={() => {
                                addFolder.reset();
                                setPicking(true);
                            }}
                            testID="environment-add-folder"
                        />
                    ) : null}
                </>
            ) : null}
            {isPcRefusal(addFolder.error) ? <PcOnlyNotice testID="environment-add-pc-only" /> : null}
            {removeFolder.error ? (
                isPcRefusal(removeFolder.error) ? <PcOnlyNotice testID="environment-remove-pc-only" /> : <Notice tone="danger" message={failureReason(removeFolder.error, t)} />
            ) : null}
        </Group>
    );
}

interface FolderRowProps {
    entry: ScopeEntry;
    t: Translate;
    canRemove: boolean;
    busy: boolean;
    confirming: boolean;
    onAsk(): void;
    onCancel(): void;
    onConfirm(): void;
}

function FolderRow({ entry, t, canRemove, busy, confirming, onAsk, onCancel, onConfirm }: FolderRowProps) {
    const name = entry.name || baseName(entry.path);
    return (
        <Stack gap="sm" testID={`environment-folder-${entry.path}`}>
            <Row gap="md" align="start">
                <Icon icon={entry.kind === 'folder' ? Folder : FileText} size="sm" tone="muted" />
                <Stack flex={1} gap="xs">
                    <Text weight="medium">{isolate(name, 'ltr')}</Text>
                    <Text role="caption" tone="muted">
                        {`${t(SOURCE_LABEL[entry.source])} · ${isolate(entry.path, 'ltr')}`}
                    </Text>
                </Stack>
                {canRemove ? (
                    <IconButton
                        icon={X}
                        size="sm"
                        variant="ghost"
                        label={t('env.remove', { path: isolate(entry.path, 'ltr') })}
                        disabled={busy}
                        onPress={onAsk}
                        testID={`environment-remove-${entry.path}`}
                    />
                ) : null}
            </Row>
            {confirming ? (
                <Stack gap="sm">
                    <Text>{t('mobileApp.env.removeConfirm', { name: isolate(name, 'ltr') })}</Text>
                    <Row gap="sm">
                        <Button label={t('docs.remove')} variant="danger" size="sm" onPress={onConfirm} testID="environment-remove-confirm" />
                        <Button label={t('sp.cancel')} variant="ghost" size="sm" onPress={onCancel} />
                    </Row>
                </Stack>
            ) : null}
        </Stack>
    );
}

function Internet({ sessionId, env }: { sessionId: string; env: SessionEnvironment | undefined }) {
    const t = useT();
    const { setInternet } = useEnvironmentActions(sessionId);
    const refused = useDeviceRefused('PUT', sessionEnvironmentPath(sessionId, 'internet'));
    if (!env) return null;
    const state = env.internet ? t('env.on') : t('env.offBlocked');
    return (
        <Group label={t('env.internet')}>
            {refused || isPcRefusal(setInternet.error) ? (
                <>
                    <Text>{env.internet ? t('env.on') : t('env.off')}</Text>
                    <PcOnlyNotice testID="environment-internet-pc-only" />
                </>
            ) : !env.internet_enforced ? (
                <Text>{env.internet ? t('env.on') : t('env.off')}</Text>
            ) : (
                <>
                    <Switch
                        value={env.internet}
                        onValueChange={(on) => setInternet.mutate(on)}
                        disabled={setInternet.isPending}
                        label={t('env.internetFor')}
                        description={state}
                        testID="environment-internet"
                    />
                    {setInternet.error ? <Notice tone="danger" message={failureReason(setInternet.error, t)} /> : null}
                </>
            )}
        </Group>
    );
}

const MODE_NOTE: Record<PermissionMode, MessageKey> = {
    ask: 'perm.askNote',
    full_access: 'perm.fullNote',
};

/** The chat's permission mode, shared with the composer's chip through the same query. */
function Permission({ sessionId }: { sessionId: string }) {
    const t = useT();
    const { mode, set } = usePermissionMode(sessionId);
    const confirming = useSessionPanelStore((s) => s.envConfirm?.kind === 'full');
    const setEnvConfirm = useSessionPanelStore((s) => s.setEnvConfirm);
    const choose = (next: PermissionMode) => {
        // Full access turns on only from "Turn it on", so a stray tap can't grant it.
        if (next === 'full_access') setEnvConfirm({ kind: 'full' });
        else set.mutate(next);
    };
    return (
        <Group label={t('env.permission')}>
            {mode ? (
                <>
                    <SegmentedControl
                        accessibilityLabel={t('env.permission')}
                        options={[
                            { value: 'ask', label: t.bare('env.ask'), accessibilityLabel: t('env.ask') },
                            { value: 'full_access', label: t.bare('perm.full'), accessibilityLabel: t('perm.full') },
                        ]}
                        value={mode}
                        onChange={(v) => choose(v === 'full_access' ? 'full_access' : 'ask')}
                    />
                    <Text role="caption" tone="muted">{t(MODE_NOTE[mode])}</Text>
                </>
            ) : set.error ? null : (
                <Text tone="muted">{t('sp.checking')}</Text>
            )}
            {confirming ? (
                <Stack gap="sm">
                    <Notice tone="warning" message={t('mobileApp.env.fullAccessWarn')} />
                    <Row gap="sm">
                        <Button
                            label={t('env.turnOn')}
                            size="sm"
                            variant="danger"
                            onPress={() => {
                                setEnvConfirm(null);
                                set.mutate('full_access');
                            }}
                            testID="environment-full-access-confirm"
                        />
                        <Button label={t('sp.cancel')} size="sm" variant="ghost" onPress={() => setEnvConfirm(null)} />
                    </Row>
                </Stack>
            ) : null}
            {set.error ? (isPcRefusal(set.error) ? <PcOnlyNotice testID="environment-mode-pc-only" /> : <Notice tone="danger" message={failureReason(set.error, t)} />) : null}
        </Group>
    );
}

interface Place {
    remote: boolean;
    name: string | null;
    description: string | null;
}

/** Where this chat is answered: a cloud provider the chat or the picked model names, this PC, or unknown. */
function placeOf(session: Session | undefined, active: ActiveModel | undefined): Place | null {
    const pickIsStale = !!session?.effective_model && !!active?.model && session.effective_model !== active.model;
    if (active?.remote && !(pickIsStale && session?.inference_remote !== undefined && session?.inference_remote !== null)) {
        return { remote: true, name: active.provider_name ?? null, description: null };
    }
    if (session?.inference_remote === true) return { remote: true, name: session.inference_provider_name ?? null, description: session.inference_description ?? null };
    if (session?.inference_remote === false) return { remote: false, name: null, description: null };
    if (!session && active?.inference_remote === false) return { remote: false, name: null, description: null };
    return null;
}

/** Lemonade ids compare case-insensitively, with or without the `user.` prefix. */
function sameModel(a: string, b: string): boolean {
    const norm = (id: string) => id.toLowerCase().replace(/^user\./, '');
    return norm(a) === norm(b);
}

function RunsOn({ sessionId }: { sessionId: string }) {
    const t = useT();
    const session = useSession(sessionId);
    const active = useActiveModel();
    const place = placeOf(session.data, active.data);
    const local = useLocalModelStatus(false);
    const label = t('env.runsOn');
    if (place?.remote) {
        return (
            <Group label={label}>
                <Row gap="sm">
                    <Icon icon={Cloud} size="sm" tone="muted" />
                    <Text>{t('env.cloudPlace', { name: place.name ?? t('ctx.cloud') })}</Text>
                </Row>
                {place.description ? <Text role="caption" tone="muted">{place.description}</Text> : null}
            </Group>
        );
    }
    if (!place) {
        return (
            <Group label={label}>
                <Text tone="muted">{session.isPending || active.isPending ? t('sp.checking') : t('env.notKnownYet')}</Text>
            </Group>
        );
    }
    return (
        <Group label={label}>
            <LocalPlace status={local.data} error={local.error} chatModel={session.data?.effective_model || active.data?.model || null} t={t} />
        </Group>
    );
}

function LocalPlace({ status, error, chatModel, t }: { status: LocalModelStatus | undefined; error: unknown; chatModel: string | null; t: Translate }) {
    // The server may have another chat's model loaded; describe it only when it is this chat's.
    const other = !!(status?.model && chatModel && !sameModel(status.model, chatModel));
    const memory = other ? null : status?.memory;
    const device = !other && status?.placement?.label ? ` · ${status.placement.label}` : '';
    return (
        <Stack gap="xs">
            <Row gap="sm">
                <Icon icon={Monitor} size="sm" tone="muted" />
                <Text>{`${t('mobileApp.env.yourPc')}${device}`}</Text>
            </Row>
            {other ? (
                <Text role="caption" tone="muted">{t('env.notLoaded', { model: isolate(chatModel!, 'ltr') })}</Text>
            ) : status?.display_name ? (
                <Text role="caption" tone="muted">{isolate(`${status.display_name}${status.state !== 'loaded' ? ` · ${status.state}` : ''}`, 'ltr')}</Text>
            ) : null}
            {memory ? (
                <Text role="caption" tone="muted">
                    {memory.model_gb != null ? t('env.inUse', { gb: isolate(memory.model_gb.toFixed(1), 'ltr') }) : t('env.inUseUnknown')}
                    {memory.free_gb != null ? ` · ${t('env.free', { gb: isolate(memory.free_gb.toFixed(1), 'ltr') })}` : ''}
                </Text>
            ) : null}
            {error ? <Notice tone="danger" message={failureReason(error, t)} /> : status?.error ? <Notice tone="danger" message={status.error} /> : null}
            {!status && !error ? <Text role="caption" tone="muted">{t('sp.checking')}</Text> : null}
        </Stack>
    );
}
