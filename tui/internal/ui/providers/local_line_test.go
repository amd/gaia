// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

func TestTheLocalRowNeverNamesACloudModel(t *testing.T) {
	info := lemonade.ServerInfo{
		BaseURL: "http://localhost:64595/api/v1", Owner: "gaia", Version: "2026.42.1",
		Chat: &lemonade.LoadedModel{Name: "fireworks.deepseek-v4p1-flash", Backend: "remote"},
	}
	line := localServerLine(info)
	if strings.Contains(line, "deepseek") || strings.Contains(line, "cloud") {
		t.Fatalf("the Local row describes the cloud model: %q", line)
	}
	if !strings.Contains(line, "no chat model loaded") {
		t.Fatalf("line = %q", line)
	}
}

func TestTheLocalRowNamesALocalModelLoadedBesideTheCloudOne(t *testing.T) {
	info := lemonade.ServerInfo{
		BaseURL: "http://localhost:64595/api/v1", Owner: "gaia",
		Chat:      &lemonade.LoadedModel{Name: "fireworks.deepseek-v4p1-flash", Backend: "remote"},
		LocalChat: &lemonade.LoadedModel{Name: "Gemma-4-E4B-it-GGUF", Backend: "metal", CtxSize: 65536},
	}
	line := localServerLine(info)
	if !strings.Contains(line, "Gemma-4-E4B-it-GGUF") || strings.Contains(line, "deepseek") {
		t.Fatalf("line = %q", line)
	}
}
