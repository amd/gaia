// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// useUsersServer saves "use my Lemonade at base" the way the first-run offer does.
func useUsersServer(t *testing.T, base string) {
	t.Helper()
	isolateHome(t)
	t.Setenv("LEMONADE_BASE_URL", "")
	t.Setenv("GAIA_LEMONADE_EMBEDDED", "")
	path := filepath.Join(t.TempDir(), "config.json")
	raw, _ := json.Marshal(map[string]any{"lemonade_mode": "external", "lemonade_base_url": base})
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_CONFIG_FILE", path)
}

// The user's server already holds the 6 GB chat model and lacks only the
// embedder: the first-run step must offer the embedder, not the chat model.
const chatAlreadyOnServerJSON = `{"ready": false, "stage": "setup", "reasons": ["Model 'user.embeddinggemma-300m-GGUF' is not downloaded"], "models": [` +
	`{"id": "user.embeddinggemma-300m-GGUF", "role": "embedding", "size_gb": 0.3, "loaded": false, "error": null, "downloaded": false},` +
	`{"id": "Gemma-4-E4B-it-GGUF", "role": "chat", "size_gb": 5.97, "loaded": false, "error": null, "downloaded": true}]}`

func TestTheModelStepNeverOffersAModelTheUsersServerHas(t *testing.T) {
	const base = "http://localhost:13305/api/v1"
	useUsersServer(t, base)
	stubGaiaInit(t, func() (string, error) { return jsonStub(t, 1, chatAlreadyOnServerJSON), nil })

	row := modelRow(localRunner{})

	if row.State != StateFailed || !row.FirstRun || row.Fix != FixRunSetup {
		t.Fatalf("want a first-run download step, got %+v", row)
	}
	if row.Step != "download the models (300 MB)" {
		t.Errorf("step = %q; the 5.97 GB chat model is already on the server", row.Step)
	}
	if strings.Contains(row.Line, "Gemma") || !strings.Contains(row.Line, "user.embeddinggemma-300m-GGUF") {
		t.Errorf("line = %q; it must name only the missing model", row.Line)
	}
	if !strings.Contains(row.Detail, base) {
		t.Errorf("detail = %q; it must say the download goes to the user's server", row.Detail)
	}
	if strings.Contains(row.Remedy.Action, "starts the local server") {
		t.Errorf("remedy = %q; GAIA starts nothing on the user's server", row.Remedy.Action)
	}
}

// The sum covers only the missing models on GAIA's own server too.
func TestTheModelStepSumsOnlyMissingModels(t *testing.T) {
	isolateHome(t)
	t.Setenv("GAIA_CONFIG_FILE", filepath.Join(t.TempDir(), "absent.json"))
	t.Setenv("LEMONADE_BASE_URL", "")
	stubGaiaInit(t, func() (string, error) { return jsonStub(t, 1, chatAlreadyOnServerJSON), nil })

	row := modelRow(localRunner{})

	if row.Step != "download the models (300 MB)" {
		t.Errorf("step = %q", row.Step)
	}
	if !strings.Contains(row.Remedy.Action, "starts the local server") {
		t.Errorf("GAIA's own server keeps its remedy: %q", row.Remedy.Action)
	}
}
