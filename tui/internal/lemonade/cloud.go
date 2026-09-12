// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package lemonade configures cloud routing without passing credentials to agents.
package lemonade

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const FireworksURL = "https://api.fireworks.ai/inference/v1"
const FireworksModel = "fireworks.gemma-4-31b-it"

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
		return fmt.Errorf("Lemonade did not respond. Start it, check its address, and retry")
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
