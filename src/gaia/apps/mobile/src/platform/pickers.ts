// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';

import type { PickedFile } from '@/api/client.types';
import { GaiaError } from '@/api/errors';

export type { PickedFile };

// The picker names camera-roll items by path; a missing name still needs a real extension for GAIA's type check.
function photoName(asset: ImagePicker.ImagePickerAsset): string {
    if (asset.fileName) return asset.fileName;
    const last = asset.uri.split('/').pop() ?? '';
    return last.includes('.') ? last : `photo-${Date.now()}.${asset.mimeType?.split('/')[1] ?? 'jpg'}`;
}

/** Photos the person chose from the library; `[]` when they cancel. Throws a GaiaError when access is denied. */
export async function pickPhotos(): Promise<PickedFile[]> {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) throw new GaiaError('mobileApp.error.photosDenied');
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsMultipleSelection: true });
    if (result.canceled) return [];
    return result.assets.map((asset) => ({
        uri: asset.uri,
        name: photoName(asset),
        mimeType: asset.mimeType ?? null,
        size: asset.fileSize ?? null,
    }));
}

/** Files the person chose from Files, iCloud or Drive; `[]` when they cancel. */
export async function pickDocuments(): Promise<PickedFile[]> {
    const result = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
    if (result.canceled) return [];
    return result.assets.map((asset) => ({
        uri: asset.uri,
        name: asset.name,
        mimeType: asset.mimeType ?? null,
        size: asset.size ?? null,
    }));
}
