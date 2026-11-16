// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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

	info := describeServer("http://localhost:51234/api/v1", 51234, h, sys, "")

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

	info := describeServer("http://localhost:13305/api/v1", 0, h, sysInfoReply{}, "")

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
	if b := describeServer("http://localhost:1/api/v1", 0, h, sys, "").Chat.Backend; b != "rocm" {
		t.Fatalf("launch backend = %q", b)
	}
	h = decodeHealth(t, `{"all_models_loaded":[{"model_name":"A","recipe":"llamacpp"}]}`)
	chat := describeServer("http://localhost:1/api/v1", 0, h, sys, "").Chat
	if chat.Backend != "vulkan" || !chat.FromDefault {
		t.Fatalf("default backend = %+v", chat)
	}
}

func TestCloudModelIsRemoteNotALocalBackend(t *testing.T) {
	var sys sysInfoReply
	sys.Recipes.Llamacpp.DefaultBackend = "vulkan"
	h := decodeHealth(t, `{"all_models_loaded":[{"model_name":"amd.DeepSeek-V4.1-Flash","recipe":"cloud","recipe_options":{"ctx_size":4096}}]}`)

	chat := describeServer("http://localhost:1/api/v1", 0, h, sys, "").Chat

	if chat.Backend != "remote" || chat.CtxSize != 0 {
		t.Fatalf("cloud chat = %+v", chat)
	}
	line := describeServer("http://localhost:1/api/v1", 0, h, sys, "").StatusLine()
	if !strings.HasSuffix(line, "amd.DeepSeek-V4.1-Flash (cloud, runs remotely)") || strings.Contains(line, "Vulkan") {
		t.Fatalf("StatusLine() = %q", line)
	}
}

