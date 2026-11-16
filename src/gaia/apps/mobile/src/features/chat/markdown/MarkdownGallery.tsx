// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Divider, Screen, Stack, Text } from '@/ui';

import { ARABIC, CODE_TABLE_CITATIONS, ENGLISH, LIBRARY, MIXED, STREAMING } from './__fixtures__/answers';
import { Markdown } from './Markdown';

const SAMPLES = [
    { title: 'English', text: ENGLISH },
    { title: 'Arabic', text: ARABIC },
    { title: 'Mixed', text: MIXED },
    { title: 'Code, table and citations', text: CODE_TABLE_CITATIONS, cite: true },
    { title: 'Streaming', text: STREAMING, streaming: true },
];

/** Developer gallery of Markdown answers; not part of the shipped screens. */
export function MarkdownGallery() {
    return (
        <Screen scroll>
            <Stack gap="xl" padding="lg">
                {SAMPLES.map((sample) => (
                    <Stack key={sample.title} gap="md" testID={`sample-${sample.title}`}>
                        <Text role="caption" tone="muted">
                            {sample.title}
                        </Text>
                        <Markdown
                            text={sample.text}
                            streaming={sample.streaming}
                            documents={sample.cite ? LIBRARY : undefined}
                            notes={sample.cite ? ['Added to your library as a copy.'] : undefined}
                        />
                        <Divider />
                    </Stack>
                ))}
            </Stack>
        </Screen>
    );
}
