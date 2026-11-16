// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';

export interface PickedFile {
    uri: string;
    name: string;
    mimeType?: string;
    size?: number;
    modified?: number;
}

/** Photos and videos from the library; an empty list when the person cancels. */
export async function pickPhotos(): Promise<PickedFile[]> {
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images', 'videos'], allowsMultipleSelection: true });
    if (result.canceled) return [];
    return result.assets.map((a) => ({
        uri: a.uri,
        name: a.fileName || a.uri.slice(a.uri.lastIndexOf('/') + 1),
        mimeType: a.mimeType,
        size: a.fileSize,
    }));
}

/** Thrown when the camera is off for GAIA, so the caller can say how to turn it on. */
export class CameraDeniedError extends Error {}

/** A photo from the camera; an empty list when the person cancels. */
export async function pickCamera(): Promise<PickedFile[]> {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) throw new CameraDeniedError('camera permission denied');
    const result = await ImagePicker.launchCameraAsync({ mediaTypes: ['images', 'videos'] });
    if (result.canceled) return [];
    return result.assets.map((a) => ({
        uri: a.uri,
        name: a.fileName || a.uri.slice(a.uri.lastIndexOf('/') + 1),
        mimeType: a.mimeType,
        size: a.fileSize,
    }));
}

/** Files from the device's file picker; an empty list when the person cancels. */
export async function pickDocuments(): Promise<PickedFile[]> {
    const result = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
    if (result.canceled) return [];
    return result.assets.map((a) => ({ uri: a.uri, name: a.name, mimeType: a.mimeType, size: a.size, modified: a.lastModified }));
}
