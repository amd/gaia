// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Lemonade /system-info bodies, trimmed to the fields the fit check reads.
const (
	strixHalo128 = `{"Physical Memory":"128 GB","devices":{"amd_gpu":[{"available":true,"integrated":true,"vram_gb":96.0,"virtual_mem_gb":15.8}],"metal":{"available":false}},"model_storage":{"free_bytes":900000000000}}`
	strixHalo64  = `{"Physical Memory":"64 GB","devices":{"amd_gpu":[{"available":true,"integrated":true,"vram_gb":48.0,"virtual_mem_gb":7.9}]},"model_storage":{"free_bytes":900000000000}}`
	macM4        = `{"Physical Memory":"16 GB","devices":{"amd_gpu":[],"metal":{"available":true,"vram_gb":11.84}},"model_storage":{"free_bytes":19600000000}}`
	cpuOnly      = `{"Physical Memory":"32 GB","devices":{"amd_gpu":[]}}`
)

func capacityOf(t *testing.T, body string) Capacity {
	t.Helper()
	var info systemInfo
	if err := json.Unmarshal([]byte(body), &info); err != nil {
		t.Fatal(err)
	}
	c, err := capacityFrom(info)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func qwenFlash() Recommended {
	for _, r := range RecommendedFor("local") {
		if r.RegisterAs != "" {
			return r
		}
	}
	panic("no custom local recommendation")
}

func TestStrixHaloPoolIsVRAMPlusSharedMemory(t *testing.T) {
	c := capacityOf(t, strixHalo128)
	if c.MemorySource != "AMD iGPU" || c.MemoryGB < 111 || c.MemoryGB > 112 {
		t.Fatalf("capacity %+v", c)
	}
	if ok, why := c.Fit(qwenFlash().SizeGB); !ok {
		t.Fatalf("Qwen3.8 Flash must fit a 128 GB Strix Halo: %s", why)
	}
}

func TestQwenFlashDoesNotFitSmallerMachines(t *testing.T) {
	for name, body := range map[string]string{"strix halo 64": strixHalo64, "mac m4": macM4, "cpu 32": cpuOnly} {
		ok, why := capacityOf(t, body).Fit(qwenFlash().SizeGB)
		if ok || !strings.Contains(why, "memory") {
			t.Errorf("%s: fits=%v reason=%q", name, ok, why)
		}
	}
}

func TestDiskIsPartOfFit(t *testing.T) {
	c := capacityOf(t, strixHalo128)
	c.DiskFreeGB = 40
	ok, why := c.Fit(qwenFlash().SizeGB)
	if ok || !strings.Contains(why, "disk") {
		t.Fatalf("fits=%v reason=%q", ok, why)
	}
}

func TestNoMemoryReportedIsAnError(t *testing.T) {
	var info systemInfo
	_ = json.Unmarshal([]byte(`{"devices":{}}`), &info)
	if _, err := capacityFrom(info); err == nil {
		t.Fatal("a machine with no reported memory must not look empty")
	}
}

func TestEntriesPutRecommendationsFirstAndJudgeFit(t *testing.T) {
	models := []Model{
		{ID: "Zeta-GGUF", Size: 2, Labels: []string{"chat"}},
		{ID: "Gemma-4-E4B-it-GGUF", Size: 5.97, Downloaded: true, Labels: []string{"chat"}},
		{ID: "Huge-GGUF", Size: 200, Labels: []string{"chat"}},
		{ID: "Qwen3-Coder-Next-GGUF", Size: 48, Labels: []string{"chat", "coding"}},
	}
	entries := BuildEntries("local", models, capacityOf(t, macM4), nil)
	if entries[0].Model.ID != "Qwen3.8-Flash-Next-GGUF" || entries[0].Listed || entries[0].Selectable() {
		t.Fatalf("unlisted Qwen3.8 Flash should lead, unselectable on a 12 GB Mac: %+v", entries[0])
	}
	if entries[1].Model.ID != "Gemma-4-E4B-it-GGUF" || !entries[1].Selectable() {
		t.Fatalf("Gemma should follow and be selectable: %+v", entries[1])
	}
	byID := map[string]Entry{}
	for _, e := range entries {
		byID[e.Model.ID] = e
	}
	if byID["Qwen3-Coder-Next-GGUF"].Selectable() || byID["Huge-GGUF"].Selectable() {
		t.Fatal("models too big for the Mac were offered")
	}
	if !byID["Zeta-GGUF"].Selectable() || !byID["Zeta-GGUF"].NeedsDownload() {
		t.Fatal("a small model should download and be selectable")
	}
	last := entries[len(entries)-1]
	if last.Selectable() {
		t.Fatal("selectable rows must sort ahead of unselectable ones")
	}
}

func TestUnreadableMachineBlocksEveryDownload(t *testing.T) {
	models := []Model{{ID: "Tiny-GGUF", Size: 0.5, Labels: []string{"chat"}}, {ID: "Have-GGUF", Size: 9, Downloaded: true, Labels: []string{"chat"}}}
	for _, e := range BuildEntries("local", models, Capacity{}, fmt.Errorf("boom")) {
		if e.NeedsDownload() && e.Selectable() {
			t.Fatalf("%s downloadable without a fit check", e.Model.ID)
		}
		if e.Model.ID == "Have-GGUF" && !e.Selectable() {
			t.Fatal("an already downloaded model must stay selectable")
		}
	}
}

func TestFireworksKeepsItsSuggestionFirstAndHidesWhatTheAccountLacks(t *testing.T) {
	entries := BuildEntries("fireworks", []Model{{ID: "fireworks.z", Recipe: "cloud"}, {ID: FireworksModel, Recipe: "cloud"}}, Capacity{}, nil)
	if len(entries) != 2 || entries[0].Model.ID != FireworksModel || !entries[0].Selectable() {
		t.Fatalf("suggested Fireworks model should lead: %+v", entries)
	}
	for _, e := range BuildEntries("fireworks", []Model{{ID: "fireworks.z", Recipe: "cloud"}}, Capacity{}, nil) {
		if e.Model.ID == FireworksModel {
			t.Fatal("a Fireworks model the account lacks was listed")
		}
	}
}

func TestPullRequestRegistersOnlyCustomModels(t *testing.T) {
	builtin := PullRequest(Entry{Model: Model{ID: "Gemma-4-E4B-it-GGUF"}})
	if _, ok := builtin["recipe"]; ok || builtin["model_name"] != "Gemma-4-E4B-it-GGUF" {
		t.Fatalf("built-in pull must go by name only (#1655): %v", builtin)
	}
	r := qwenFlash()
	custom := PullRequest(Entry{Model: Model{ID: r.ID}, Recommended: &r})
	if !strings.HasPrefix(custom["model_name"].(string), "user.") || custom["checkpoint"] != r.Checkpoint ||
		custom["recipe"] != "llamacpp" || custom["mmproj"] != "mmproj-F16.gguf" || custom["vision"] != true {
		t.Fatalf("custom registration incomplete: %v", custom)
	}
}

func TestPullRefusesAModelThatDoesNotFit(t *testing.T) {
	called := false
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true }))
	defer s.Close()
	e := Entry{Model: Model{ID: "Huge-GGUF", Size: 200}, Listed: true, Reason: "needs ~222 GB"}
	if err := New(s.URL).Pull(context.Background(), e, nil); err == nil || called {
		t.Fatalf("pull of a non-fitting model reached Lemonade (err=%v)", err)
	}
}