func TestNPUModelAndRemoteServer(t *testing.T) {
	h := decodeHealth(t, `{"all_models_loaded":[{"model_name":"gemma4-it-e2b-FLM","recipe":"flm","device":"npu"}]}`)
	info := describeServer("https://lemonade.example.com/api/v1", 0, h, sysInfoReply{}, "")
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
			_, _ = w.Write([]byte(`{"version":"9.1.4","all_models_loaded":[{"model_name":"Gemma-4-E4B-it-GGUF","recipe":"llamacpp"}]}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer s.Close()
	t.Setenv("GAIA_HOME", t.TempDir())
	t.Setenv("GAIA_CONFIG_FILE", filepath.Join(t.TempDir(), "config.json"))

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

func TestALocalModelBesideACloudOneIsStillTheLocalChat(t *testing.T) {
	h := decodeHealth(t, `{"model_loaded":"fireworks.deepseek-v4p1-flash","all_models_loaded":[
		{"model_name":"fireworks.deepseek-v4p1-flash","type":"llm","recipe":"cloud"},
		{"model_name":"Gemma-4-E4B-it-GGUF","type":"llm","recipe":"llamacpp","recipe_options":{"llamacpp_backend":"metal"}}]}`)

	info := describeServer("http://localhost:1/api/v1", 0, h, sysInfoReply{}, "")

	if info.Chat == nil || info.Chat.Backend != "remote" {
		t.Fatalf("chat = %+v, want the active cloud model", info.Chat)
	}
	if info.LocalChat == nil || info.LocalChat.Name != "Gemma-4-E4B-it-GGUF" {
		t.Fatalf("local chat = %+v, want the resident Gemma", info.LocalChat)
	}
}

func TestOnlyACloudModelLeavesNoLocalChat(t *testing.T) {
	h := decodeHealth(t, `{"model_loaded":"fireworks.deepseek-v4p1-flash","all_models_loaded":[
		{"model_name":"fireworks.deepseek-v4p1-flash","type":"llm","recipe":"cloud"}]}`)

	if info := describeServer("http://localhost:1/api/v1", 0, h, sysInfoReply{}, ""); info.LocalChat != nil {
		t.Fatalf("local chat = %+v, want none", info.LocalChat)
	}
}

// sharedServer is a Lemonade another app also uses: it loaded a cloud model
// last, while GAIA's local Gemma (registered as user.) stays loaded.
func sharedServer(t *testing.T, config string) ServerInfo {
	t.Helper()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/health") {
			_, _ = w.Write([]byte(`{"version":"10.8.1","model_loaded":"fireworks.kimi-k2p7","all_models_loaded":[
				{"model_name":"fireworks.kimi-k2p7","type":"llm","recipe":"cloud"},
				{"model_name":"Qwen3.6-35B-A3B-GGUF","type":"llm","recipe":"llamacpp","recipe_options":{"llamacpp_backend":"rocm"}},
				{"model_name":"Gemma-4-E4B-it-GGUF","type":"llm","recipe":"llamacpp","recipe_options":{"ctx_size":65536,"llamacpp_backend":"vulkan"}}]}`))
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(s.Close)
	home := t.TempDir()
	t.Setenv("GAIA_HOME", home)
	path := filepath.Join(home, "config.json")
	t.Setenv("GAIA_CONFIG_FILE", path)
	if config != "" {
		if err := os.WriteFile(path, []byte(config), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	info, err := New(s.URL).ServerInfo(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	return info
}

func TestASharedServersLastModelIsNotGaiasChatModel(t *testing.T) {
	info := sharedServer(t, "")

	if info.Chat == nil || info.Chat.Name != "Gemma-4-E4B-it-GGUF" || info.Chat.Backend != "vulkan" {
		t.Fatalf("chat = %+v, want GAIA's default Gemma, not the other app's cloud model", info.Chat)
	}
	if line := info.StatusLine(); !strings.HasSuffix(line, "· Gemma-4-E4B-it-GGUF on Vulkan, 64K ctx") {
		t.Fatalf("StatusLine() = %q", line)
	}
}

func TestTheChatModelFollowsConfigIgnoringTheUserPrefix(t *testing.T) {
	for _, tc := range []struct{ name, config, want string }{
		{"default_model", `{"default_model":"Qwen3.6-35B-A3B-GGUF"}`, "Qwen3.6-35B-A3B-GGUF"},
		{"last_model wins", `{"default_model":"Qwen3.6-35B-A3B-GGUF","last_model":"user.Gemma-4-E4B-it-GGUF"}`, "Gemma-4-E4B-it-GGUF"},
		{"hybrid starts on its cheap model", `{"default_model":"hybrid","hybrid_cheap_model":"user.Qwen3.6-35B-A3B-GGUF","hybrid_strong_model":"amd.Claude-Opus-5-5"}`, "Qwen3.6-35B-A3B-GGUF"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if info := sharedServer(t, tc.config); info.Chat == nil || info.Chat.Name != tc.want {
				t.Fatalf("chat = %+v, want %s", info.Chat, tc.want)
			}
		})
	}
}

func TestGaiasChatModelNotLoadedIsSaidNotReplaced(t *testing.T) {
	info := sharedServer(t, `{"last_model":"Llama-4-Scout-GGUF"}`)

	if info.Chat != nil || info.LocalChat != nil {
		t.Fatalf("chat = %+v, local = %+v, want neither: GAIA's model isn't loaded", info.Chat, info.LocalChat)
	}
	if line := info.StatusLine(); !strings.HasSuffix(line, "· Llama-4-Scout-GGUF not loaded") {
		t.Fatalf("StatusLine() = %q", line)
	}
}

func TestAnUnreadableConfigNamesNoChatModel(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	t.Setenv("GAIA_CONFIG_FILE", path)
	if err := os.WriteFile(path, []byte(`{not json`), 0o600); err != nil {
		t.Fatal(err)
	}

	if got := GaiaChatModel(); got != "" {
		t.Fatalf("GaiaChatModel() = %q, want \"\" so Lemonade's own report is used", got)
	}
}
