// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { View } from 'react-native';

import { isolate } from '@webui/i18n/registry';
import { formatTimestamp } from '@webui/utils/documentLibrary.core';
import { formatSize } from '@webui/utils/format';
import { isExtensionSupported } from '@webui/utils/uploadErrors.core';

import type { FileEntry, QuickLink } from '@/api/client.types';
import { ENDPOINTS } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';
import { useDeviceRefused } from '@/api/routeAccess';
import { useAddPcFiles, useBrowseFiles, useSearchFiles, useSetupPreflight } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import {
    ActionSheet,
    Badge,
    Button,
    Chip,
    EmptyState,
    HScroll,
    Icon,
    IconButton,
    List,
    ListRow,
    ListSkeleton,
    Notice,
    Pressable,
    Row,
    Screen,
    SearchField,
    Stack,
    Switch,
    Text,
    useTheme,
} from '@/ui';
import { ArrowUp, Brain, Check, ChevronDown, ChevronRight, Code, Eye, File, FileText, Folder, House, Monitor, Download, Table, X } from '@/ui/icons';
import type { LucideIcon } from '@/ui/icons';

import { PcOnlyNotice } from '../settings/PcOnlyNotice';
import { askPrompt, breadcrumbs, canAct, emptyKey, FILE_TYPE_FILTERS, folderName, QUICK_LINK_LABELS, visibleEntries } from './fileBrowserModel';

const QUICK_LINK_ICONS: Record<string, LucideIcon> = { home: House, desktop: Monitor, documents: FileText, download: Download };

function fileIcon(entry: { type: string; extension?: string | null }): LucideIcon {
    if (entry.type === 'folder') return Folder;
    const ext = (entry.extension || '').toLowerCase();
    if (['.csv', '.xlsx', '.xls', '.tsv'].includes(ext)) return Table;
    if (['.py', '.js', '.ts', '.java', '.c', '.cpp', '.go', '.rs'].includes(ext)) return Code;
    if (['.pdf', '.doc', '.docx', '.txt', '.md'].includes(ext)) return FileText;
    return File;
}

function extensionOf(entry: { name: string; extension?: string | null }): string {
    return entry.extension || (entry.name.includes('.') ? `.${entry.name.split('.').pop()?.toLowerCase()}` : '');
}

interface Item {
    name: string;
    path: string;
    type: 'file' | 'folder';
    size: number;
    extension: string;
    modified: string;
}

function CheckBox({ checked, disabled }: { checked: boolean; disabled: boolean }) {
    const { colors } = useTheme();
    return (
        <View
            style={{
                width: 22,
                height: 22,
                borderRadius: 6,
                borderWidth: 1.5,
                alignItems: 'center',
                justifyContent: 'center',
                borderColor: checked ? colors.accentSolid : colors.border,
                backgroundColor: checked ? colors.accentSolid : 'transparent',
                opacity: disabled ? 0.4 : 1,
            }}
        >
            {checked ? <Icon icon={Check} size="sm" tone="onAccent" /> : null}
        </View>
    );
}

export interface FileBrowserScreenProps {
    // The chat the files are added to; away from the PC it also names the folders the PC shows.
    chatId: string;
    onClose: () => void;
    // Opens a file's preview screen.
    onPreview?: (path: string) => void;
    // Sends the question into the chat the files were added to; false when that chat isn't open.
    onAsk?: (prompt: string) => boolean;
    // Where the browser opens, for the dev gallery's scenes.
    initial?: { path?: string; search?: string; selected?: string[] };
}

