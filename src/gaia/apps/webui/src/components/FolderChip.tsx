// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Folder } from 'lucide-react';
import { useSessionEnvironment } from '../hooks/useSessionEnvironment';
import './FolderChip.css';

interface FolderChipProps {
    path: string;
    name: string;
    /** GAIA is in its own documents folder, not one the user started it from. */
    gaiaFolder?: boolean;
    /** Opens where the folder can be changed; without it the chip only names the folder. */
    onClick?: () => void;
}

/** The folder GAIA works in, shown beside the model in the composer. */
export function FolderChip({ path, name, gaiaFolder = false, onClick }: FolderChipProps) {
    const title = gaiaFolder
        ? `GAIA works in its own folder, ${path}. Attach a file or start GAIA from a project folder to work there.`
        : `GAIA works in ${path}`;
    const body = (
        <>
            <Folder size={13} aria-hidden="true" />
            <span className="composer-chip-label">{name}</span>
        </>
    );
    return onClick
        ? <button type="button" className="composer-chip folder-chip" title={title} aria-label={`Working folder ${name}, show the chat's folders`} onClick={onClick}>{body}</button>
        : <span className="composer-chip folder-chip" title={title}>{body}</span>;
}

/** The folder a chat started in, from its environment; opens the panel where it can be changed. */
export function SessionFolderChip({ sessionId, onOpen }: { sessionId: string; onOpen: () => void }) {
    const { env } = useSessionEnvironment(sessionId);
    const folder = env?.folders.find((f) => f.source === 'working_folder');
    if (!folder) return null;
    const name = folder.path.split(/[\\/]/).filter(Boolean).pop() ?? folder.path;
    return <FolderChip path={folder.path} name={name} onClick={onOpen} />;
}
