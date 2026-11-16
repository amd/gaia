// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package hybrid

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// focusTemplate moves to the template with id.
func focusTemplate(t *testing.T, m Model, id string) Model {
	t.Helper()
	for i := 0; m.field() >= 0 || m.templates[m.focus].ID != id; i++ {
		if i > 100 {
			t.Fatalf("no template %s", id)
		}
		m = key(t, m, "down")
	}
	return m
}

func TestTemplatesWithNoServerAreJudgedHere(t *testing.T) {
	m, _ := open(t, lemonade.DefaultHybridSettings(), nil, nil)
	if len(m.templates) != 7 || m.fromServer {
		t.Fatalf("%d templates, from server %v", len(m.templates), m.fromServer)
	}
	v := view(m)
	for _, want := range []string{
		"judged on this PC; the Agent UI server isn't running",
		"DeepSeek + Opus (gateway) · measured",
		"DeepSeek + Sonnet (gateway) · not yet measured",
		"Can't use yet: Download Qwen3.6-35B-A3B-GGUF first",
		"Can't use yet: set ANTHROPIC_API_KEY first",
	} {
		if !strings.Contains(v, want) {
			t.Errorf("view lacks %q:\n%s", want, v)
		}
	}
}

func TestApplyingATemplateWritesItsModelsAndPolicy(t *testing.T) {
	s := lemonade.DefaultHybridSettings()
	s.Timeout = 30
	m, st := open(t, s, nil, nil)
	m = focusTemplate(t, m, "gateway-balanced")
	m = key(t, m, "enter")
	if len(st.saved) != 1 {
		t.Fatalf("not applied:\n%s", view(m))
	}
	got := st.saved[0]
	if got.Cheap != "amd.DeepSeek-V4.1-Flash" || got.Strong != "amd.Claude-Opus-5-5" ||
		got.Classifier != got.Cheap || got.Policy != lemonade.HybridPolicyBalanced || got.Timeout != 30 || got.Rules != nil {
		t.Fatalf("applied %+v", got)
	}
	if m.edit.Policy != lemonade.HybridPolicyBalanced || !strings.Contains(view(m), "applied") {
		t.Fatalf("screen did not reload the applied settings:\n%s", view(m))
	}
}

func TestLocalPlusCloudUsesTheDefaultLocalModel(t *testing.T) {
	m, st := open(t, lemonade.DefaultHybridSettings(), nil, nil)
	m = focusTemplate(t, m, "local-plus-cloud")
	m = key(t, m, "enter")
	if len(st.saved) != 1 || st.saved[0].Cheap != "Gemma-4-E4B-it-GGUF" || st.saved[0].Classifier != "Gemma-4-E4B-it-GGUF" {
		t.Fatalf("saved %+v", st.saved)
	}
	if !strings.Contains(view(m), "small local model") {
		t.Fatalf("local classifier note missing:\n%s", view(m))
	}
}

func TestAnUnavailableTemplateIsNotApplied(t *testing.T) {
	m, st := open(t, lemonade.DefaultHybridSettings(), nil, nil)
	m = focusTemplate(t, m, "claude-direct")
	m = key(t, m, "enter")
	if len(st.saved) != 0 || !strings.Contains(view(m), "Claude Haiku + Opus can't be used yet: set ANTHROPIC_API_KEY first") {
		t.Fatalf("saved %+v\n%s", st.saved, view(m))
	}
}

func TestLocalOnlyLeavesOnlyAllLocal(t *testing.T) {
	c := testCatalog()
	c.LocalOnly = true
	c.Models[lemonade.HybridFromLocal] = append(c.Models[lemonade.HybridFromLocal], "Qwen3.6-35B-A3B-GGUF")
	m, st := openWith(t, lemonade.DefaultHybridSettings(), nil, nil, c)
	for _, tpl := range m.templates {
		if tpl.Available != (tpl.ID == "all-local") {
			t.Errorf("%s available=%v under Local only (%s)", tpl.ID, tpl.Available, tpl.Reason)
		}
	}
	m = focusTemplate(t, m, "all-local")
	m = key(t, m, "enter")
	if len(st.saved) != 1 || st.saved[0].Strong != "Qwen3.6-35B-A3B-GGUF" {
		t.Fatalf("saved %+v\n%s", st.saved, view(m))
	}
}

func TestTemplatesComeFromTheServerWhenItAnswers(t *testing.T) {
	var applied string
	mux := http.NewServeMux()
	mux.HandleFunc("/api/hybrid/templates", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]lemonade.HybridTemplate{
			{ID: "gateway-deepseek-opus", Name: "Server default", Available: true, Measured: true},
			{ID: "fireworks", Name: "Server Fireworks", Available: false, Reason: "Add a Fireworks key"},
		})
	})
	mux.HandleFunc("/api/hybrid/templates/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.Header.Get("X-Gaia-UI") != "1" {
			http.Error(w, "bad", http.StatusBadRequest)
			return
		}
		applied = r.URL.Path
		_, _ = io.WriteString(w, `{"policy":"quality"}`)
	})
	m, st := open(t, lemonade.DefaultHybridSettings(), nil, fastAPI404(mux))
	if !m.fromServer || len(m.templates) != 2 {
		t.Fatalf("templates %+v", m.templates)
	}
	v := view(m)
	if !strings.Contains(v, "Server default · measured") || !strings.Contains(v, "Can't use yet: Add a Fireworks key") {
		t.Fatalf("view:\n%s", v)
	}
	m = key(t, m, "enter")
	if applied != "/api/hybrid/templates/gateway-deepseek-opus/apply" || len(st.saved) != 0 {
		t.Fatalf("applied %q, saved locally %d", applied, len(st.saved))
	}
	if !strings.Contains(view(m), "Server default applied") {
		t.Fatalf("view:\n%s", view(m))
	}
}

func TestServerRefusalOfATemplateIsShown(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/hybrid/templates", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]lemonade.HybridTemplate{{ID: "all-local", Name: "All local", Available: true}})
	})
	mux.HandleFunc("/api/hybrid/templates/", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusConflict)
		_, _ = io.WriteString(w, `{"detail":"Download Qwen3.6-35B-A3B-GGUF"}`)
	})
	m, _ := open(t, lemonade.DefaultHybridSettings(), nil, fastAPI404(mux))
	m = key(t, m, "enter")
	if v := view(m); !strings.Contains(v, "Not applied") || !strings.Contains(v, "Download Qwen3.6-35B-A3B-GGUF") {
		t.Fatalf("view:\n%s", v)
	}
}

func TestAnOlderServerFallsBackToTheTemplatesJudgedHere(t *testing.T) {
	m, _ := open(t, lemonade.DefaultHybridSettings(), nil, fastAPI404(http.NewServeMux()))
	if m.fromServer || len(m.templates) != 7 || m.templatesErr != nil {
		t.Fatalf("from server %v, %d templates, err %v", m.fromServer, len(m.templates), m.templatesErr)
	}
}
