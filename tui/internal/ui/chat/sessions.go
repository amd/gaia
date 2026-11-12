// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

// OpenSessionsMsg asks the host to show the /sessions list. The host owns
// sessions: reopening one replaces this whole ChatModel.
type OpenSessionsMsg struct {
	// AllKinds also lists eval and scheduled runs (/sessions all).
	AllKinds bool
}
