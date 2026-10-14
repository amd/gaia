// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// llamacppBackends are the backends Lemonade's /load accepts, the same list as
// gaia.llm.model_server_info.LLAMACPP_BACKENDS.
var llamacppBackends = []string{"vulkan", "rocm", "metal", "cpu"}

var backendLabels = map[string]string{
	"vulkan": "Vulkan", "rocm": "ROCm", "metal": "Metal", "cpu": "CPU",
	"npu": "NPU", "remote": "remote (cloud)",
}

// LoadedModel is one model Lemonade has loaded. Backend is empty when Lemonade
// did not say, which renders as "unknown".
type LoadedModel struct {
	Name    string
	Backend string
	// FromDefault is set when the backend is Lemonade's default, not one the
	// load named or the launch path showed.
	FromDefault bool
	CtxSize     int
}

// ServerInfo is which Lemonade GAIA talks to and what it runs, mirroring the
// Agent UI's Settings → Advanced → Model server panel.
type ServerInfo struct {
	BaseURL string
	Port    int
	// Owner is "gaia" (GAIA's own Lemonade), "user" (another one on this PC),
	// or "remote".
	Owner    string
	Version  string
	Chat     *LoadedModel
	Embedder *LoadedModel
}

type loadedEntry struct {
	ModelName     string          `json:"model_name"`
	Type          string          `json:"type"`
	Recipe        string          `json:"recipe"`
	Device        string          `json:"device"`
	LaunchCommand json.RawMessage `json:"launch_command"`
	RecipeOptions json.RawMessage `json:"recipe_options"`
}

type healthReply struct {
	Version     string        `json:"version"`
	ModelLoaded string        `json:"model_loaded"`
	Loaded      []loadedEntry `json:"all_models_loaded"`
}

type sysInfoReply struct {
	Recipes struct {
		Llamacpp struct {
			DefaultBackend string `json:"default_backend"`
		} `json:"llamacpp"`
	} `json:"recipes"`
}

var nonChatTypes = map[string]bool{
	"embedding": true, "reranking": true, "transcription": true, "image": true, "tts": true, "audio": true,
}

func describeEntry(e loadedEntry, defaultBackend string) *LoadedModel {
	m := &LoadedModel{Name: e.ModelName}
	if e.Recipe == "cloud" || IsCloudID(e.ModelName) {
		m.Backend = "remote"
		return m
	}
	// Any field may be absent or of an unexpected type on an older Lemonade.
	var opts struct {
		CtxSize json.Number `json:"ctx_size"`
		Backend any         `json:"llamacpp_backend"`
	}
	_ = json.Unmarshal(e.RecipeOptions, &opts)
	if n, err := opts.CtxSize.Int64(); err == nil && n > 0 {
		m.CtxSize = int(n)
	}
	switch {
	case e.Recipe == "llamacpp":
		if b, ok := opts.Backend.(string); ok && b != "" {
			m.Backend = b
		} else if b := backendFromLaunch(e.LaunchCommand); b != "" {
			m.Backend = b
		} else if defaultBackend != "" {
			m.Backend, m.FromDefault = defaultBackend, true
		}
	case strings.Contains(strings.ToLower(e.Device), "npu"):
		m.Backend = "npu"
	}
	return m
}

