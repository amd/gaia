// Package gateway is the TUI screen for connecting GAIA to the AMD LLM
// gateway through Lemonade's cloud offload (Lemonade >= 11.8).
//
// The screen talks to Lemonade directly rather than through the daemon: the
// API token is the whole point of the screen, and a direct call is one fewer
// process it passes through. Lemonade holds the token in memory only and never
// writes it to disk — see docs/guides/llm-gateway.mdx.
package gateway

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"
)

// Provider is the name the gateway is registered under. Lemonade namespaces
// discovered models as "<provider>.<id>", so it is also the prefix of every
// gateway model id the TUI sees. Keep in lock-step with
// gaia.llm.gateway.GATEWAY_PROVIDER.
const Provider = "amd"

// APIKeyEnv is the variable Lemonade resolves for this provider. Set it in
// *Lemonade's* environment for a token that survives a restart.
const APIKeyEnv = "LEMONADE_AMD_API_KEY"

// DefaultBaseURL is a starting suggestion, not a silent default: the screen
// makes it editable and Probe reports the real HTTP status when it is wrong.
const DefaultBaseURL = "https://llm.amd.com/api/v1"

// cloudRecipe is what Lemonade stamps on a model it proxies to a gateway.
const cloudRecipe = "cloud"

// recommendedHints float a discovered model to the top of the picker. Hints
// for ordering only — the real ids come from the gateway, which names things
// its own way (e.g. "Claude-Opus-5").
var recommendedHints = []string{
	"gemma-4-31b",
	"gemma4-31b",
	"claude-opus",
	"claude-sonnet",
}

// Model is a gateway model Lemonade has discovered.
type Model struct {
	ID      string
	Labels  []string
	CtxSize int
}

// Recommended reports whether this model should be surfaced first.
func (m Model) Recommended() bool {
	lowered := strings.ToLower(m.ID)
	for _, hint := range recommendedHints {
		if strings.Contains(lowered, hint) {
			return true
		}
	}
	return false
}

// Status is the gateway provider's registration and auth state.
type Status struct {
	Installed        bool
	BaseURL          string
	EnvVarSet        bool
	RuntimeKeySet    bool
	ModelsDiscovered int
}

// Authenticated reports whether Lemonade can resolve a token for the gateway.
func (s Status) Authenticated() bool { return s.EnvVarSet || s.RuntimeKeySet }

// Client speaks Lemonade's cloud-provider API.
type Client struct {
	baseURL string
	http    *http.Client
}

// NewClient resolves Lemonade's address the same way the agent transport does.
// It returns an error rather than a client pointed at nothing, so a missing
// server is reported once, here, instead of as a confusing failure later.
func NewClient() (*Client, error) {
	base := DetectLemonadeURL()
	if base == "" {
		return nil, fmt.Errorf(
			"Lemonade Server is not reachable on port 13305 or 8000.\n" +
				"Start it with `lemonade-server serve`, or set LEMONADE_BASE_URL " +
				"to a running server")
	}
	return &Client{baseURL: base, http: &http.Client{Timeout: 60 * time.Second}}, nil
}

// NewClientAt builds a client against an explicit Lemonade base URL. Tests use
// it to point at a stub server.
func NewClientAt(baseURL string) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: 60 * time.Second},
	}
}

// BaseURL is the Lemonade address this client talks to.
func (c *Client) BaseURL() string { return c.baseURL }

// DetectLemonadeURL probes the ports Lemonade listens on and returns the first
// reachable API base, honouring LEMONADE_BASE_URL when it is set.
func DetectLemonadeURL() string {
	probe := &http.Client{Timeout: 2 * time.Second}
	if env := strings.TrimSpace(os.Getenv("LEMONADE_BASE_URL")); env != "" {
		base := strings.TrimRight(env, "/")
		if !strings.HasSuffix(base, "/api/v1") {
			base += "/api/v1"
		}
		if resp, err := probe.Get(base + "/models"); err == nil {
			resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return base
			}
		}
		return ""
	}
	for _, port := range []string{"13305", "8000"} {
		base := "http://localhost:" + port + "/api/v1"
		resp, err := probe.Get(base + "/models")
		if err != nil {
			continue
		}
		resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			return base
		}
	}
	return ""
}

// do issues a request to Lemonade and decodes the JSON body. The payload is
// never logged — it may carry the token.
func (c *Client) do(method, path string, payload any, out any) error {
	var body *bytes.Reader
	if payload != nil {
		encoded, err := json.Marshal(payload)
		if err != nil {
			return fmt.Errorf("could not encode request: %w", err)
		}
		body = bytes.NewReader(encoded)
	} else {
		body = bytes.NewReader(nil)
	}

	url := c.baseURL + "/" + strings.TrimLeft(path, "/")
	req, err := http.NewRequest(method, url, body)
	if err != nil {
		return fmt.Errorf("could not build request for %s: %w", url, err)
	}
	req.Header.Set("Content-Type", "application/json")
	if key := strings.TrimSpace(os.Getenv("LEMONADE_API_KEY")); key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("Lemonade at %s is not reachable: %w", c.baseURL, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return c.httpError(resp, method, url)
	}
	if out == nil {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("Lemonade returned an unreadable response from %s: %w", url, err)
	}
	return nil
}

// errorEnvelope is Lemonade's error body shape.
type errorEnvelope struct {
	Error struct {
		Message string `json:"message"`
		Type    string `json:"type"`
	} `json:"error"`
}

