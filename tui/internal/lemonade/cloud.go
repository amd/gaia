// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package lemonade configures cloud routing without passing credentials to agents.
package lemonade

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const FireworksURL = "https://api.fireworks.ai/inference/v1"

// Recommendation is a Fireworks model worth steering users to, with the reason.
type Recommendation struct {
	ID   string
	Note string
	// Evidence is the measured result behind the note, one line, or empty
	// when the rank rests on the benchmark alone.
	Evidence string
}

// RecommendedModels is ranked by the agent task benchmark (September 2026); refresh it as models change.
//
// Every Evidence figure is transcribed from the GAIA-harness rows of the
// harness × model table published in amd/gaia#4335, over the `everyday` suite
// defined in eval/tasks/tasks.json: 14 tasks, mean of 3 runs, graded by a blind
// Opus 5 judge, cost metered from Fireworks' own billing. Regenerate with
//
//	gaia eval tasks run --suite everyday --model <id> --repeats 3
//
// and update EvidenceSource below in the same edit; TestEvidenceIsTranscribed
// fails when a figure here drifts from it.
var RecommendedModels = []Recommendation{
	{
		ID:       "fireworks.glm-5p3-flash",
		Note:     "best overall, cheapest",
		Evidence: "14/14 tasks · quality 4.89/5 · $0.09 per 14-task run",
	},
	{
		ID:       "fireworks.deepseek-v4p1-flash",
		Note:     "fastest",
		Evidence: "14/14 tasks · quality 4.92/5 · $0.10 per 14-task run",
	},
	{ID: "fireworks.deepseek-v4-pro-0813", Note: "most truthful"},
}

// EvidenceSource is the published cell each Evidence figure was read from, keyed
// by model id: passed, quality, cost. Kept beside the strings so a drifting
// figure fails a test instead of shipping as an unsourced measurement.
var EvidenceSource = map[string]struct {
	Passed, Quality, Cost string
}{
	// from amd/gaia#4335, "What it measures today (14 everyday tasks, harness × model)",
	// row "GLM-5.3 Flash · glm-5p3-flash | GAIA | 3 | 14/14 | 4.89 | … | 7:04 | $0.09"
	"fireworks.glm-5p3-flash": {"14/14", "4.89", "$0.09"},
	// same table, row "DeepSeek V4.1 Flash · deepseek-v4p1-flash | GAIA | 3 | 14/14 | 4.92 | … | 5:39 | $0.10"
	"fireworks.deepseek-v4p1-flash": {"14/14", "4.92", "$0.10"},
}

func TopRecommendation() Recommendation { return RecommendedModels[0] }

// rankKey reduces a cloud id to provider + trailing name segment, so that
// "fireworks.accounts/fireworks/models/glm-5p3-flash" and
// "fireworks.glm-5p3-flash" — both forms Lemonade reports — compare equal. The
// provider stays in the key so amd.<name> never matches a Fireworks entry.
func rankKey(id string) string {
	provider, name, found := strings.Cut(id, ".")
	if !found {
		return id
	}
	return provider + "." + name[strings.LastIndex(name, "/")+1:]
}

// Evidence returns the measured line behind a recommended model, or "".
func Evidence(id string) string {
	key := rankKey(id)
	for _, r := range RecommendedModels {
		if rankKey(r.ID) == key {
			return r.Evidence
		}
	}
	return ""
}

// Rank returns a model's 1-based rank and note, or ok=false when it is not recommended.
func Rank(id string) (rank int, note string, ok bool) {
	key := rankKey(id)
	for i, r := range RecommendedModels {
		if rankKey(r.ID) == key {
			return i + 1, r.Note, true
		}
	}
	return 0, "", false
}

type Provider struct {
	Name       string `json:"name"`
	BaseURL    string `json:"base_url"`
	Header     string `json:"auth_header_name"`
	Prefix     string `json:"auth_header_prefix"`
	EnvKey     bool   `json:"env_var_set"`
	RuntimeKey bool   `json:"runtime_key_set"`
}
type Model struct {
	ID            string   `json:"id"`
	ContextLength int      `json:"context_length"`
	Recipe        string   `json:"recipe"`
	Provider      string   `json:"cloud_provider"`
	Downloaded    bool     `json:"downloaded"`
	Labels        []string `json:"labels"`
}

func (m Model) Cloud() bool { return m.Recipe == "cloud" || m.Provider != "" || IsCloudID(m.ID) }
func IsCloudID(id string) bool {
	return strings.HasPrefix(id, "fireworks.") || strings.HasPrefix(id, "amd.")
}
func Label(provider string) string {
	switch provider {
	case "local":
		return "Local"
	case "fireworks":
		return "Fireworks AI"
	case "amd":
		return "AMD LLM Gateway"
	}
	return provider
}

