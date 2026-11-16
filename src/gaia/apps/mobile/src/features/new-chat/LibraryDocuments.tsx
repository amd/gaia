// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import type { DocumentListResponse } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Button, Icon, IconButton, Ltr, Row, Stack, Text } from '@/ui';
import { FileText, X } from '@/ui/icons';

import { foldDocuments, splitDocuments } from './attachedDocs';

interface LibraryDocumentsProps {
    documents: DocumentListResponse['documents'];
    // The ids held for the chat about to be created.
    attachedIds: readonly string[];
    onAttach: (ids: string[]) => void;
    onRemove: (id: string) => void;
    // Opens the document library to pick which ones.
    onChoose: () => void;
}

// A chat searches only the documents attached to it: show what this one gets and offer the rest.
export function LibraryDocuments({ documents, attachedIds, onAttach, onRemove, onChoose }: LibraryDocumentsProps) {
    const t = useT();
    const [expanded, setExpanded] = useState(false);
    const { attached, offered } = splitDocuments(documents, attachedIds);
    if (!attached.length && !offered.length) return null;
    const { shown, hidden } = foldDocuments(attached, expanded);
    const n = offered.length;
    return (
        <Stack gap="sm">
            {attached.length > 0 && (
                <Stack gap="xs" accessibilityRole="list">
                    <Text role="caption" tone="secondary">
                        {t('newChat.docs.forChat')}
                    </Text>
                    {shown.map((d) => (
                        <Row key={d.id} gap="sm">
                            <Icon icon={FileText} size="sm" tone="secondary" />
                            <Stack flex={1}>
                                <Ltr>{d.filename}</Ltr>
                            </Stack>
                            <IconButton
                                icon={X}
                                size="sm"
                                label={t('newChat.docs.dontAttach', { name: d.filename })}
                                onPress={() => onRemove(d.id)}
                            />
                        </Row>
                    ))}
                </Stack>
            )}
            {hidden > 0 && (
                <Button
                    variant="ghost"
                    size="sm"
                    label={expanded ? t('newChat.docs.fewer') : t('newChat.docs.hidden', { n: hidden })}
                    onPress={() => setExpanded((v) => !v)}
                />
            )}
            {n > 0 && (
                <Stack gap="xs">
                    <Text role="label" tone="secondary">
                        {attached.length ? t.plural('newChat.docs.more', n) : t.plural('newChat.docs.library', n)}
                    </Text>
                    <Row gap="xs" wrap>
                        <Button
                            variant="ghost"
                            size="sm"
                            label={n === 1 ? t('newChat.docs.attachIt') : t('newChat.docs.attachAll', { n })}
                            onPress={() => onAttach(offered.map((d) => d.id))}
                        />
                        {n > 1 && <Button variant="ghost" size="sm" label={t('newChat.docs.choose')} onPress={onChoose} />}
                    </Row>
                </Stack>
            )}
        </Stack>
    );
}
