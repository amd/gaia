// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Image } from 'react-native';

import { isImagePayload } from '@webui/utils/renderCards.core';

import { PcOnlyNotice } from '@/features/settings/PcOnlyNotice';
import { useT } from '@/i18n/useT';
import { Notice, Pressable, Skeleton, Stack, Surface, Text } from '@/ui';

import { useCardImage } from './generatedImage';
import { ImagePreview } from './ImagePreview';
import { UnsupportedCard } from './UnsupportedCard';

function CardImage({ src, alt, caption }: { src: string; alt?: string; caption?: string }) {
    const t = useT();
    const state = useCardImage(src);
    const [open, setOpen] = useState(false);
    const [ratio, setRatio] = useState(4 / 3);
    const uri = state.status === 'ready' ? state.image.uri : null;
    useEffect(() => {
        if (!uri) return;
        Image.getSize(
            uri,
            (width, height) => width > 0 && height > 0 && setRatio(width / height),
            (e) => console.error('Reading the image size failed', e),
        );
    }, [uri]);
    if (state.status === 'failed' && state.refused) return <PcOnlyNotice testID="card-image-pc-only" />;
    if (state.status === 'failed') {
        return <Notice tone="danger" title={t('mobileApp.render.imageFailed')} message={state.error} testID="card-image-failed" />;
    }
    return (
        <Surface tone="card" radius="md" border padding="sm" testID="card-image">
            <Stack gap="sm">
                {state.status === 'loading' ? (
                    <Skeleton height={160} />
                ) : (
                    <Pressable onPress={() => setOpen(true)} accessibilityRole="button" accessibilityLabel={alt || t('media.showFull')} testID="card-image-open">
                        <Image
                            source={{ uri: state.image.uri }}
                            accessibilityLabel={alt || undefined}
                            resizeMode="contain"
                            style={{ width: '100%', aspectRatio: ratio }}
                            testID="card-image-img"
                        />
                    </Pressable>
                )}
                {caption ? (
                    <Text role="caption" tone="secondary" dir="auto">
                        {caption}
                    </Text>
                ) : null}
            </Stack>
            {open && state.status === 'ready' ? <ImagePreview image={state.image} alt={alt} onClose={() => setOpen(false)} /> : null}
        </Surface>
    );
}

export function ImageCard({ data }: { data: unknown }) {
    if (!isImagePayload(data)) return <UnsupportedCard variant="invalid" render="image" data={data} />;
    return <CardImage src={data.src} alt={data.alt} caption={data.caption} />;
}
