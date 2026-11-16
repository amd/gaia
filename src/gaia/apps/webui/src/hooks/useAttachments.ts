// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Translate } from '../i18n/useT';
import { useT } from '../i18n/useT';
import * as api from '../services/api';
import { log } from '../utils/logger';
import { isMediaFile } from '../utils/mediaFiles';
import type { Attachment } from '../types';

/** Files attached to the message being composed, uploaded as soon as they are added. */
export interface UseAttachments {
    attachments: Attachment[];
    addFiles: (files: File[]) => void;
    /** Takes pasted files of any type; returns true when it consumed the paste. */
    handlePaste: (e: React.ClipboardEvent) => boolean;
    remove: (id: string) => void;
    clear: () => void;
    /** True when at least one attachment has uploaded. */
    hasUploaded: boolean;
    hasImage: boolean;
    uploading: boolean;
    /** The message text with markdown links to the uploaded files appended. */
    compose: (text: string) => string;
}

const MODERN_OFFICE: Record<string, string> = { '.doc': '.docx', '.xls': '.xlsx', '.ppt': '.pptx' };

/** The upload refusal in the chosen language; a refusal this build doesn't know shows the server's own words. */
function uploadErrorText(err: unknown, t: Translate): string {
    if (!(err instanceof api.ApiError)) return err instanceof Error ? err.message : t('composer.uploadFailed');
    const { extension, size_mb: size, max_mb: max } = err.body ?? {};
    switch (err.reason) {
        case 'no_extension': return t('composer.upload.noExtension');
        case 'unsupported_type': {
            const modern = MODERN_OFFICE[String(extension)];
            if (modern) return t('composer.upload.legacyOffice', { ext: String(extension), modern });
            return t('composer.upload.unsupported', { ext: String(extension) });
        }
        case 'empty_file': return t('composer.upload.empty');
        case 'too_large': return t('composer.upload.tooLarge', { size: String(size), max: String(max) });
        default: return err.message;
    }
}

function screenshotName(): string {
    return `screenshot-${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')}.png`;
}

export function useAttachments(): UseAttachments {
    const t = useT();
    const [attachments, setAttachments] = useState<Attachment[]>([]);
    const mounted = useRef(true);
    const current = useRef<Attachment[]>([]);
    current.current = attachments;
    const uploads = useRef(new Map<string, AbortController>());
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);

    const upload = useCallback(async (attachment: Attachment) => {
        const controller = new AbortController();
        uploads.current.set(attachment.id, controller);
        try {
            const result = attachment.isMedia
                ? await api.uploadMedia(attachment.file, (progress) => {
                    if (!mounted.current) return;
                    setAttachments((prev) => prev.map((a) => (a.id === attachment.id ? { ...a, progress } : a)));
                }, controller.signal)
                : await api.uploadFile(attachment.file);
            if (!mounted.current) return;
            setAttachments((prev) => prev.map((a) =>
                a.id === attachment.id ? { ...a, uploading: false, uploaded: true, serverUrl: result.url } : a,
            ));
        } catch (err) {
            if (controller.signal.aborted) return;
            log.chat.error(`Upload failed: ${attachment.name}`, err);
            if (!mounted.current) return;
            const message = uploadErrorText(err, t);
            setAttachments((prev) => prev.map((a) =>
                a.id === attachment.id ? { ...a, uploading: false, error: message } : a,
            ));
        } finally {
            uploads.current.delete(attachment.id);
        }
    }, [t]);

    const addFiles = useCallback((files: File[]) => {
        const taken = [...current.current];
        for (const file of files) {
            // The same file picked twice is one chip; a refused one may be retried.
            if (taken.some((a) => !a.error && a.file.name === file.name && a.file.size === file.size
                && a.file.lastModified === file.lastModified)) continue;
            const isImage = file.type.startsWith('image/');
            const attachment: Attachment = {
                id: `attach-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                file,
                name: file.name || (isImage ? screenshotName() : 'file'),
                url: isImage ? URL.createObjectURL(file) : '',
                uploading: true,
                uploaded: false,
                isImage,
                isMedia: isMediaFile(file.name),
            };
            taken.push(attachment);
            setAttachments((prev) => [...prev, attachment]);
            void upload(attachment);
        }
    }, [upload]);

    const handlePaste = useCallback((e: React.ClipboardEvent): boolean => {
        // Files win over text: apps often put the file's name on the clipboard beside it.
        const files = Array.from(e.clipboardData?.items ?? [])
            .filter((item) => item.kind === 'file')
            .map((item) => item.getAsFile())
            .filter((f): f is File => f !== null);
        if (files.length === 0) return false;
        e.preventDefault();
        addFiles(files);
        return true;
    }, [addFiles]);

    const remove = useCallback((id: string) => {
        uploads.current.get(id)?.abort();
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
            .map((a) => {
                // A bracket in a file name ends the link early, and the agent never sees the file.
                const label = a.name.replace(/[[\]]/g, ' ').trim();
                return a.isImage ? `![${label}](${a.serverUrl})` : `[${label}](${a.serverUrl})`;
            })
            .join('\n');
        if (!links) return text;
        // A recording sent with no words still has an obvious request.
        const ask = text || (attachments.some((a) => a.uploaded && a.isMedia) ? t('composer.mediaPrompt') : '');
        return ask ? `${ask}\n\n${links}` : links;
    }, [attachments, t]);

    return {
        attachments,
        addFiles,
        handlePaste,
        remove,
        clear,
        hasUploaded: attachments.some((a) => a.uploaded),
        hasImage: attachments.some((a) => a.uploaded && a.isImage),
        uploading: attachments.some((a) => a.uploading),
        compose,
    };
}
