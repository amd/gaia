// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/amd/gaia/tui/internal/daemon"
)

const DefaultBaseURL = "http://localhost:13305/api/v1"

// EmbeddedState records the private Lemonade endpoint, whose port and key are
// chosen when GAIA starts it. Never include this structure in logs or errors.
type EmbeddedState struct {
	PID    int    `json:"pid"`
	Port   int    `json:"port"`
	APIKey string `json:"api_key"`
}

// ReadEmbedded uses the same state directory as Python's EmbeddedLemonade.
// An explicit GAIA_HOME isolates the entire runtime: absent or malformed state
// there must not cause a connection to the user's unrelated default instance.
func ReadEmbedded() *EmbeddedState {
	dir, ok := embeddedDir()
	if !ok {
		return nil
	}
	raw, err := os.ReadFile(filepath.Join(dir, "state.json"))
	if err != nil {
		return nil
	}
	var state EmbeddedState
	if json.Unmarshal(raw, &state) != nil || state.Port < 1 || state.Port > 65535 {
		return nil
	}
	// A server killed without `stop` leaves its record behind; following it
	// sends every client to a dead port. Python's reader drops it the same way.
	if state.PID > 0 && !daemon.PIDAlive(state.PID) {
		return nil
	}
	state.APIKey = strings.TrimSpace(state.APIKey)
	return &state
}

// EmbeddedInstalled reports whether GAIA's private Lemonade is unpacked, running
// or not. The state file only exists while it runs, so it cannot answer this.
func EmbeddedInstalled() bool {
	dir, ok := embeddedDir()
	if !ok {
		return false
	}
	matches, _ := filepath.Glob(filepath.Join(dir, "dist", "*", "lemond*"))
	return len(matches) > 0
}

// embeddedDir is <GAIA_HOME>/lemonade, as Python's EmbeddedLemonade lays it out.
func embeddedDir() (string, bool) {
	dir := strings.TrimSpace(os.Getenv("GAIA_HOME"))
	if dir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", false
		}
		dir = filepath.Join(home, ".gaia")
	}
	return filepath.Join(dir, "lemonade"), true
}

// ResolveBaseURL keeps an explicitly selected endpoint, then the user's choice
// (LEMONADE_BASE_URL, else the saved external server), the private runtime,
// and finally Lemonade's standard port. Mirrors Python's
// resolve_lemonade_base_url.
func ResolveBaseURL(base string) string {
	if base = strings.TrimSpace(base); base != "" {
		return normalizeBaseURL(base)
	}
	if base = configuredURL(); base != "" {
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

// APIKeyFor is the key to send to base: LEMONADE_API_KEY, else the key saved
// for the user's own server (only to that server), else GAIA's own server's
// key (only to its recorded endpoint). An error means a key was saved for base
// but could not be fetched; sending none would only earn a confusing 401.
func APIKeyFor(ctx context.Context, base string) (string, error) {
	if key := strings.TrimSpace(os.Getenv("LEMONADE_API_KEY")); key != "" && !ownCredentials() {
		return key, nil
	}
	if err := ChoiceError(); err != nil {
		return "", err
	}
	target := ResolveBaseURL(base)
	if key, ok, err := savedExternalKey(ctx, target); err != nil || ok {
		return key, err
	}
	state := ReadEmbedded()
	if state == nil || state.APIKey == "" {
		return "", nil
	}
	if isEmbeddedEndpoint(target, state.Port) {
		return state.APIKey, nil
	}
	return "", nil
}

// isEmbeddedEndpoint reports whether target is GAIA's own lemond on port. An
// unparseable target is not.
func isEmbeddedEndpoint(target string, port int) bool {
	u, err := url.Parse(target)
	if err != nil || u.Scheme != "http" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Port() != strconv.Itoa(port) {
		return false
	}
	// lemond binds localhost. Treat its IPv4/IPv6 spellings as the same
	// endpoint, without extending that trust to arbitrary 127/8 addresses.
	switch strings.ToLower(u.Hostname()) {
	case "localhost", "127.0.0.1", "::1":
		return true
	}
	return false
}
