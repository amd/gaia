// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Folder, Plus, X } from 'lucide-react';
import { useFolderPicker } from '../hooks/useFolderPicker';
import { useSessionEnvironment } from '../hooks/useSessionEnvironment';
import { useChatStore } from '../stores/chatStore';
import { useT } from '../i18n/useT';
import type { StartScreen } from '../types';
import './FolderChip.css';

type WorkingFolder = StartScreen['working_folder'];

const baseName = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() ?? path;

/**
 * The new chat's folders: where GAIA works, and an "Add a folder" button that
 * gives this chat one more, when the agent can be held to it. GAIA's own
 * documents folder is not named, since a user never chose it.
 */
export function FolderChip({ folder, disabled }: { folder: WorkingFolder; disabled?: boolean }) {
    const t = useT();
    const picked = useChatStore((s) => s.draftFolder);
    const setPicked = useChatStore((s) => s.setDraftFolder);
    const { open, picker, error } = useFolderPicker(setPicked);

    const where = folder.gaia_folder
        ? t('folderChip.worksInOwn', { path: folder.path })
        : t('folderChip.worksIn', { path: folder.path });
    const working = !folder.gaia_folder && (
        <span className="new-chat-folder" title={where}>
            <Folder size={13} aria-hidden="true" />
            <span className="new-chat-folder-name" dir="auto">{folder.name}</span>
        </span>
    );

    if (!folder.choosable) {
        return working || (
            <span className="new-chat-folder" title={`${where} ${t('folderChip.ownFixedHint')}`}>
                <Folder size={13} aria-hidden="true" />
                <span className="new-chat-folder-name">{t('folderChip.gaiaFolder')}</span>
            </span>
        );
    }

    return (
        <>
            {working}
            {picked ? (
                <span className="new-chat-folder is-on" title={t('folderChip.picked', { path: picked })}>
                    <Folder size={13} aria-hidden="true" />
                    <span className="new-chat-folder-name" dir="auto">{baseName(picked)}</span>
                    <button
                        type="button"
                        className="new-chat-folder-clear"
                        onClick={() => setPicked(null)}
                        disabled={disabled}
                        aria-label={t('folderChip.remove', { name: baseName(picked) })}
                        title={t('folderChip.remove', { name: baseName(picked) })}
                    >
                        <X size={12} aria-hidden="true" />
                    </button>
                </span>
            ) : (
                <button
                    type="button"
                    className="new-chat-folder is-button"
                    onClick={() => open(folder.path)}
                    disabled={disabled}
                    aria-haspopup="dialog"
                    title={`${where} ${t('folderChip.addHint')}`}
                >
                    <Plus size={13} aria-hidden="true" />
                    <span className="new-chat-folder-name">{t('folderChip.add')}</span>
                </button>
            )}
            {error && <span className="new-chat-folder-error" role="alert">{error}</span>}
            {picker}
        </>
    );
}

/** The folder a chat started in, beside the model in its composer; opens the panel where it can be changed. */
export function SessionFolderChip({ sessionId, onOpen }: { sessionId: string; onOpen: () => void }) {
    const { env } = useSessionEnvironment(sessionId);
    const folder = env?.folders.find((f) => f.source === 'working_folder');
    if (!folder) return null;
    const name = baseName(folder.path);
    return (
        <button
            type="button"
            className="composer-chip folder-chip"
            title={`GAIA works in ${folder.path}`}
            aria-label={`Working folder ${name}, show the chat's folders`}
            onClick={onOpen}
        >
            <Folder size={13} aria-hidden="true" />
            <span className="composer-chip-label" dir="auto">{name}</span>
        </button>
    );
}
