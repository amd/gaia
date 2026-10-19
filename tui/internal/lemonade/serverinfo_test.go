// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func decodeHealth(t *testing.T, raw string) healthReply {
	t.Helper()
	var h healthReply
	if err := json.Unmarshal([]byte(raw), &h); err != nil {
		t.Fatalf("health fixture: %v", err)
	}
	return h
}

func TestDescribeServerReadsTheChatModelsBackend(t *testing.T) {
	h := decodeHealth(t, `{"version":"2026.41.1","model_loaded":"embeddinggemma-300m-GGUF","all_models_loaded":[
		{"model_name":"embeddinggemma-300m-GGUF","type":"embedding","recipe":"llamacpp","recipe_options":{"llamacpp_backend":"cpu"}},
		{"model_name":"Qwen3.6-35B-A3B-GGUF","type":"llm","recipe":"llamacpp","recipe_options":{"ctx_size":65536,"llamacpp_backend":"rocm"}}]}`)
	var sys sysInfoReply
	sys.Recipes.Llamacpp.DefaultBackend = "vulkan"

	info := describeServer("http://localhost:51234/api/v1", 51234, h, sys)

	if info.Owner != "gaia" || info.Port != 51234 {
		t.Fatalf("owner/port = %q/%d", info.Owner, info.Port)
	}
	if info.Chat == nil || info.Chat.Name != "Qwen3.6-35B-A3B-GGUF" || info.Chat.Backend != "rocm" || info.Chat.CtxSize != 65536 {
		t.Fatalf("chat = %+v", info.Chat)
	}
	if info.Embedder == nil || info.Embedder.Backend != "cpu" {
		t.Fatalf("embedder = %+v", info.Embedder)
	}
	want := "Lemonade 2026.41.1 (GAIA's own) at localhost:51234 · Qwen3.6-35B-A3B-GGUF on ROCm, 64K ctx"
	if got := info.StatusLine(); got != want {
		t.Fatalf("StatusLine() =\n %q\nwant\n %q", got, want)
	}
}

func TestOlderLemonadeWithoutBackendInfoReadsUnknown(t *testing.T) {
	h := decodeHealth(t, `{"all_models_loaded":[{"model_name":"Old-Model","recipe":"llamacpp","recipe_options":"weird","launch_command":"not-a-list"}]}`)

	info := describeServer("http://localhost:13305/api/v1", 0, h, sysInfoReply{})

	if info.Chat == nil || info.Chat.Backend != "" || info.Chat.CtxSize != 0 {
		t.Fatalf("chat = %+v", info.Chat)
	}
	if got := info.StatusLine(); got != "Lemonade (yours) at localhost:13305 · Old-Model on unknown" {
		t.Fatalf("StatusLine() = %q", got)
	}
}

func TestBackendFromLaunchPathAndDefault(t *testing.T) {
	var sys sysInfoReply
	sys.Recipes.Llamacpp.DefaultBackend = "vulkan"
	h := decodeHealth(t, `{"all_models_loaded":[{"model_name":"A","recipe":"llamacpp",
		"launch_command":["C:\\Users\\x\\.cache\\lemonade\\bin\\llamacpp\\rocm\\llama-server.exe","-m","a.gguf"]}]}`)
	if b := describeServer("http://localhost:1/api/v1", 0, h, sys).Chat.Backend; b != "rocm" {
		t.Fatalf("launch backend = %q", b)
	}
	h = decodeHealth(t, `{"all_models_loaded":[{"model_name":"A","recipe":"llamacpp"}]}`)
	chat := describeServer("http://localhost:1/api/v1", 0, h, sys).Chat
	if chat.Backend != "vulkan" || !chat.FromDefault {
		t.Fatalf("default backend = %+v", chat)
	}
}

func TestCloudModelIsRemoteNotALocalBackend(t *testing.T) {
	var sys sysInfoReply
	sys.Recipes.Llamacpp.DefaultBackend = "vulkan"
	h := decodeHealth(t, `{"all_models_loaded":[{"model_name":"amd.DeepSeek-V4.1-Flash","recipe":"cloud","recipe_options":{"ctx_size":4096}}]}`)

	chat := describeServer("http://localhost:1/api/v1", 0, h, sys).Chat

	if chat.Backend != "remote" || chat.CtxSize != 0 {
		t.Fatalf("cloud chat = %+v", chat)
	}
	line := describeServer("http://localhost:1/api/v1", 0, h, sys).StatusLine()
	if !strings.HasSuffix(line, "amd.DeepSeek-V4.1-Flash (cloud, runs remotely)") || strings.Contains(line, "Vulkan") {
		t.Fatalf("StatusLine() = %q", line)
	}
}

func TestNPUModelAndRemoteServer(t *testing.T) {
	h := decodeHealth(t, `{"all_models_loaded":[{"model_name":"gemma4-it-e2b-FLM","recipe":"flm","device":"npu"}]}`)
	info := describeServer("https://lemonade.example.com/api/v1", 0, h, sysInfoReply{})
	if info.Owner != "remote" || info.Chat.Backend != "npu" {
		t.Fatalf("info = %+v chat = %+v", info, info.Chat)
	}
}

func TestVeryLongModelNameIsClippedInTheStatusLine(t *testing.T) {
	long := "user." + strings.Repeat("Very-Long-Model-Name-", 20) + "GGUF"
	info := ServerInfo{BaseURL: "http://localhost:1/api/v1", Owner: "user", Chat: &LoadedModel{Name: long, Backend: "vulkan"}}
	line := info.StatusLine()
	if strings.Contains(line, long) || !strings.Contains(line, "…") || len([]rune(line)) > 120 {
		t.Fatalf("line not clipped: %q", line)
	}
}

func TestServerInfoSurvivesAMissingSystemInfo(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/health") {
			_, _ = w.Write([]byte(`{"version":"9.1.4","all_models_loaded":[{"model_name":"M","recipe":"llamacpp"}]}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer s.Close()
	t.Setenv("GAIA_HOME", t.TempDir())

	info, err := New(s.URL).ServerInfo(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if info.Version != "9.1.4" || info.Chat == nil || info.Chat.Backend != "" {
		t.Fatalf("info = %+v chat = %+v", info, info.Chat)
	}
}

func TestServerInfoUnreachableIsOnePlainLine(t *testing.T) {
	s := httptest.NewServer(http.NotFoundHandler())
	url := s.URL
	s.Close()
	t.Setenv("GAIA_HOME", t.TempDir())

	_, err := New(url).ServerInfo(context.Background())
	if err == nil || strings.Contains(err.Error(), "\n") || !strings.Contains(err.Error(), "not answering at") {
		t.Fatalf("err = %v", err)
	}
}