// ErrUnreachable is returned when nothing answered at the Lemonade address, so
// a caller that knows Lemonade is not set up yet can say that instead.
var ErrUnreachable = errors.New("Lemonade did not respond. Start it, check its address, and retry")

type Client struct {
	BaseURL string
	HTTP    *http.Client
}

func New(base string) *Client {
	return &Client{ResolveBaseURL(base), &http.Client{Timeout: 45 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
}
func validateURL(raw string, loopback bool) error {
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return fmt.Errorf("Enter a base URL without credentials, query parameters, or fragments")
	}
	ip := net.ParseIP(u.Hostname())
	local := u.Hostname() == "localhost" || (ip != nil && ip.IsLoopback())
	if (u.Scheme != "https" && !(loopback && u.Scheme == "http" && local)) || (loopback && !local) {
		return fmt.Errorf("Use HTTPS for gateways; provider setup requires a loopback Lemonade server")
	}
	return nil
}
func (c *Client) request(ctx context.Context, method, path string, data any, result any) error {
	// Configuration changes are local administrative operations. Never send a
	// pasted key to an arbitrary remote Lemonade address or follow a redirect.
	if err := validateURL(c.BaseURL, true); err != nil {
		return err
	}
	var body io.Reader
	if data != nil {
		b, err := json.Marshal(data)
		if err != nil {
			return fmt.Errorf("Could not encode provider settings")
		}
		body = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, body)
	if err != nil {
		return fmt.Errorf("Invalid Lemonade address")
	}
	req.Header.Set("Content-Type", "application/json")
	if key := APIKeyFor(c.BaseURL); key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return ErrUnreachable
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		switch resp.StatusCode {
		case 401, 403:
			return fmt.Errorf("Authentication was rejected. Check the Lemonade or provider credential and retry")
		case 404:
			return fmt.Errorf("Cloud setup is unavailable. Update Lemonade to 11.8.1 or later and retry")
		case 409:
			return fmt.Errorf("Lemonade has an environment key for this provider. It takes precedence; the pasted key was not saved. Leave the key blank to use it")
		default:
			return fmt.Errorf("Lemonade rejected the operation (HTTP %d). Check provider settings and retry", resp.StatusCode)
		}
	}
	if result != nil && json.NewDecoder(io.LimitReader(resp.Body, 4<<20)).Decode(result) != nil {
		return fmt.Errorf("Lemonade returned an invalid response")
	}
	return nil
}
func (c *Client) Providers(ctx context.Context) ([]Provider, error) {
	var reply struct {
		Cloud struct {
			Providers []Provider `json:"providers"`
		} `json:"cloud"`
	}
	err := c.request(ctx, "GET", "/system-info", nil, &reply)
	return reply.Cloud.Providers, err
}
func (c *Client) Models(ctx context.Context, provider string) ([]Model, error) {
	var reply struct {
		Data []Model `json:"data"`
	}
	if err := c.request(ctx, "GET", "/models?show_all=true", nil, &reply); err != nil {
		return nil, err
	}
	var out []Model
	for _, m := range reply.Data {
		chat := true
		for _, label := range m.Labels {
			switch label {
			case "embeddings", "image", "reranker", "audio", "tts", "stt":
				chat = false
			}
		}
		if !chat || m.ID == "" {
			continue
		}
		if provider == "local" {
			if !m.Cloud() && m.Downloaded {
				out = append(out, m)
			}
		} else if m.Cloud() && strings.HasPrefix(m.ID, provider+".") {
			out = append(out, m)
		}
	}
	return out, nil
}
func (c *Client) Configure(ctx context.Context, p Provider, key string) error {
	if p.Name != "fireworks" && p.Name != "amd" {
		return fmt.Errorf("Unknown provider")
	}
	if p.Name == "fireworks" && p.BaseURL != FireworksURL {
		return fmt.Errorf("Fireworks must use its official API endpoint")
	}
	if err := validateURL(p.BaseURL, false); err != nil {
		return err
	}
	if strings.TrimSpace(p.Header) == "" || strings.ContainsAny(p.Header+p.Prefix, "\r\n") {
		return fmt.Errorf("Enter a valid authentication header and prefix")
	}
	data := map[string]any{"backend": "cloud", "provider": p.Name, "base_url": p.BaseURL, "auth_header_name": p.Header, "auth_header_prefix": p.Prefix, "wire_format": "openai"}
	if err := c.request(ctx, "POST", "/install", data, nil); err != nil {
		return err
	}
	if key != "" {
		return c.request(ctx, "POST", "/cloud/auth", map[string]string{"provider": p.Name, "api_key": key}, nil)
	}
	return nil
}
func (c *Client) Clear(ctx context.Context, provider string) error {
	if provider != "fireworks" && provider != "amd" {
		return fmt.Errorf("Unknown provider")
	}
	return c.request(ctx, "DELETE", "/cloud/auth/"+provider, nil, nil)
}
