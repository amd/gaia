// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMemo, useState } from 'react';

import { joinAttachmentLinks, splitAttachmentLinks } from '@webui/utils/markdown';
import { useT } from '@/i18n/useT';
import { Button, Chip, Row, Stack, TextField } from '@/ui';
import { FileText } from '@/ui/icons';

export interface MessageEditorProps {
    original: string;
    /** Receives the edited text with the attachment links put back. */
    onSend: (text: string) => void;
    onCancel: () => void;
    /** A reply is running or the edit is going out. */
    disabled?: boolean;
}

/** A sent message open for editing: the typed text only, its attached files as chips. */
export function MessageEditor({ original, onSend, onCancel, disabled = false }: MessageEditorProps) {
    const t = useT();
    const { text: typed, links } = useMemo(() => splitAttachmentLinks(original), [original]);
    const [text, setText] = useState(typed);
    const changed = text.trim() !== '' && text !== typed;
    return (
        <Stack gap="sm">
            <TextField
                value={text}
                onChangeText={setText}
                multiline
                maxLines={8}
                autoFocus
                accessibilityLabel={t('msg.editLabel')}
            />
            {links.length > 0 ? (
                <Stack gap="xs">
                    {links.map((link, i) => (
                        <Chip key={i} label={link.name} icon={FileText} />
                    ))}
                </Stack>
            ) : null}
            <Row gap="sm" justify="end">
                <Button label={t('msg.editCancel')} variant="secondary" size="sm" onPress={onCancel} />
                <Button
                    label={t('msg.editSend')}
                    variant="primary"
                    size="sm"
                    disabled={!changed || disabled}
                    onPress={() => onSend(joinAttachmentLinks(text, links))}
                />
            </Row>
        </Stack>
    );
}
