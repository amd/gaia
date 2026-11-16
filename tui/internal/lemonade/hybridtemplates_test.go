// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"os"
	"path/filepath"
	"regexp"
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
		"gateway-deepseek-opus": "Connect the AMD LLM Gateway first",
		"all-local":             "Download " + HybridSmallLocalModel + " first",
		"fireworks":             "Add a Fireworks key first",
		"claude-direct":         "set ANTHROPIC_API_KEY first",
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

// The templates are defined once, in Python; this copy is what the TUI judges
// when the Agent UI server isn't running, so it must name the same ids, models
// and policies.
func TestHybridTemplatesMatchPython(t *testing.T) {
	path := filepath.Join("..", "..", "..", "src", "gaia", "llm", "hybrid_templates.py")
	raw, err := os.ReadFile(path) // #nosec G304 -- fixed relative path to a checked-in source file
	if os.IsNotExist(err) {
		t.Skipf("%s is not in this checkout yet; it lands with the hybrid settings backend", path)
	}
	if err != nil {
		t.Fatal(err)
	}
	src := string(raw)
	c := catalogWith(map[string][]string{
		HybridFromLocal:     {HybridSmallLocalModel, HybridLargeLocalModel},
		HybridFromGateway:   {DefaultHybridCheapModel, DefaultHybridStrongModel, HybridSonnetModel},
		HybridFromFireworks: {HybridFireworksCheap},
	})
	for _, tpl := range BuiltinHybridTemplates(c) {
		if !strings.Contains(src, `"`+tpl.ID+`"`) {
			t.Errorf("Python has no template %q", tpl.ID)
		}
	}
	for _, id := range []string{
		DefaultHybridCheapModel, DefaultHybridStrongModel, HybridSonnetModel, HybridFireworksCheap,
		HybridClaudeDirectCheap, HybridClaudeDirectStrong, HybridSmallLocalModel, HybridLargeLocalModel,
	} {
		if !strings.Contains(src, id) && !pythonNamesConstantFor(src, id) {
			t.Errorf("Python's templates never name %s", id)
		}
	}
	ids := regexp.MustCompile(`id\s*=\s*"([a-z0-9-]+)"|"id"\s*:\s*"([a-z0-9-]+)"`).FindAllStringSubmatch(src, -1)
	if len(ids) > 0 && len(ids) != 7 {
		t.Errorf("Python defines %d templates, the TUI 7", len(ids))
	}
}

// pythonNamesConstantFor allows Python to name a default model by the
// constant that holds it (DEFAULT_CHEAP_MODEL and the like).
func pythonNamesConstantFor(src, id string) bool {
	constants := map[string][]string{
		DefaultHybridCheapModel:  {"DEFAULT_CHEAP_MODEL"},
		DefaultHybridStrongModel: {"DEFAULT_STRONG_MODEL"},
		HybridSmallLocalModel:    {"DEFAULT_MODEL_NAME"},
		HybridLargeLocalModel:    {"LARGE_DEFAULT_MODEL_NAME"},
	}
	for _, name := range constants[id] {
		if strings.Contains(src, name) {
			return true
		}
	}
	return false
}
