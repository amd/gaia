// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** File changes a turn made, as the agent's snapshot store reports them (#5225). */

export type FileChangeKind = 'edited' | 'created' | 'deleted';

export interface TurnFileChange {
    path: string;
    change: FileChangeKind;
    tool: string | null;
}

/** Work that may have changed files without GAIA keeping a copy. */
export interface UntrackedWork {
    kind: string;
    detail: string;
    paths: string[];
}

export interface TurnChanges {
    turn_id: string;
    session_id: string;
    message_id: number | null;
    started_at: string;
    finished_at: string | null;
    restored_at: string | null;
    files: TurnFileChange[];
    untracked: UntrackedWork[];
    incomplete: boolean;
}

export interface FileDiff {
    path: string;
    change: FileChangeKind;
    unified: string;
    binary: boolean;
    /** Too large to read for a diff. */
    too_large: boolean;
    truncated: boolean;
    before_size: number;
    after_size: number;
}

export interface UndoResult {
    turn_id: string;
    restored: string[];
    removed: string[];
    kept_folders: string[];
    untracked: UntrackedWork[];
    incomplete: boolean;
}

export interface UndoSettings {
    retention_days: number;
    max_mb: number;
    used_bytes: number;
    turns: number;
    path: string;
}
