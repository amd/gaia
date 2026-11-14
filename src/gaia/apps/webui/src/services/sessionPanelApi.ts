// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The session panel: a chat's Environment, Outputs and Changes (#5279). */

import type { AgentCapabilities, SessionChanges, SessionEnvironment, SessionGit, SessionOutputs, UndoAllResult } from '../types/sessionPanel';
import { apiFetch } from './api';

const sessionPath = (sessionId: string) => `/sessions/${encodeURIComponent(sessionId)}`;

export async function getEnvironment(sessionId: string): Promise<SessionEnvironment> {
    return apiFetch('GET', `${sessionPath(sessionId)}/environment`);
}

export async function addEnvironmentFolder(sessionId: string, path: string): Promise<SessionEnvironment> {
    return apiFetch('POST', `${sessionPath(sessionId)}/environment/folders`, { path });
}

/** Why a chat can't be given *path*, or null: the same rule adding it applies. */
export async function checkEnvironmentFolder(path: string): Promise<{ refusal: string | null }> {
    return apiFetch('POST', '/environment/folders/check', { path });
}

export async function removeEnvironmentFolder(sessionId: string, path: string): Promise<SessionEnvironment> {
    return apiFetch('POST', `${sessionPath(sessionId)}/environment/folders/remove`, { path });
}

export async function setInternet(sessionId: string, on: boolean): Promise<SessionEnvironment> {
    return apiFetch('PUT', `${sessionPath(sessionId)}/environment/internet`, { on });
}

export async function getOutputs(sessionId: string): Promise<SessionOutputs> {
    return apiFetch('GET', `${sessionPath(sessionId)}/outputs`);
}

/** The tools the chat's agent has; a 409 when it isn't running yet. */
export async function getCapabilities(sessionId: string): Promise<AgentCapabilities> {
    return apiFetch('GET', `${sessionPath(sessionId)}/capabilities`);
}

export async function getSessionChanges(sessionId: string): Promise<SessionChanges> {
    return apiFetch('GET', `${sessionPath(sessionId)}/changes/total`);
}

export async function undoAllChanges(sessionId: string, force = false): Promise<UndoAllResult> {
    return apiFetch('POST', `${sessionPath(sessionId)}/changes/undo`, { force });
}

export async function getSessionGit(sessionId: string): Promise<SessionGit> {
    return apiFetch('GET', `${sessionPath(sessionId)}/changes/git`);
}

export async function commitChanges(sessionId: string, root: string, message: string): Promise<{ root: string; commit: string; files: string[]; ignored: string[] }> {
    return apiFetch('POST', `${sessionPath(sessionId)}/changes/git/commit`, { root, message, confirmed: true });
}

export async function createPullRequest(sessionId: string, root: string, title: string, body: string): Promise<{ root: string; url: string }> {
    return apiFetch('POST', `${sessionPath(sessionId)}/changes/git/pr`, { root, title, body, confirmed: true });
}
