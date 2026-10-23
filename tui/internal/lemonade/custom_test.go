// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The shape gaia.llm.custom_models.add_custom_model writes.
const addedConfig = `{
  "default_device": "gpu",
  "custom_models": [
    {
      "id": "user.Qwen3-0.6B-Q4_K_M-GGUF",
      "display_name": "Qwen3-0.6B Q4_K_M",
      "checkpoint": "unsloth/Qwen3-0.6B-GGUF:Qwen3-0.6B-Q4_K_M.gguf",
      "recipe": "llamacpp",
      "mmproj": null,
      "vision": false,
      "reasoning": true,
      "tool_calling": true,
      "size_gb": 0.4,
      "architecture": "qwen3",
      "context_length": 40960,
      "source_url": "https://huggingface.co/unsloth/Qwen3-0.6B-GGUF",
      "added_at": 1792690060
    }
  ]
}`

func writeConfig(t *testing.T, body string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_CONFIG_FILE", path)
}

func TestReadCustomModelsMapsEachEntryToARegistration(t *testing.T) {
	writeConfig(t, addedConfig)
	got, err := ReadCustomModels()
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d models, want 1", len(got))
	}
	r := got[0]
	if !r.Custom || r.Provider != "local" || r.RegisterAs != "user.Qwen3-0.6B-Q4_K_M-GGUF" {
		t.Errorf("not a local custom registration: %+v", r)
	}
	if r.Checkpoint != "unsloth/Qwen3-0.6B-GGUF:Qwen3-0.6B-Q4_K_M.gguf" || r.Recipe != "llamacpp" || !r.Reasoning {
		t.Errorf("registration fields lost: %+v", r)
	}
	if r.Label != "Qwen3-0.6B Q4_K_M" || r.SizeGB != 0.4 || r.MinCtxSize != 32768 {
		t.Errorf("label, size or window wrong: %+v", r)
	}
	if r.SourceURL != "https://huggingface.co/unsloth/Qwen3-0.6B-GGUF" {
		t.Errorf("source link lost: %q", r.SourceURL)
	}
}

func TestReadCustomModelsWithoutFileOrKeyIsEmpty(t *testing.T) {
	t.Setenv("GAIA_CONFIG_FILE", filepath.Join(t.TempDir(), "missing.json"))
	if got, err := ReadCustomModels(); err != nil || len(got) != 0 {
		t.Fatalf("missing file: %v, %v", got, err)
	}
	writeConfig(t, `{"default_device": "gpu"}`)
	if got, err := ReadCustomModels(); err != nil || len(got) != 0 {
		t.Fatalf("no key: %v, %v", got, err)
	}
}

func TestReadCustomModelsRefusesAMalformedEntry(t *testing.T) {
	writeConfig(t, `{"custom_models": [{"id": "no-namespace", "checkpoint": "a/b:c", "recipe": "llamacpp"}]}`)
	if _, err := ReadCustomModels(); err == nil || !strings.Contains(err.Error(), "gaia model remove") {
		t.Fatalf("want an actionable error, got %v", err)
	}
	writeConfig(t, `{nope`)
	if _, err := ReadCustomModels(); err == nil {
		t.Fatal("invalid JSON must be an error")
	}
}

func addedModels(t *testing.T) []Recommended {
	t.Helper()
	writeConfig(t, addedConfig)
	added, err := ReadCustomModels()
	if err != nil {
		t.Fatal(err)
	}
	return added
}

func TestAnAddedModelIsListedBeforeItsFirstPull(t *testing.T) {
	added := addedModels(t)
	capacity := Capacity{MemoryGB: 32, MemorySource: "System RAM", DiskFreeGB: 500}
	entries := BuildEntries("local", []Model{{ID: "Gemma-4-E4B-it-GGUF", Downloaded: true, Labels: []string{"chat"}}}, capacity, nil, added...)
	var row *Entry
	for i := range entries {
		if entries[i].Recommended != nil && entries[i].Recommended.Custom {
			row = &entries[i]
		}
	}
	if row == nil {
		t.Fatalf("added model missing from %+v", entries)
	}
	if row.Listed || row.Model.ID != "user.Qwen3-0.6B-Q4_K_M-GGUF" || !row.Fits || !row.NeedsDownload() {
		t.Errorf("want an unlisted row that fits and downloads first, got %+v", row)
	}
	body := PullRequest(*row)
	if body["model_name"] != "user.Qwen3-0.6B-Q4_K_M-GGUF" || body["checkpoint"] == nil || body["recipe"] != "llamacpp" {
		t.Errorf("first pull must register under the user. name: %v", body)
	}
}

func TestAnAddedModelMatchesTheBareNameLemonadeListsAfterThePull(t *testing.T) {
	added := addedModels(t)
	listed := Model{ID: "Qwen3-0.6B-Q4_K_M-GGUF", Downloaded: true, Labels: []string{"chat", "custom"}}
	entries := BuildEntries("local", []Model{listed}, Capacity{MemoryGB: 32}, nil, added...)
	count := 0
	for _, e := range entries {
		if strings.Contains(e.Model.ID, "Qwen3-0.6B") {
			count++
			if e.Recommended == nil || !e.Recommended.Custom || !e.Model.Downloaded || e.NeedsDownload() {
				t.Errorf("the listed model must be the added one, downloaded: %+v", e)
			}
		}
	}
	if count != 1 {
		t.Fatalf("want one row for the added model, got %d", count)
	}
}

func TestAddedModelsOnlyJoinTheLocalList(t *testing.T) {
	added := addedModels(t)
	for _, e := range BuildEntries("fireworks", nil, Capacity{}, nil, added...) {
		if e.Recommended != nil && e.Recommended.Custom {
			t.Fatalf("an added local model leaked into the cloud list: %+v", e)
		}
	}
}