export function FileBrowserScreen({ chatId, onClose, onPreview, onAsk, initial }: FileBrowserScreenProps) {
    const t = useT();
    const preflight = useSetupPreflight();
    const pcIsMac = /^(mac|darwin)/i.test(preflight.data?.os ?? '');

    const [path, setPath] = useState<string | undefined>(initial?.path);
    const [text, setText] = useState(initial?.search ?? '');
    const [submitted, setSubmitted] = useState<string | null>(initial?.search ?? null);
    const [typeFilter, setTypeFilter] = useState('');
    const [filterOpen, setFilterOpen] = useState(false);
    const [showHidden, setShowHidden] = useState(false);
    const [selected, setSelected] = useState<ReadonlySet<string>>(new Set(initial?.selected));
    const [quickLinks, setQuickLinks] = useState<QuickLink[]>([]);
    const [result, setResult] = useState<{ tone: 'success' | 'danger'; message: string } | null>(null);

    const browse = useBrowseFiles(chatId, path);
    const search = useSearchFiles(chatId, submitted, typeFilter);
    const add = useAddPcFiles();
    // Documents are added to the library on the PC; the phone still browses and previews.
    const addRefused = useDeviceRefused('POST', ENDPOINTS.documentUploadPath);
    const searching = submitted !== null;

    // The links stay while a folder loads or a search runs, so they are kept from the last listing that had some.
    const listedLinks = browse.data?.quick_links;
    if (listedLinks && listedLinks.length > 0 && listedLinks !== quickLinks) setQuickLinks(listedLinks);

    const denied = path && browse.error instanceof GaiaApiError && browse.error.body?.status === 'os_denied' ? folderName(path) : null;
    const browseError = browse.isError && !denied ? describeError(browse.error, t) : null;
    const searchError = search.isError ? describeError(search.error, t) : null;
    const currentPath = browse.data?.current_path ?? path ?? '';
    const parentPath = browse.data?.parent_path ?? null;

    const entries: FileEntry[] = browse.data?.entries ?? [];
    const shown = visibleEntries(entries, showHidden);
    const items: Item[] = searching
        ? (search.data?.results ?? []).map((r): Item => ({ name: r.name, path: r.path, type: 'file' as const, size: r.size, extension: r.extension || '', modified: r.modified || '' }))
        : shown.map((e): Item => ({ name: e.name, path: e.path, type: e.type === 'folder' ? 'folder' : 'file', size: e.size ?? 0, extension: e.extension ?? '', modified: e.modified ?? '' }));
    const chosen = items.filter((i) => i.type === 'file' && selected.has(i.path)).map((i) => i.path);
    const loading = searching ? search.isFetching : browse.isPending;
    const failed = searching ? searchError : browseError;

    const open = (next: string) => {
        setPath(next);
        setSubmitted(null);
        setText('');
        setSelected(new Set());
        setResult(null);
    };
    const runSearch = () => {
        if (text.trim()) setSubmitted(text.trim());
    };
    const leaveSearch = () => {
        setSubmitted(null);
        setText('');
    };
    const toggle = (p: string) =>
        setSelected((prev) => {
            const next = new Set(prev);
            if (!next.delete(p)) next.add(p);
            return next;
        });

    const addSelected = (ask: boolean) => {
        setResult(null);
        add.mutate(
            { paths: chosen, chatId },
            {
                onSuccess: ({ added, unreadable, failures }) => {
                    const bad = unreadable.length + failures.length;
                    if (ask) {
                        if (added.length === 0) {
                            setResult({ tone: 'danger', message: t('fileBrowser.addedWithFailures', { added: 0, failed: bad }) });
                            return;
                        }
                        if (bad > 0) setResult({ tone: 'danger', message: t.plural('fileBrowser.someFailed', bad) });
                        const names = added.map((d) => d.filename);
                        if (onAsk?.(askPrompt(names, t))) setSelected(new Set());
                        else setResult({ tone: 'danger', message: t('mobileApp.files.askNoChat') });
                    } else if (bad > 0) {
                        setResult({ tone: 'danger', message: t('fileBrowser.addedWithFailures', { added: added.length, failed: bad }) });
                    } else {
                        setResult({ tone: 'success', message: t.plural('fileBrowser.added', added.length) });
                        setSelected(new Set());
                    }
                },
                onError: (e) => setResult({ tone: 'danger', message: describeError(e, t) }),
            },
        );
    };

    const crumbs = breadcrumbs(currentPath);
    const filterLabel = t(FILE_TYPE_FILTERS.find((f) => f.value === typeFilter)?.label ?? 'fileBrowser.filter.all');
    const scope = search.data?.searched_locations ?? [];
    const scopeText = scope.length > 1 ? t('fileBrowser.scopeMore', { first: scope[0], n: scope.length - 1 }) : (scope[0] ?? '');

    return (
        <Screen
            header={
                <Stack paddingX="lg" paddingY="sm">
                    <Row gap="sm" accessibilityRole="header">
                        <Stack flex={1}>
                            <Text role="title" weight="semibold" numberOfLines={1}>
                                {t('fileBrowser.title')}
                            </Text>
                        </Stack>
                        <IconButton icon={X} label={t('fileBrowser.close')} onPress={onClose} />
                    </Row>
                </Stack>
            }
            footer={
                <Stack gap="sm">
                    {addRefused ? (
                        <PcOnlyNotice testID="file-browser-pc-only" />
                    ) : (
                        <>
                            {result ? <Notice tone={result.tone} role="note" message={result.message} onDismiss={() => setResult(null)} /> : null}
                            <Text role="caption" tone="secondary">
                                {add.isPending ? t.plural('fileBrowser.adding', chosen.length) : chosen.length > 0 ? t.plural('fileBrowser.selected', chosen.length) : t('fileBrowser.selectHint')}
                            </Text>
                            <Button label={t('fileBrowser.askGaia')} icon={Brain} variant="primary" full disabled={!canAct(chosen.length, add.isPending)} onPress={() => addSelected(true)} />
                            <Button label={t('fileBrowser.addToLibrary')} variant="secondary" full disabled={!canAct(chosen.length, add.isPending)} onPress={() => addSelected(false)} />
                        </>
                    )}
                </Stack>
            }
        >
            <Stack gap="sm" paddingX="lg" paddingY="sm">
                {quickLinks.length > 0 ? (
                    <HScroll>
                        <Row gap="sm">
                            {quickLinks.map((link) => (
                                <Chip
                                    key={link.path}
                                    label={QUICK_LINK_LABELS[link.icon ?? ''] ? t(QUICK_LINK_LABELS[link.icon ?? '']) : link.name}
                                    icon={QUICK_LINK_ICONS[link.icon ?? ''] ?? Folder}
                                    onPress={() => open(link.path)}
                                />
                            ))}
                        </Row>
                    </HScroll>
                ) : null}
                <SearchField
                    value={text}
                    onChangeText={(v) => {
                        setText(v);
                        if (!v) setSubmitted(null);
                    }}
                    placeholder={t.bare('fileBrowser.searchPlaceholder')}
                    accessibilityLabel={t('fileBrowser.searchAria')}
                    onSubmitEditing={runSearch}
                />
                <Row gap="sm">
                    <Pressable accessibilityLabel={`${t('fileBrowser.typeFilter')}: ${filterLabel}`} onPress={() => setFilterOpen(true)}>
                        <Row gap="xs">
                            <Text role="label" weight="medium">
                                {filterLabel}
                            </Text>
                            <Icon icon={ChevronDown} size="sm" tone="secondary" />
                        </Row>
                    </Pressable>
                    <Stack flex={1} />
                    <Button label={search.isFetching ? t('fileBrowser.searching') : t('fileBrowser.search')} variant="secondary" disabled={!text.trim() || search.isFetching} onPress={runSearch} />
                </Row>
                {searching ? (
                    <Stack gap="xs">
                        {search.data ? <Text role="label" weight="medium">{t.plural('fileBrowser.found', search.data.results.length, { query: submitted ?? '' })}</Text> : null}
                        {scopeText ? (
                            <Text role="caption" tone="secondary">
                                {t('fileBrowser.searchingIn')} {isolate(scopeText, 'ltr')}
                            </Text>
                        ) : null}
                        {search.data?.truncated ? <Text role="caption" tone="warning">{t('fileBrowser.searchStopped')}</Text> : null}
                        <Row>
                            <Button label={t('fileBrowser.backToBrowsing')} variant="ghost" onPress={leaveSearch} />
                        </Row>
                    </Stack>
                ) : (
                    <Stack gap="xs">
                        <Row gap="xs">
                            <Stack flex={1}>
                                <HScroll dir="ltr">
                                    <Row gap="xs">
                                        {crumbs.map((crumb, i) => (
                                            <Row key={crumb.path} gap="xs">
                                                {i > 0 ? <Icon icon={ChevronRight} size="sm" tone="muted" /> : null}
                                                <Pressable inline accessibilityLabel={crumb.label} onPress={() => open(crumb.path)}>
                                                    <Text role="label" tone={i === crumbs.length - 1 ? 'primary' : 'secondary'}>
                                                        {crumb.label}
                                                    </Text>
                                                </Pressable>
                                            </Row>
                                        ))}
                                    </Row>
                                </HScroll>
                            </Stack>
                            {parentPath ? <IconButton icon={ArrowUp} label={t('fileBrowser.goUp')} onPress={() => open(parentPath)} /> : null}
                        </Row>
                        <Switch label={t('fileBrowser.showHidden')} value={showHidden} onValueChange={setShowHidden} />
                    </Stack>
                )}
                {denied ? <Notice tone="danger" message={t(pcIsMac ? 'fileBrowser.deniedMac' : 'fileBrowser.denied', { name: denied })} /> : null}
                {failed ? <Notice tone="danger" message={failed} action={{ label: t('docs.loadRetry'), onPress: () => void (searching ? search.refetch() : browse.refetch()) }} /> : null}
            </Stack>
            {loading && items.length === 0 ? <ListSkeleton rows={4} /> : null}
            {!loading && !failed && !denied && items.length === 0 ? <EmptyState title={t(emptyKey(searching, entries.length - shown.length))} /> : null}
            {items.length > 0 ? (
                <List
                    data={items}
                    keyExtractor={(i) => i.path}
                    accessibilityLabel={t('fileBrowser.title')}
                    renderItem={({ item }) => {
                        const isFolder = item.type === 'folder';
                        const ext = extensionOf(item);
                        const unsupported = !isFolder && ext !== '' && !isExtensionSupported(ext);
                        const picked = selected.has(item.path);
                        const when = item.modified ? formatTimestamp(item.modified, t) : '';
                        const subtitle = [isFolder ? '' : isolate(formatSize(item.size), 'ltr'), when].filter(Boolean).join(' · ');
                        return (
                            <ListRow
                                title={item.name}
                                subtitle={unsupported ? t('fileBrowser.notIndexableTitle') : subtitle}
                                selected={picked}
                                accessibilityLabel={isFolder ? item.name : t('fileBrowser.selectAria', { name: item.name })}
                                onPress={isFolder ? () => open(item.path) : unsupported ? undefined : () => toggle(item.path)}
                                leading={
                                    isFolder ? (
                                        <Icon icon={fileIcon(item)} size="md" tone="accent" />
                                    ) : (
                                        <Row gap="sm">
                                            <CheckBox checked={picked} disabled={unsupported} />
                                            <Icon icon={fileIcon(item)} size="md" tone="secondary" />
                                        </Row>
                                    )
                                }
                                trailing={
                                    isFolder ? (
                                        <Icon icon={ChevronRight} size="sm" tone="muted" />
                                    ) : (
                                        <Row gap="xs">
                                            {unsupported ? <Badge label={t('fileBrowser.notIndexable')} tone="warning" /> : null}
                                            {onPreview ? <IconButton icon={Eye} label={t('fileBrowser.previewAria', { name: item.name })} onPress={() => onPreview(item.path)} /> : null}
                                        </Row>
                                    )
                                }
                            />
                        );
                    }}
                />
            ) : null}
            <ActionSheet
                open={filterOpen}
                onClose={() => setFilterOpen(false)}
                title={t('fileBrowser.typeFilter')}
                actions={FILE_TYPE_FILTERS.map((f) => ({ label: t(f.label), icon: f.value === typeFilter ? Check : undefined, onPress: () => setTypeFilter(f.value) }))}
            />
        </Screen>
    );
}
