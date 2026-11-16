// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { File, Paths } from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { useState } from 'react';
import { Image, Modal, ScrollView, View, useWindowDimensions } from 'react-native';

import { SHARE_IMAGE_PREFIX } from '@/features/files/shareTemp';
import { useT } from '@/i18n/useT';
import { IconButton, Notice, Row, useTheme } from '@/ui';
import { Share2, X } from '@/ui/icons';

import type { LoadedImage } from './generatedImage';

const EXTENSION: Record<string, string> = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp' };

async function shareImage(image: LoadedImage): Promise<void> {
    if (!(await Sharing.isAvailableAsync())) throw new Error('This phone has no share sheet for files');
    const file = new File(Paths.cache, `${SHARE_IMAGE_PREFIX}${Date.now()}.${EXTENSION[image.mime] ?? 'png'}`);
    file.create();
    try {
        file.write(Uint8Array.from(atob(image.base64), (c) => c.charCodeAt(0)));
        await Sharing.shareAsync(file.uri, { mimeType: image.mime });
    } finally {
        if (file.exists) file.delete();
    }
}

export interface ImagePreviewProps {
    image: LoadedImage;
    alt?: string;
    onClose: () => void;
}

/** The image full screen on a dark ground: pinch to zoom (iOS), and share through the phone's own share sheet. */
export function ImagePreview({ image, alt, onClose }: ImagePreviewProps) {
    const t = useT();
    const { colors } = useTheme();
    const { width, height } = useWindowDimensions();
    const [error, setError] = useState<string | null>(null);
    const share = () => {
        setError(null);
        shareImage(image).catch((e: unknown) => {
            console.error('Sharing the image failed', e);
            setError(e instanceof Error ? e.message : String(e));
        });
    };
    return (
        <Modal visible transparent animationType="fade" onRequestClose={onClose} supportedOrientations={['portrait', 'landscape']}>
            <View style={{ flex: 1, backgroundColor: colors.bgCode }} testID="image-preview">
                <ScrollView
                    maximumZoomScale={5}
                    minimumZoomScale={1}
                    centerContent
                    showsHorizontalScrollIndicator={false}
                    showsVerticalScrollIndicator={false}
                    contentContainerStyle={{ flexGrow: 1, justifyContent: 'center' }}
                >
                    <Image source={{ uri: image.uri }} accessibilityLabel={alt || undefined} resizeMode="contain" style={{ width, height: height * 0.8 }} />
                </ScrollView>
                <View style={{ position: 'absolute', top: 0, start: 0, end: 0, padding: 12, paddingTop: 48 }}>
                    <Row justify="between" align="center">
                        <IconButton icon={X} label={t('fileBrowser.closePreview')} variant="filled" onPress={onClose} testID="image-preview-close" />
                        <IconButton icon={Share2} label={t('cards.share')} variant="filled" onPress={share} testID="image-preview-share" />
                    </Row>
                    {error ? <Notice tone="danger" title={t('cards.shareFailed', { error })} /> : null}
                </View>
            </View>
        </Modal>
    );
}
