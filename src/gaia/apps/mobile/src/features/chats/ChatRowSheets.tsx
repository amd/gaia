// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { MAX_SESSION_TITLE_CHARS } from '@webui/utils/constants';

import { useT } from '@/i18n/useT';
import { ActionSheet, Button, Row, Sheet, Stack, TextField, type SheetAction } from '@/ui';
import { Pencil, Pin, PinOff, Trash2 } from '@/ui/icons';

import { renamedTitle, rowActions, type ChatRow, type RowAction } from './chatList';

export type RowSheet = { row: ChatRow; mode: 'menu' | 'rename' | 'delete' };

export interface ChatRowSheetsProps {
    target: RowSheet | null;
    onTarget: (target: RowSheet | null) => void;
    onRename: (id: string, title: string) => void;
    onSetPinned: (id: string, pinned: boolean) => void;
    onDelete: (id: string) => void;
}

const ICONS = { rename: Pencil, pin: Pin, unpin: PinOff, delete: Trash2 } as const;

/** The "⋯" menu and the two steps it opens: the rename field and the delete confirm. */
export function ChatRowSheets({ target, onTarget, onRename, onSetPinned, onDelete }: ChatRowSheetsProps) {
    const t = useT();
    const close = () => onTarget(null);
    const row = target?.row;
    if (!row) return null;
    const label: Record<RowAction, string> = {
        rename: t('row.rename'),
        pin: t('row.pin'),
        unpin: t('row.unpin'),
        delete: t('row.delete'),
    };
    const run: Record<RowAction, () => void> = {
        rename: () => onTarget({ row, mode: 'rename' }),
        pin: () => onSetPinned(row.id, true),
        unpin: () => onSetPinned(row.id, false),
        delete: () => onTarget({ row, mode: 'delete' }),
    };
    const actions: SheetAction[] = rowActions(row).map((a) => ({
        label: label[a],
        icon: ICONS[a],
        destructive: a === 'delete',
        onPress: run[a],
    }));
    const confirmLabel = row.unsent > 0 ? t.plural('row.deleteUnsent', row.unsent) : t('row.deleteConfirm');

    return (
        <>
            <ActionSheet open={target.mode === 'menu'} onClose={close} actions={actions} title={t('row.actions')} />
            <ActionSheet
                open={target.mode === 'delete'}
                onClose={close}
                title={t('row.deleteMemoryNote')}
                actions={[
                    {
                        label: t('row.tapAgain', { label: confirmLabel }),
                        icon: Trash2,
                        destructive: true,
                        onPress: () => onDelete(row.id),
                    },
                ]}
            />
            {target.mode === 'rename' ? (
                <RenameSheet key={row.id} row={row} onClose={close} onSave={(title) => onRename(row.id, title)} />
            ) : null}
        </>
    );
}

function RenameSheet({ row, onClose, onSave }: { row: ChatRow; onClose: () => void; onSave: (title: string) => void }) {
    const t = useT();
    const [draft, setDraft] = useState(row.title);
    const save = () => {
        const title = renamedTitle(draft, row.title);
        onClose();
        if (title) onSave(title);
    };
    return (
        <Sheet open onClose={onClose} title={t('row.rename')}>
            <Stack gap="md">
                <TextField
                    value={draft}
                    onChangeText={(text) => setDraft(text.slice(0, MAX_SESSION_TITLE_CHARS))}
                    accessibilityLabel={t('row.renameAria', { title: row.title })}
                    returnKeyType="done"
                    onSubmitEditing={save}
                    autoFocus
                />
                <Row gap="sm">
                    <Button label={t('row.cancelRename')} variant="secondary" onPress={onClose} />
                    <Button label={t('row.saveName')} onPress={save} />
                </Row>
            </Stack>
        </Sheet>
    );
}