func (c *Client) httpError(resp *http.Response, method, url string) error {
	var env errorEnvelope
	_ = json.NewDecoder(resp.Body).Decode(&env)

	if resp.StatusCode == http.StatusConflict && env.Error.Type == "auth_conflict" {
		return fmt.Errorf(
			"%s is already set in Lemonade's environment, so Lemonade will not "+
				"accept a different token at runtime.\nEither keep using that key, "+
				"or unset %s and restart Lemonade.", APIKeyEnv, APIKeyEnv)
	}
	if resp.StatusCode == http.StatusNotFound && strings.Contains(url, "cloud") {
		return fmt.Errorf(
			"Lemonade at %s has no cloud-provider API (needs >= 11.8.0).\n"+
				"Upgrade with `gaia init` and try again.", c.baseURL)
	}
	if env.Error.Message != "" {
		return fmt.Errorf("%s failed (HTTP %d): %s", method, resp.StatusCode, env.Error.Message)
	}
	return fmt.Errorf("%s %s failed with HTTP %d", method, url, resp.StatusCode)
}

// probeTimeout bounds how long the screen freezes on a mistyped host. A DNS
// failure to a non-existent domain can otherwise take ~30s, which reads as a
// hang rather than an answer.
const probeTimeout = 12 * time.Second

// Probe checks the gateway's own /models endpoint before registration, so a
// wrong URL or a dead token fails here with the real status rather than
// surfacing later as an empty model list.
func (c *Client) Probe(baseURL string) (int, error) {
	url := strings.TrimRight(baseURL, "/") + "/models"
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return 0, fmt.Errorf("%q is not a usable URL: %w", baseURL, err)
	}
	if token := strings.TrimSpace(os.Getenv(APIKeyEnv)); token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}

	probe := &http.Client{Timeout: probeTimeout}
	resp, err := probe.Do(req)
	if err != nil {
		return 0, fmt.Errorf(
			"could not reach the gateway at %s: %w\n"+
				"Check the URL, and that you are on a network that can see it", url, err)
	}
	defer resp.Body.Close()

	switch {
	case resp.StatusCode == http.StatusUnauthorized, resp.StatusCode == http.StatusForbidden:
		// Expected before a token is supplied — the endpoint is real, so let
		// the flow continue to the token step.
		return 0, nil
	case resp.StatusCode >= 400:
		return 0, fmt.Errorf(
			"the gateway at %s returned HTTP %d.\n"+
				"Check the base URL includes the API path (e.g. .../api/v1)",
			url, resp.StatusCode)
	}

	var envelope struct {
		Data []json.RawMessage `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&envelope); err != nil {
		return 0, fmt.Errorf(
			"%s did not return JSON, so it is not an OpenAI-compatible models "+
				"endpoint: %w", url, err)
	}
	return len(envelope.Data), nil
}

// installResult is the useful half of Lemonade's install response.
type installResult struct {
	ModelsDiscovered int `json:"models_discovered"`
	AuthState        struct {
		EnvVarSet     bool `json:"env_var_set"`
		RuntimeKeySet bool `json:"runtime_key_set"`
	} `json:"auth_state"`
}

// Install registers the gateway with Lemonade as a cloud provider.
func (c *Client) Install(baseURL string) (installResult, error) {
	var result installResult
	err := c.do(http.MethodPost, "install", map[string]any{
		"backend":  "cloud",
		"provider": Provider,
		"base_url": strings.TrimRight(baseURL, "/"),
		// GAIA's agents speak OpenAI chat completions end to end.
		"wire_format": "openai",
	}, &result)
	return result, err
}

// SetToken hands a token to Lemonade for this session. Lemonade holds it in
// process memory and never writes it to disk.
func (c *Client) SetToken(token string) (installResult, error) {
	var result installResult
	err := c.do(http.MethodPost, "cloud/auth", map[string]any{
		"provider": Provider,
		"api_key":  strings.TrimSpace(token),
	}, &result)
	return result, err
}

// Status reads the gateway provider's registration and auth state.
func (c *Client) Status() (Status, error) {
	var info struct {
		Cloud struct {
			Providers []struct {
				Name             string `json:"name"`
				BaseURL          string `json:"base_url"`
				EnvVarSet        bool   `json:"env_var_set"`
				RuntimeKeySet    bool   `json:"runtime_key_set"`
				ModelsDiscovered int    `json:"models_discovered"`
			} `json:"providers"`
		} `json:"cloud"`
	}
	if err := c.do(http.MethodGet, "system-info", nil, &info); err != nil {
		return Status{}, err
	}
	for _, p := range info.Cloud.Providers {
		if p.Name != Provider {
			continue
		}
		return Status{
			Installed:        true,
			BaseURL:          p.BaseURL,
			EnvVarSet:        p.EnvVarSet,
			RuntimeKeySet:    p.RuntimeKeySet,
			ModelsDiscovered: p.ModelsDiscovered,
		}, nil
	}
	return Status{Installed: false}, nil
}

// ListModels returns the gateway models Lemonade has discovered, recommended
// ones first.
func (c *Client) ListModels() ([]Model, error) {
	var payload struct {
		Data []struct {
			ID            string   `json:"id"`
			Recipe        string   `json:"recipe"`
			Labels        []string `json:"labels"`
			ContextLength int      `json:"context_length"`
		} `json:"data"`
	}
	if err := c.do(http.MethodGet, "models", nil, &payload); err != nil {
		return nil, err
	}

	prefix := Provider + "."
	var models []Model
	for _, entry := range payload.Data {
		if entry.Recipe != cloudRecipe || !strings.HasPrefix(entry.ID, prefix) {
			continue
		}
		models = append(models, Model{
			ID:      entry.ID,
			Labels:  entry.Labels,
			CtxSize: entry.ContextLength,
		})
	}
	sort.SliceStable(models, func(i, j int) bool {
		if models[i].Recommended() != models[j].Recommended() {
			return models[i].Recommended()
		}
		return strings.ToLower(models[i].ID) < strings.ToLower(models[j].ID)
	})
	return models, nil
}
