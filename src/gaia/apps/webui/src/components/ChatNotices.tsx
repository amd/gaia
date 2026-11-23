// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** One quiet line saying older turns left the model's view. */
export function HistoryNotice({ text }: { text: string }) {
    return <div className="history-notice" role="note">{text}</div>;
}

export function LibraryNotices({ notices }: { notices: string[] }) {
    return (
        <div className="library-notices" role="note">
            {notices.map((notice, index) => <div key={`${index}-${notice}`} dir="auto">{notice}</div>)}
        </div>
    );
}
