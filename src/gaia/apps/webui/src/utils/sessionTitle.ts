// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Mirrors PLACEHOLDER_TITLES in src/gaia/ui/database.py: titles the server
// still treats as unnamed, so it names the chat after its first question.
const PLACEHOLDER_TITLES = new Set(['', 'new chat', 'new task', 'untitled', 'untitled session', 'chat']);

export function isPlaceholderTitle(title: string | null | undefined): boolean {
    return PLACEHOLDER_TITLES.has((title ?? '').trim().toLowerCase());
}
