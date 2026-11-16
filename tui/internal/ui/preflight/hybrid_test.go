// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/status"
)

func stubHybridModels(t *testing.T, h lemonade.HybridModels) {
	t.Helper()
	orig := readHybridModels
	readHybridModels = func() (lemonade.HybridModels, error) { return h, nil }
	t.Cleanup(func() { readHybridModels = orig })
}

func TestHybridIsNeverPickedAsALocalModel(t *testing.T) {
	r := localRunner{opts: LocalOptions{Model: "hybrid"}}
	if r.pickedLocalModel() != "" || !r.verifySkipsChatModel() || !r.skipChatModel() {
		t.Fatal("hybrid must not be downloaded or loaded as a local chat model")
	}
	if _, cloud := r.ModelSetup(); !cloud {
		t.Error("setup for hybrid must offer only the embedding model")
	}
}

func TestAHybridGateChecksEachConcreteModel(t *testing.T) {
	stubGaiaInit(t, func() (string, error) { return jsonStub(t, 0, embedderLoadsJSON), nil })
	stubReplayKeptKey(t, func(context.Context, string) (bool, error) { return false, nil })
	stubHybridModels(t, lemonade.HybridModels{
		Cheap: "amd.DeepSeek-V4.1-Flash", Strong: "fireworks.big", Classifier: "amd.judge",
	})
	asked := map[string]bool{}
	served := map[string][]lemonade.Model{
		"amd":       {{ID: "amd.DeepSeek-V4.1-Flash", Recipe: "cloud"}, {ID: "amd.judge", Recipe: "cloud"}},
		"fireworks": {{ID: "fireworks.big", Recipe: "cloud"}},
	}
	stubListedCloudModels(t, func(_ context.Context, provider string) ([]lemonade.Model, error) {
		asked[provider] = true
		return served[provider], nil
	})
	r := localRunner{opts: LocalOptions{Model: "hybrid"}}

	row, chat, _ := r.verifyModels(context.Background(), localCfg())
	if row.State != StateOK || chat == "" {
		t.Fatalf("hybrid with every model served was refused: %+v chat=%q", row, chat)
	}
	if asked["hybrid"] || !asked["amd"] || !asked["fireworks"] {
		t.Fatalf("hybrid must be checked through its concrete models' providers: %v", asked)
	}

	served["fireworks"] = nil
	row, chat, _ = r.verifyModels(context.Background(), localCfg())
	if row.State != StateFailed || row.Disposition != status.DispositionHalt || chat != "" {
		t.Fatalf("hybrid passed with its strong model missing: %+v chat=%q", row, chat)
	}
	if !strings.Contains(row.Line, "strong") || !strings.Contains(row.Line, "fireworks.big") {
		t.Errorf("line %q does not name the missing model", row.Line)
	}
	if !strings.Contains(row.Remedy.Action, "gaia config set hybrid_strong_model") ||
		!strings.Contains(row.Remedy.Action, "gaia gateway models") {
		t.Errorf("remedy %q does not say how to pick another", row.Remedy.Action)
	}

	served["fireworks"] = []lemonade.Model{{ID: "fireworks.big", Recipe: "cloud"}}
	served["amd"] = served["amd"][:1]
	if row = modelRow(r); row.State != StateFailed || !strings.Contains(row.Line, "classifier") ||
		!strings.Contains(row.Remedy.Action, "hybrid_classifier_model") {
		t.Fatalf("a missing distinct classifier must stop the gate by name: %+v", row)
	}
}

func TestAHybridGateChecksALocalConcreteModelIsDownloaded(t *testing.T) {
	stubGaiaInit(t, func() (string, error) { return jsonStub(t, 0, embedderLoadsJSON), nil })
	stubHybridModels(t, lemonade.HybridModels{
		Cheap: "Gemma-4-E4B-it-GGUF", Strong: "amd.big", Classifier: "Gemma-4-E4B-it-GGUF",
	})
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		return []lemonade.Model{{ID: "amd.big", Recipe: "cloud"}}, nil
	})
	orig := downloadedLocalModels
	downloadedLocalModels = func(context.Context) ([]lemonade.Model, error) { return nil, nil }
	t.Cleanup(func() { downloadedLocalModels = orig })

	row := modelRow(localRunner{opts: LocalOptions{Model: "hybrid"}})
	if row.State != StateFailed || !strings.Contains(row.Line, "cheap") ||
		!strings.Contains(row.Remedy.Action, "hybrid_cheap_model") {
		t.Fatalf("a missing local cheap model must stop the gate by name: %+v", row)
	}
}

func TestUnreadableHybridSettingsStopTheGate(t *testing.T) {
	orig := readHybridModels
	readHybridModels = func() (lemonade.HybridModels, error) {
		return lemonade.HybridModels{}, errors.New("'hybrid_cheap_model' can't be 'hybrid'")
	}
	t.Cleanup(func() { readHybridModels = orig })
	row, ok := checkCloudModel(context.Background(), "hybrid", Row{Key: KeyModel})
	if ok || row.State != StateFailed || !strings.Contains(row.Detail, "hybrid_cheap_model") {
		t.Fatalf("bad hybrid settings passed: %+v", row)
	}
}
