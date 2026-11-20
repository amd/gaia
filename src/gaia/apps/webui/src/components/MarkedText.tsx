// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { bracketParts } from '../i18n/registry';

/** Marks a param (a number with its unit, a path, a tool name) for `MarkedText` to keep left to right. */
export const markLtr = (text: string): string => `${text}`;
const MARKED = /([^]*)/;

/** Text with each marked piece in its own left-to-right `<bdi>`. */
function Marked({ text }: { text: string }) {
    return <>{text.split(MARKED).map((piece, i) => (i % 2 ? <bdi dir="ltr" key={i}>{piece}</bdi> : piece))}</>;
}

/** A translated line with marked params, its bracketed English set apart as the quieter gloss. */
export function MarkedText({ text }: { text: string }) {
    const [own, english] = bracketParts(text);
    if (english === undefined) return <Marked text={own} />;
    return <><Marked text={own} />{' '}<small className="i18n-gloss" lang="en"><Marked text={english} /></small></>;
}