func backendFromLaunch(raw json.RawMessage) string {
	var cmd []any
	if json.Unmarshal(raw, &cmd) != nil || len(cmd) == 0 {
		return ""
	}
	program, _ := cmd[0].(string)
	parts := strings.Split(strings.ToLower(strings.ReplaceAll(program, `\`, "/")), "/")
	for _, b := range llamacppBackends {
		for _, p := range parts {
			if p == b {
				return b
			}
		}
	}
	return ""
}

// describeServer reads /health and /system-info replies into a ServerInfo.
func describeServer(base string, embeddedPort int, health healthReply, sys sysInfoReply) ServerInfo {
	info := ServerInfo{BaseURL: base, Owner: serverOwner(base, embeddedPort), Version: health.Version}
	if u, err := url.Parse(base); err == nil {
		info.Port, _ = strconv.Atoi(u.Port())
	}
	def := sys.Recipes.Llamacpp.DefaultBackend
	var chat []loadedEntry
	for _, e := range health.Loaded {
		if e.Type == "embedding" && info.Embedder == nil {
			info.Embedder = describeEntry(e, def)
		}
		if !nonChatTypes[e.Type] {
			chat = append(chat, e)
		}
	}
	for _, e := range chat {
		if health.ModelLoaded != "" && strings.EqualFold(e.ModelName, health.ModelLoaded) {
			info.Chat = describeEntry(e, def)
		}
	}
	if info.Chat == nil && len(chat) > 0 {
		info.Chat = describeEntry(chat[0], def)
	}
	return info
}

func serverOwner(base string, embeddedPort int) string {
	u, err := url.Parse(base)
	if err != nil {
		return "remote"
	}
	switch strings.ToLower(u.Hostname()) {
	case "localhost", "127.0.0.1", "::1":
	default:
		return "remote"
	}
	if embeddedPort > 0 && u.Port() == strconv.Itoa(embeddedPort) {
		return "gaia"
	}
	return "user"
}

// BackendLabel is "Vulkan", "ROCm", "CPU", "NPU", "remote", or "unknown".
func BackendLabel(b string) string {
	if b == "" {
		return "unknown"
	}
	if l, ok := backendLabels[b]; ok {
		return l
	}
	return b
}

// clip shortens s to at most n runes, ending in "…".
func clip(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n-1]) + "…"
}

// StatusLine is one line for a status bar or `gaia-tui status`:
// "Lemonade 2026.41.1 (GAIA's own) at localhost:51234 · Qwen3.6-35B-A3B-GGUF on Vulkan, 64K ctx".
func (s ServerInfo) StatusLine() string {
	owner := map[string]string{"gaia": "GAIA's own", "user": "yours", "remote": "remote"}[s.Owner]
	head := "Lemonade"
	if s.Version != "" {
		head += " " + s.Version
	}
	where := s.BaseURL
	if u, err := url.Parse(s.BaseURL); err == nil && u.Host != "" {
		where = u.Host
	}
	line := fmt.Sprintf("%s (%s) at %s", head, owner, where)
	if s.Chat == nil {
		return line + " · no chat model loaded"
	}
	if s.Chat.Backend == "remote" {
		return line + " · " + clip(s.Chat.Name, 48) + " (cloud, runs remotely)"
	}
	line += " · " + clip(s.Chat.Name, 48) + " on " + BackendLabel(s.Chat.Backend)
	if s.Chat.FromDefault {
		line += " (default)"
	}
	if s.Chat.CtxSize > 0 {
		line += fmt.Sprintf(", %dK ctx", s.Chat.CtxSize/1024)
	}
	return line
}

// get reads one Lemonade endpoint for display. Unlike request it accepts a
// remote server, since nothing is written and no pasted key is sent.
func (c *Client) get(ctx context.Context, path string, out any) error {
	base := c.current()
	u, err := url.Parse(base)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil {
		return fmt.Errorf("Invalid Lemonade address")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base+path, nil)
	if err != nil {
		return fmt.Errorf("Invalid Lemonade address")
	}
	if key := APIKeyFor(base); key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return ErrUnreachable
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("Lemonade returned HTTP %d for %s", resp.StatusCode, path)
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 4<<20)).Decode(out) != nil {
		return fmt.Errorf("Lemonade returned an invalid response")
	}
	return nil
}

// ServerInfo reads which Lemonade this is and what it runs. A Lemonade that
// answers /health but not /system-info still reports its models, with the
// backend unknown where the load did not name one.
func (c *Client) ServerInfo(ctx context.Context) (ServerInfo, error) {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	base := c.current()
	var health healthReply
	if err := c.get(ctx, "/health", &health); err != nil {
		if err == ErrUnreachable {
			return ServerInfo{BaseURL: base}, fmt.Errorf("Lemonade is not answering at %s", base)
		}
		return ServerInfo{BaseURL: base}, fmt.Errorf("Could not read Lemonade at %s: %v", base, err)
	}
	var sys sysInfoReply
	_ = c.get(ctx, "/system-info", &sys) // optional: older servers lack the backend fields
	port := 0
	if state := ReadEmbedded(); state != nil {
		port = state.Port
	}
	return describeServer(base, port, health, sys), nil
}
