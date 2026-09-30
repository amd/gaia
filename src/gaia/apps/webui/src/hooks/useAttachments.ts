// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { Attachment } from '../types';

/** Files attached to the message being composed, uploaded as soon as they are added. */
export interface UseAttachments {
    attachments: Attachment[];
    addFiles: (files: File[]) => void;
    /** Takes pasted images; returns true when it consumed the paste. */
    handlePaste: (e: React.ClipboardEvent) => boolean;
    remove: (id: string) => void;
    clear: () => void;
    /** True when at least one attachment has uploaded. */
    hasUploaded: boolean;
    uploading: boolean;
    /** The message text with markdown links to the uploaded files appended. */
    compose: (text: string) => string;
}

function screenshotName(): string {
    return `screenshot-${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')}.png`;
}

export function useAttachments(): UseAttachments {
    const [attachments, setAttachments] = useState<Attachment[]>([]);
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);

    const upload = useCallback(async (attachment: Attachment) => {
        try {
            const result = await api.uploadFile(attachment.file);
            if (!mounted.current) return;
            setAttachments((prev) => prev.map((a) =>
                a.id === attachment.id ? { ...a, uploading: false, uploaded: true, serverUrl: result.url } : a,
            ));
        } catch (err) {
            log.chat.error(`Upload failed: ${attachment.name}`, err);
            if (!mounted.current) return;
            const message = err instanceof Error ? err.message : 'Upload failed';
            setAttachments((prev) => prev.map((a) =>
                a.id === attachment.id ? { ...a, uploading: false, error: message } : a,
            ));
        }
    }, []);

    const addFiles = useCallback((files: File[]) => {
        for (const file of files) {
            const isImage = file.type.startsWith('image/');
            const attachment: Attachment = {
                id: `attach-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                file,
                name: file.name || (isImage ? screenshotName() : 'file'),
                url: isImage ? URL.createObjectURL(file) : '',
                uploading: true,
                uploaded: false,
                isImage,
            };
            setAttachments((prev) => [...prev, attachment]);
            void upload(attachment);
        }
    }, [upload]);

    const handlePaste = useCallback((e: React.ClipboardEvent): boolean => {
        const images = Array.from(e.clipboardData?.items ?? [])
            .filter((item) => item.type.startsWith('image/'))
            .map((item) => item.getAsFile())
            .filter((f): f is File => f !== null);
        if (images.length === 0) return false;
        e.preventDefault();
        addFiles(images);
        return true;
    }, [addFiles]);

    const remove = useCallback((id: string) => {
        setAttachments((prev) => {
            const gone = prev.find((a) => a.id === id);
            if (gone?.url) URL.revokeObjectURL(gone.url);
            return prev.filter((a) => a.id !== id);
        });
    }, []);

    const clear = useCallback(() => {
        setAttachments((prev) => {
            prev.forEach((a) => { if (a.url) URL.revokeObjectURL(a.url); });
            return [];
        });
    }, []);

    const compose = useCallback((text: string) => {
        const links = attachments
            .filter((a) => a.uploaded && a.serverUrl)
            .map((a) => (a.isImage ? `![${a.name}](${a.serverUrl})` : `[${a.name}](${a.serverUrl})`))
            .join('\n');
        if (!links) return text;
        return text ? `${text}\n\n${links}` : links;
    }, [attachments]);

    return {
        attachments,
        addFiles,
        handlePaste,
        remove,
        clear,
        hasUploaded: attachments.some((a) => a.uploaded),
        uploading: attachments.some((a) => a.uploading),
        compose,
    };
}
