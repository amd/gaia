// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

const DefaultBaseURL = "http://localhost:13305/api/v1"

// EmbeddedState records the private Lemonade endpoint, whose port and key are
// chosen when GAIA starts it. Never include this structure in logs or errors.
type EmbeddedState struct {
	Port   int    `json:"port"`
	APIKey string `json:"api_key"`
}

// ReadEmbedded uses the same state directory as Python's EmbeddedLemonade.
// An explicit GAIA_HOME isolates the entire runtime: absent or malformed state
// there must not cause a connection to the user's unrelated default instance.
func ReadEmbedded() *EmbeddedState {
	dir := strings.TrimSpace(os.Getenv("GAIA_HOME"))
	if dir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return nil
		}
		dir = filepath.Join(home, ".gaia")
	}
	raw, err := os.ReadFile(filepath.Join(dir, "lemonade", "state.json"))
	if err != nil {
		return nil
	}
	var state EmbeddedState
	if json.Unmarshal(raw, &state) != nil || state.Port < 1 || state.Port > 65535 {
		return nil
	}
	state.APIKey = strings.TrimSpace(state.APIKey)
	return &state
}

// ResolveBaseURL keeps an explicitly selected endpoint, then checks the
// environment, the private runtime, and finally Lemonade's standard port.
func ResolveBaseURL(base string) string {
	if base = strings.TrimSpace(base); base != "" {
		return normalizeBaseURL(base)
	}
	if base = strings.TrimSpace(os.Getenv("LEMONADE_BASE_URL")); base != "" {
		return normalizeBaseURL(base)
	}
	if state := ReadEmbedded(); state != nil {
		return fmt.Sprintf("http://localhost:%d/api/v1", state.Port)
	}
	return DefaultBaseURL
}

// A bare Lemonade origin means its standard API. Preserve an explicitly
// configured API path, including reverse proxies serving a different prefix.
func normalizeBaseURL(base string) string {
	base = strings.TrimRight(base, "/")
	u, err := url.Parse(base)
	if err == nil && u.Hostname() != "" && u.Path == "" {
		u.Path = "/api/v1"
		return u.String()
	}
	return base
}

// APIKeyFor scopes automatically discovered credentials to the private
// endpoint that issued them. A user-supplied environment key is an explicit
// override and remains available for other configured Lemonade servers.
func APIKeyFor(base string) string {
	if key := strings.TrimSpace(os.Getenv("LEMONADE_API_KEY")); key != "" {
		return key
	}
	state := ReadEmbedded()
	if state == nil || state.APIKey == "" {
		return ""
	}
	u, err := url.Parse(ResolveBaseURL(base))
	if err != nil || u.Scheme != "http" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Port() != strconv.Itoa(state.Port) {
		return ""
	}
	// lemond binds localhost. Treat its IPv4/IPv6 spellings as the same
	// endpoint, without extending that trust to arbitrary 127/8 addresses.
	switch strings.ToLower(u.Hostname()) {
	case "localhost", "127.0.0.1", "::1":
		return state.APIKey
	}
	return ""
}
