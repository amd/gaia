// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/gaiainit"
	"github.com/amd/gaia/tui/internal/ui/status"
)

func downloaded(v bool) *bool { return &v }

func TestAgentOnlySetupSaysTheAgentNeedsInstallingNotADownload(t *testing.T) {
	size := 6.2
	st := gaiainit.Status{
		Stage:        gaiainit.StageSetup,
		AgentMissing: true,
		Reasons:      []string{"The GAIA agent is not installed -- run: gaia init"},
		Models: []gaiainit.Model{
			{ID: "Gemma-4-E4B-it-GGUF", Role: "chat", SizeGB: &size, Downloaded: downloaded(true)},
			{ID: "user.embeddinggemma-300m-GGUF", Role: "embedding", Downloaded: downloaded(true)},
		},
	}
	row := localRunner{}.agentMissingRow(st)

	if row.State != StateFailed || !row.FirstRun || row.Disposition != status.DispositionHalt || row.Fix != FixRunSetup {
		t.Errorf("row = %+v, want a first-run step setup can fix", row)
	}
	if !strings.Contains(row.Line, "GAIA agent needs installing") || row.Step != "install the GAIA agent" {
		t.Errorf("line %q / step %q do not say the agent needs installing", row.Line, row.Step)
	}
	for _, text := range []string{row.Line, row.Step, row.Remedy.Action} {
		if strings.Contains(text, "not downloaded") || strings.Contains(text, "GB") ||
			strings.Contains(text, "download the models") {
			t.Errorf("%q offers a model download when every model is on the server", text)
		}
	}
	if row.Raw != st.Reasons[0] {
		t.Errorf("raw = %q, want the check's own reason", row.Raw)
	}
}

func TestAMissingModelStillReadsAsADownload(t *testing.T) {
	st := gaiainit.Status{
		Stage:        gaiainit.StageSetup,
		AgentMissing: true,
		Models: []gaiainit.Model{
			{ID: "Gemma-4-E4B-it-GGUF", Role: "chat", Downloaded: downloaded(false)},
		},
	}
	if st.AgentOnly() {
		t.Error("a missing chat model was hidden behind the agent")
	}
}
