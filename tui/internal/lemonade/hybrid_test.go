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

func TestHybridIsAVirtualCloudModel(t *testing.T) {
	for _, id := range []string{"hybrid", "Hybrid", " hybrid "} {
		if !IsHybridID(id) || !IsCloudID(id) {
			t.Errorf("%q: hybrid must read as a cloud model, never a local download", id)
		}
	}
	for _, id := range []string{"hybrid.x", "user.hybrid", "Gemma-4-E4B-it-GGUF", ""} {
		if IsHybridID(id) {
			t.Errorf("%q is not hybrid", id)
		}
	}
	if IsCloudProvider(HybridProvider) {
		t.Error("hybrid is not a provider to connect")
	}
	if Label(HybridProvider) == HybridProvider {
		t.Error("hybrid has no label of its own")
	}
}

func TestHybridModelsUseHybridSettingsDefaults(t *testing.T) {
	useConfig(t, nil)
	h, err := ReadHybridModels()
	if err != nil {
		t.Fatal(err)
	}
	want := HybridModels{DefaultHybridCheapModel, DefaultHybridStrongModel, DefaultHybridCheapModel}
	if h != want {
		t.Fatalf("no config: %+v, want %+v", h, want)
	}
	if roles := h.Roles(); len(roles) != 2 {
		t.Fatalf("a classifier that is the cheap model is checked twice: %+v", roles)
	}

	useConfig(t, map[string]any{"hybrid_cheap_model": "", "hybrid_strong_model": nil})
	if h, err = ReadHybridModels(); err != nil || h != want {
		t.Fatalf("empty keys must be the defaults: %+v %v", h, err)
	}
}

func TestHybridClassifierFollowsTheConfiguredCheapModel(t *testing.T) {
	useConfig(t, map[string]any{"hybrid_cheap_model": " fireworks.small ", "hybrid_strong_model": "amd.big"})
	h, err := ReadHybridModels()
	if err != nil {
		t.Fatal(err)
	}
	if h.Cheap != "fireworks.small" || h.Strong != "amd.big" || h.Classifier != "fireworks.small" {
		t.Fatalf("got %+v", h)
	}

	useConfig(t, map[string]any{"hybrid_classifier_model": "amd.judge"})
	if h, err = ReadHybridModels(); err != nil {
		t.Fatal(err)
	}
	roles := h.Roles()
	if len(roles) != 3 || roles[2].Model != "amd.judge" || roles[2].Key != "hybrid_classifier_model" {
		t.Fatalf("a distinct classifier must be checked too: %+v", roles)
	}
}

func TestBadHybridKeysAreRefusedWithTheFix(t *testing.T) {
	for name, doc := range map[string]map[string]any{
		"routes to itself": {"hybrid_strong_model": "HYBRID"},
		"not a string":     {"hybrid_cheap_model": 7},
	} {
		t.Run(name, func(t *testing.T) {
			useConfig(t, doc)
			_, err := ReadHybridModels()
			if err == nil || !strings.Contains(err.Error(), "gaia config set hybrid_") {
				t.Fatalf("err = %v, want one naming the key to set", err)
			}
		})
	}
	path := useConfig(t, nil)
	if err := os.WriteFile(path, []byte(`{"hybrid_cheap_model":`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadHybridModels(); err == nil {
		t.Fatal("an unreadable config.json must not fall back to the defaults")
	}
}

func TestHybridWithOneModelForBothRolesIsRefused(t *testing.T) {
	for name, doc := range map[string]map[string]any{
		"both set":                    {"hybrid_cheap_model": "amd.same", "hybrid_strong_model": " amd.same "},
		"cheap is the strong default": {"hybrid_cheap_model": DefaultHybridStrongModel},
		"strong is the cheap default": {"hybrid_strong_model": DefaultHybridCheapModel},
	} {
		t.Run(name, func(t *testing.T) {
			useConfig(t, doc)
			h, err := ReadHybridModels()
			if err == nil {
				t.Fatalf("one model for both roles must be refused, got %+v", h)
			}
			for _, want := range []string{"nothing to route between", "instead of 'hybrid'"} {
				if !strings.Contains(err.Error(), want) {
					t.Errorf("err = %q, want it to contain %q", err, want)
				}
			}
		})
	}
	// Ids differing only in case are distinct models, as they are in Python.
	useConfig(t, map[string]any{"hybrid_cheap_model": "amd.Model", "hybrid_strong_model": "amd.model"})
	if _, err := ReadHybridModels(); err != nil {
		t.Fatalf("distinct ids must be accepted: %v", err)
	}
}

// The TUI checks the models Python routes to, so the defaults must agree.
func TestHybridDefaultsMatchPython(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "src", "gaia", "llm", "hybrid.py"))
	if err != nil {
		t.Skipf("Python source not alongside this checkout: %v", err)
	}
	for name, want := range map[string]string{
		"HYBRID_MODEL_ID":      HybridModelID,
		"DEFAULT_CHEAP_MODEL":  DefaultHybridCheapModel,
		"DEFAULT_STRONG_MODEL": DefaultHybridStrongModel,
	} {
		m := regexp.MustCompile(`(?m)^` + name + ` = "([^"]+)"`).FindSubmatch(raw)
		if m == nil {
			t.Fatalf("could not find %s in src/gaia/llm/hybrid.py", name)
		}
		if got := string(m[1]); got != want {
			t.Errorf("%s is %q in Python, %q in the TUI", name, got, want)
		}
	}
}
