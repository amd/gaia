// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Wire shapes for the session panel's Environment, Outputs and Changes (#5279). */

import type { FileChangeKind, UntrackedWork } from './changes';

export type ScopeSource =
    | 'added' | 'gaia_documents' | 'scratch' | 'document' | 'working_folder' | 'approved';

export interface ScopeEntry {
    path: string;
    kind: 'folder' | 'file';
    source: ScopeSource;
    removable: boolean;
}

export interface SessionEnvironment {
    session_id: string;
    folders: ScopeEntry[];
    removed: string[];
    internet: boolean;
    /** False when the chat's agent keeps its own file scope (a relayed sidecar). */
    enforced: boolean;
}

export type OutputKind = 'file' | 'export' | 'image' | 'document' | 'memory' | 'schedule';

export interface OutputItem {
    id: string;
    kind: OutputKind;
    name: string;
    path: string | null;
    detail: string | null;
    message_id: number | null;
    /** UTC, `YYYY-MM-DD HH:MM:SS`. */
    created_at: string | null;
    change: FileChangeKind | null;
    state: 'present' | 'undone' | 'gone';
    openable: boolean;
    revealable: boolean;
}

export interface SessionOutputs {
    session_id: string;
    items: OutputItem[];
}

export interface ChatFileChange {
    path: string;
    change: FileChangeKind;
    unified: string;
    binary: boolean;
    too_large: boolean;
    truncated: boolean;
    /** Null when the file is binary or too large to count. */
    additions: number | null;
    deletions: number | null;
    before_size: number;
    after_size: number;
    message_ids: number[];
    /** Why the file on disk no longer matches what the chat left, or null. */
    changed_since: string | null;
}

export interface SessionChanges {
    files: ChatFileChange[];
    totals: { files: number; additions: number; deletions: number; uncounted: number };
    turns: number;
    /** A turn is still running; its changes are not counted yet. */
    pending: boolean;
    untracked: UntrackedWork[];
    incomplete: boolean;
}

export interface UndoAllResult {
    turn_ids: string[];
    restored: string[];
    removed: string[];
    kept_folders: string[];
    untracked: UntrackedWork[];
    incomplete: boolean;
}

export interface GitRepo {
    root: string;
    branch: string;
    remote: string | null;
    default_branch: string | null;
    gh: boolean;
    files: string[];
    /** The chat's files git still sees as changed. */
    uncommitted: string[];
    /** The chat's files git ignores; never staged. */
    ignored: string[];
    draft: { title: string; body: string };
    pr_blocked: string | null;
}

export interface SessionGit {
    available: boolean;
    repos: GitRepo[];
    reason?: string;
}

export interface TunnelDevice {
    name: string;
    lastSeen: string;
}
