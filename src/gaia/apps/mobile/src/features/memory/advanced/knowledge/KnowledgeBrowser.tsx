// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { KNOWLEDGE_CATEGORIES, KNOWLEDGE_PAGE_SIZE, type KnowledgeEntry } from '@webui/utils/memoryFormat.core';

import type { KnowledgeListParams } from '@/api/client.types';
import { useAddMemory, useEditMemory, useForgetMemory, useKnowledgeBrowse, useMemoryEntities } from '@/data';
import { useT } from '@/i18n/useT';
import { Button, Chip, IconButton, Row, SearchField, Select, Sheet, Spinner, Stack, Switch, Text } from '@/ui';
import { Brain, ChevronLeft, ChevronRight, Plus, X } from '@/ui/icons';

import type { MemoryPaneProps } from '../../tabs';
import { SectionEmpty, SectionTitle } from '../SectionTitle';
import { SectionFailed } from '../SectionFailed';
import { KnowledgeCard } from './KnowledgeCard';
import { KnowledgeDetail } from './KnowledgeDetail';
import { KnowledgeForm } from './KnowledgeForm';
import { categoryLabel } from './knowledgeUi';

/** The web's search delay. */
export const KNOWLEDGE_SEARCH_DEBOUNCE_MS = 300;

interface KnowledgeBrowserProps extends Pick<MemoryPaneProps, 'notify'> {
    /** The contexts memories are filed under, from the stats. */
    contexts: string[];
}

