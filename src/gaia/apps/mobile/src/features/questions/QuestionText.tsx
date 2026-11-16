// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Markdown } from '@/features/chat/markdown/Markdown';

/** A question's text, in the chat's Markdown renderer. */
export function QuestionText({ text }: { text: string }) {
    return <Markdown text={text} variant="formatting" />;
}