func TestPullStreamsProgressAndSendsRegistration(t *testing.T) {
	var body map[string]any
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/pull") {
			t.Errorf("path %s", r.URL.Path)
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		fmt.Fprint(w, "event: progress\ndata: {\"file_index\":1,\"total_files\":3,\"bytes_downloaded\":1000000000,\"bytes_total\":4000000000,\"percent\":25}\n\n")
		fmt.Fprint(w, "event: complete\ndata: {\"percent\":100}\n\n")
	}))
	defer s.Close()
	r := qwenFlash()
	e := Entry{Model: Model{ID: r.ID}, Recommended: &r, Fits: true}
	var seen []PullProgress
	if err := New(s.URL).Pull(context.Background(), e, func(p PullProgress) { seen = append(seen, p) }); err != nil {
		t.Fatal(err)
	}
	if len(seen) != 1 || seen[0].Percent != 25 || body["checkpoint"] != r.Checkpoint {
		t.Fatalf("progress=%v body=%v", seen, body)
	}
}

func TestPullStreamErrorsAreReported(t *testing.T) {
	for name, stream := range map[string]string{
		"error event": "event: error\ndata: {\"error\":\"disk full\"}\n\n",
		"truncated":   "event: progress\ndata: {\"percent\":10}\n\n",
	} {
		err := readPullStream(strings.NewReader(stream), "X", nil)
		if err == nil {
			t.Errorf("%s: no error", name)
		}
		if name == "error event" && !strings.Contains(err.Error(), "disk full") {
			t.Errorf("error lost the server's reason: %v", err)
		}
	}
}