/** The Advanced tab's knowledge browser: search and filters, one card per memory, paging, and the detail and form sheets. */
export function KnowledgeBrowser({ contexts, notify }: KnowledgeBrowserProps) {
    const t = useT();
    const [search, setSearch] = useState('');
    const [categories, setCategories] = useState<string[]>([]);
    const [context, setContext] = useState('');
    const [entity, setEntity] = useState('');
    const [superseded, setSuperseded] = useState(false);
    const [offset, setOffset] = useState(0);
    const [detail, setDetail] = useState<KnowledgeEntry | null>(null);
    const [form, setForm] = useState<{ item?: KnowledgeEntry; key: number } | null>(null);
    const [formCount, setFormCount] = useState(0);

    const params: KnowledgeListParams = {
        category: categories.length > 0 ? categories : undefined,
        context: context || undefined,
        entity: entity || undefined,
        search: search.trim() || undefined,
        sort_by: 'updated_at',
        order: 'desc',
        offset,
        limit: KNOWLEDGE_PAGE_SIZE,
        include_sensitive: true,
        include_superseded: superseded,
    };
    const list = useKnowledgeBrowse(params);
    const entities = useMemoryEntities();
    const add = useAddMemory();
    const edit = useEditMemory();
    const forget = useForgetMemory();
    const total = list.data?.total ?? 0;
    const items = list.data?.items;
    const filtered = !!params.search || categories.length > 0 || !!context || !!entity;

    const refilter = <T,>(set: (value: T) => void) => (value: T) => {
        set(value);
        setOffset(0);
    };
    const toggleCategory = (category: string) => {
        setCategories((prev) => (prev.includes(category) ? prev.filter((c) => c !== category) : [...prev, category]));
        setOffset(0);
    };
    const openForm = (item?: KnowledgeEntry) => {
        setFormCount((n) => n + 1);
        setForm({ item, key: formCount });
    };

    const options = (all: string, values: { value: string; label: string }[]) => [{ value: '', label: all }, ...values];

    return (
        <Stack gap="md" testID="memory-knowledge">
            <Row gap="sm" wrap>
                <Stack flex={1}>
                    <SectionTitle icon={Brain} title={t('mem.knowledge.title')} note={total > 0 ? String(total) : undefined} />
                </Stack>
                <Button label={t.bare('mem.knowledge.add')} accessibilityLabel={t('mem.knowledge.addTitle')} icon={Plus} variant="secondary" size="sm" onPress={() => openForm()} testID="memory-knowledge-add" />
            </Row>

            <SearchField
                value={search}
                onChangeText={refilter(setSearch)}
                placeholder={t.bare('mem.knowledge.searchPlaceholder')}
                accessibilityLabel={t('mem.knowledge.search')}
                debounceMs={KNOWLEDGE_SEARCH_DEBOUNCE_MS}
                testID="memory-knowledge-search"
            />
            <Row gap="sm" wrap accessibilityRole="toolbar">
                {KNOWLEDGE_CATEGORIES.map((category) => (
                    <Chip
                        key={category}
                        label={categoryLabel(category, undefined, t, true)}
                        accessibilityLabel={categoryLabel(category, undefined, t, false)}
                        selected={categories.includes(category)}
                        onPress={() => toggleCategory(category)}
                        testID={`memory-knowledge-filter-${category}`}
                    />
                ))}
                {categories.length > 0 ? (
                    <Chip label={t.bare('mem.knowledge.clear')} accessibilityLabel={t('mem.knowledge.clear')} icon={X} onPress={() => refilter(setCategories)([])} testID="memory-knowledge-filter-clear" />
                ) : null}
            </Row>
            <Select
                label={t('mem.knowledge.filterContext')}
                value={context}
                onChange={refilter(setContext)}
                options={options(t.bare('mem.knowledge.allContexts'), contexts.map((c) => ({ value: c, label: c })))}
                testID="memory-knowledge-context"
            />
            <Select
                label={t('mem.knowledge.filterEntity')}
                value={entity}
                onChange={refilter(setEntity)}
                options={options(t.bare('mem.knowledge.allEntities'), (entities.data ?? []).map((e) => ({ value: e.entity, label: `${e.entity} (${e.count})` })))}
                testID="memory-knowledge-entity"
            />
            <Switch value={superseded} onValueChange={refilter(setSuperseded)} label={t('mem.knowledge.superseded')} description={t('mem.knowledge.supersededTitle')} testID="memory-knowledge-superseded" />

            {list.isError && !items ? (
                <SectionFailed error={list.error} onRetry={() => void list.refetch()} />
            ) : !items ? (
                <Spinner />
            ) : items.length === 0 ? (
                <Stack gap="sm" align="start">
                    <SectionEmpty icon={Brain} text={t(filtered ? 'mem.knowledge.noMatch' : 'mem.knowledge.empty')} />
                    {offset > 0 ? (
                        <Button
                            label={t.bare('mem.page.previous')}
                            accessibilityLabel={t('mem.page.previous')}
                            icon={ChevronLeft}
                            variant="secondary"
                            onPress={() => setOffset(Math.max(0, offset - KNOWLEDGE_PAGE_SIZE))}
                        />
                    ) : null}
                </Stack>
            ) : (
                <Stack gap="sm" testID="memory-knowledge-list">
                    {items.map((item) => (
                        <KnowledgeCard key={item.id} item={item} onOpen={setDetail} />
                    ))}
                    <Row gap="sm" justify="between">
                        <Text role="caption" tone="muted">
                            {t('mem.page.range', { from: offset + 1, to: Math.min(offset + KNOWLEDGE_PAGE_SIZE, total), total })}
                        </Text>
                        <Row gap="xs">
                            <IconButton icon={ChevronLeft} label={t('mem.page.previous')} disabled={offset === 0} onPress={() => setOffset(Math.max(0, offset - KNOWLEDGE_PAGE_SIZE))} testID="memory-knowledge-prev" />
                            <IconButton icon={ChevronRight} label={t('mem.page.next')} disabled={offset + KNOWLEDGE_PAGE_SIZE >= total} onPress={() => setOffset(offset + KNOWLEDGE_PAGE_SIZE)} testID="memory-knowledge-next" />
                        </Row>
                    </Row>
                </Stack>
            )}

            <Sheet open={detail !== null} onClose={() => setDetail(null)} title={detail ? categoryLabel(detail.category, detail.source, t, false) : undefined}>
                {detail ? (
                    <KnowledgeDetail
                        key={detail.id}
                        item={detail}
                        edit={edit}
                        forget={forget}
                        onEdit={(item) => {
                            setDetail(null);
                            openForm(item);
                        }}
                        onChanged={setDetail}
                        onFilterEntity={(name) => {
                            setDetail(null);
                            refilter(setEntity)(name);
                        }}
                        onClose={() => {
                            setDetail(null);
                            notify({ tone: 'info', text: t('mem.toast.deleted') });
                        }}
                    />
                ) : null}
            </Sheet>
            <Sheet open={form !== null} onClose={() => setForm(null)} title={t(form?.item ? 'mem.row.edit' : 'mem.knowledge.addTitle')} dismissible>
                {form ? <KnowledgeForm key={form.key} item={form.item} add={add} edit={edit} notify={notify} onSaved={() => setForm(null)} onCancel={() => setForm(null)} /> : null}
            </Sheet>
        </Stack>
    );
}
