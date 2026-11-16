// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"os"
	"strings"
	"testing"
)

func catalogWith(models map[string][]string) HybridCatalog {
	return HybridCatalog{Models: models, Errs: map[string]error{}, DefaultLocal: HybridSmallLocalModel}
}

func templateByID(t *testing.T, list []HybridTemplate, id string) HybridTemplate {
	t.Helper()
	for _, tpl := range list {
		if tpl.ID == id {
			return tpl
		}
	}
	t.Fatalf("no template %s", id)
	return HybridTemplate{}
}

func TestBuiltinTemplatesAreTheSevenWithStableIDs(t *testing.T) {
	list := BuiltinHybridTemplates(catalogWith(nil))
	var ids []string
	measured := 0
	for _, tpl := range list {
		ids = append(ids, tpl.ID)
		if tpl.Measured {
			measured++
			if tpl.ID != HybridMeasuredTemplate {
				t.Errorf("%s claims to be measured", tpl.ID)
			}
		}
		if tpl.Classifier != tpl.Cheap {
			t.Errorf("%s: the classifier is the cheap model", tpl.ID)
		}
	}
	want := "gateway-deepseek-opus,gateway-deepseek-sonnet,gateway-balanced,local-plus-cloud,all-local,fireworks,claude-direct"
	if strings.Join(ids, ",") != want || measured != 1 {
		t.Fatalf("ids %v, measured %d", ids, measured)
	}
}

func TestTemplateAvailabilityFollowsWhatIsSetUp(t *testing.T) {
	none := BuiltinHybridTemplates(catalogWith(nil))
	for id, why := range map[string]string{
		"gateway-deepseek-opus": "needs the AMD LLM Gateway: Connect the AMD LLM Gateway with /gateway",
		"all-local":             "Download " + HybridSmallLocalModel + " with /models",
		"fireworks":             "needs Fireworks AI: Add a Fireworks key with /provider. The strong model needs the AMD LLM Gateway or an Anthropic key",
		"claude-direct":         "needs Anthropic: Add an Anthropic API key",
	} {
		tpl := templateByID(t, none, id)
		if tpl.Available || !strings.Contains(tpl.Reason, why) {
			t.Errorf("%s: available=%v reason=%q, want %q", id, tpl.Available, tpl.Reason, why)
		}
	}

	c := catalogWith(map[string][]string{
		HybridFromLocal:     {HybridSmallLocalModel, HybridLargeLocalModel},
		HybridFromGateway:   {DefaultHybridCheapModel, DefaultHybridStrongModel, HybridSonnetModel},
		HybridFromFireworks: {"fireworks.accounts/fireworks/models/deepseek-v4p1-flash"},
	})
	c.ClaudeKey = true
	for _, tpl := range BuiltinHybridTemplates(c) {
		if !tpl.Available {
			t.Errorf("%s unavailable with everything set up: %s", tpl.ID, tpl.Reason)
		}
	}
	fw := templateByID(t, BuiltinHybridTemplates(c), "fireworks")
	if fw.Cheap != "fireworks.accounts/fireworks/models/deepseek-v4p1-flash" || fw.Strong != DefaultHybridStrongModel {
		t.Fatalf("fireworks template %+v: members must be in the provider's own spelling", fw)
	}
}

func TestFireworksTemplateFallsToDirectClaudeWithoutTheGateway(t *testing.T) {
	c := catalogWith(map[string][]string{HybridFromFireworks: {HybridFireworksCheap}})
	c.ClaudeKey = true
	fw := templateByID(t, BuiltinHybridTemplates(c), "fireworks")
	if !fw.Available || fw.Strong != HybridClaudeDirectStrong {
		t.Fatalf("%+v", fw)
	}
}

func TestLocalPlusCloudFollowsTheDefaultLocalModel(t *testing.T) {
	c := catalogWith(map[string][]string{
		HybridFromLocal:   {"Qwen3.5-9B-GGUF"},
		HybridFromGateway: {DefaultHybridStrongModel},
	})
	c.DefaultLocal = "Qwen3.5-9B-GGUF"
	tpl := templateByID(t, BuiltinHybridTemplates(c), "local-plus-cloud")
	if !tpl.Available || tpl.Cheap != "Qwen3.5-9B-GGUF" || tpl.Classifier != "Qwen3.5-9B-GGUF" {
		t.Fatalf("%+v", tpl)
	}
}

func TestLocalOnlyRefusesEveryCloudTemplate(t *testing.T) {
	c := catalogWith(map[string][]string{
		HybridFromLocal:   {HybridSmallLocalModel, HybridLargeLocalModel},
		HybridFromGateway: {DefaultHybridCheapModel, DefaultHybridStrongModel},
	})
	c.LocalOnly = true
	for _, tpl := range BuiltinHybridTemplates(c) {
		if tpl.Available != (tpl.ID == "all-local") {
			t.Errorf("%s available=%v under Local only", tpl.ID, tpl.Available)
		}
	}
}

func TestDefaultLocalChatModel(t *testing.T) {
	for doc, want := range map[string]string{
		`{}`:                                   defaultChatModel,
		`{"default_model": "hybrid"}`:          defaultChatModel,
		`{"default_model": "amd.X"}`:           defaultChatModel,
		`{"default_model": "claude-opus-5"}`:   defaultChatModel,
		`{"default_model": "Qwen3.5-9B-GGUF"}`: "Qwen3.5-9B-GGUF",
	} {
		path := useConfig(t, nil)
		if err := os.WriteFile(path, []byte(doc), 0o600); err != nil {
			t.Fatal(err)
		}
		if got, err := DefaultLocalChatModel(); err != nil || got != want {
			t.Errorf("%s: %q %v, want %q", doc, got, err, want)
		}
	}
}

func TestTemplateNotesUseTheBackendsWords(t *testing.T) {
	c := catalogWith(nil)
	for _, tpl := range BuiltinHybridTemplates(c) {
		wantMeasured := tpl.ID == HybridMeasuredTemplate
		if tpl.Measured != wantMeasured {
			t.Errorf("%s measured=%v", tpl.ID, tpl.Measured)
		}
		if tpl.Measured != (tpl.MeasuredNote == "") || (!tpl.Measured && tpl.MeasuredNote != HybridMeasuredNote) {
			t.Errorf("%s measured note %q", tpl.ID, tpl.MeasuredNote)
		}
		local := HybridProviderOf(tpl.Classifier) == HybridFromLocal
		if local != (tpl.ClassifierNote == HybridClassifierNote) {
			t.Errorf("%s classifier %s note %q", tpl.ID, tpl.Classifier, tpl.ClassifierNote)
		}
	}
}

func TestCheckedNamesTheProvidersAndHowToAddMore(t *testing.T) {
	c := catalogWith(map[string][]string{HybridFromGateway: {"amd.x"}})
	want := "Checked the AMD LLM Gateway. To use more models: Download a model with /models; " +
		"Add a Fireworks key with /provider; Add an Anthropic API key (ANTHROPIC_API_KEY in GAIA's environment)."
	if got := c.Checked(); got != want {
		t.Fatalf("%q", got)
	}
}