func TestCatalogListsUndownloadedChatModelsOnly(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `{"data":[
			{"id":"Gemma-4-E4B-it-GGUF","downloaded":true,"labels":["chat"],"size":5.97},
			{"id":"Qwen3-Coder-Next-GGUF","downloaded":false,"labels":["chat","coding"],"size":48},
			{"id":"LMX-Omni","recipe":"collection.omni","labels":["chat"]},
			{"id":"Whisper-Base","labels":["transcription"]},
			{"id":"embeddinggemma-300m-GGUF","labels":["custom","embeddings"]},
			{"id":"fireworks.glm","recipe":"cloud","labels":["chat"]}]}`)
	}))
	defer s.Close()
	entries, err := New(s.URL).Catalog(context.Background(), "local", capacityOf(t, strixHalo128), nil)
	if err != nil {
		t.Fatal(err)
	}
	listed := map[string]bool{}
	for _, e := range entries {
		if e.Listed {
			listed[e.Model.ID] = true
		}
	}
	if !listed["Gemma-4-E4B-it-GGUF"] || !listed["Qwen3-Coder-Next-GGUF"] || len(listed) != 2 {
		t.Fatalf("listed %v", listed)
	}
}

func TestRecommendedBuiltinMissingFromLemonadeIsUnavailableNotTooBig(t *testing.T) {
	for _, e := range BuildEntries("local", nil, capacityOf(t, strixHalo128), nil) {
		if e.Model.ID != "Qwen3-Coder-Next-GGUF" {
			continue
		}
		if !e.Unavailable() || e.Selectable() || !strings.Contains(e.Reason, "does not offer") {
			t.Fatalf("missing built-in misreported: %+v", e)
		}
		return
	}
	t.Fatal("Qwen3 Coder Next recommendation missing")
}

func fixtureCapacity(t *testing.T, name string, physical string) (Capacity, error) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "hardware", name))
	if err != nil {
		t.Fatal(err)
	}
	var info systemInfo
	if err := json.Unmarshal(raw, &info); err != nil {
		t.Fatal(err)
	}
	if physical != "" {
		info.PhysicalMemory = physical
	}
	return capacityFrom(info)
}

func TestRealLemonadeReportsMatchThePythonRule(t *testing.T) {
	c, err := fixtureCapacity(t, "lemonade11_amd_igpu_linux.json", "")
	if err != nil || c.MemorySource != "AMD iGPU" || c.MemoryGB < 62.9 || c.MemoryGB > 63.1 {
		t.Fatalf("linux strix halo: %+v %v", c, err)
	}
	if c, err = fixtureCapacity(t, "lemonade11_metal_macos.json", ""); err != nil || c.MemorySource != "Apple GPU" {
		t.Fatalf("macos: %+v %v", c, err)
	}
	// A GPU without reported memory must not be judged on a big system RAM.
	if c, err = fixtureCapacity(t, "lemonade11_amd_dgpu_windows.json", "128 GB"); err == nil {
		t.Fatalf("judged a VRAM-less GPU on system RAM: %+v", c)
	}
}

func TestUnknownFitIsLabelledAsSuch(t *testing.T) {
	for _, e := range BuildEntries("local", []Model{{ID: "Tiny-GGUF", Size: 1, Labels: []string{"chat"}}}, Capacity{}, fmt.Errorf("boom")) {
		if e.Model.ID == "Tiny-GGUF" && (!e.FitUnknown || e.Selectable()) {
			t.Fatalf("%+v", e)
		}
	}
}
