// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { ChevronDown, Folder, X } from 'lucide-react';
import { FolderPicker } from './session-panel/FolderPicker';
import { useChatStore } from '../stores/chatStore';
import type { StartScreen } from '../types';

type WorkingFolder = StartScreen['working_folder'];

const baseName = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() ?? path;

/** The new chat's folder: picks one more folder the chat may use, when the agent can be held to it. */
export function FolderChip({ folder, disabled }: { folder: WorkingFolder; disabled?: boolean }) {
    const picked = useChatStore((s) => s.draftFolder);
    const setPicked = useChatStore((s) => s.setDraftFolder);
    const [picking, setPicking] = useState(false);

    const where = folder.gaia_folder
        ? `GAIA works in its own folder, ${folder.path}.`
        : `GAIA works in ${folder.path}.`;

    if (!folder.choosable) {
        return (
            <span
                className="new-chat-folder"
                title={folder.gaia_folder
                    ? `${where} Attach a file or start GAIA from a project folder to work there.`
                    : where}
            >
                <Folder size={13} aria-hidden="true" />
                <span className="new-chat-folder-name">{folder.name}</span>
            </span>
        );
    }

    return (
        <>
            <button
                type="button"
                className={`new-chat-folder is-button${picked ? ' is-on' : ''}`}
                onClick={() => setPicking(true)}
                disabled={disabled}
                aria-haspopup="dialog"
                aria-label={picked
                    ? `Folder for this chat: ${folder.name}, plus ${baseName(picked)}`
                    : `Folder for this chat: ${folder.name}`}
                title={picked
                    ? `This chat can read and write ${picked}. Click to pick another folder.`
                    : `${where} Click to give this chat another folder.`}
            >
                <Folder size={13} aria-hidden="true" />
                <span className="new-chat-folder-name">{picked ? `${folder.name} + ${baseName(picked)}` : folder.name}</span>
                <ChevronDown size={12} aria-hidden="true" />
            </button>
            {picked && (
                <button
                    type="button"
                    className="btn-icon-sm new-chat-folder-clear"
                    onClick={() => setPicked(null)}
                    disabled={disabled}
                    aria-label={`Don't give this chat ${baseName(picked)}`}
                    title="Don't add this folder"
                >
                    <X size={12} />
                </button>
            )}
            {picking && (
                <FolderPicker
                    onPick={(path) => { setPicked(path); setPicking(false); }}
                    onClose={() => setPicking(false)}
                />
            )}
        </>
    );
}
