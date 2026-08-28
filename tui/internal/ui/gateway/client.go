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

// DefaultBaseURL is AMD's gateway. `llm.amd.com` is the SSO-gated portal, not
// the API; the OpenAI-compatible surface is the Unified API on a separate host.
// Verified live: <base>/models lists 76 models, <base>/chat/completions works.
const DefaultBaseURL = "https://llm-api.amd.com/Unified/v1"

// The gateway is Azure API Management, which authenticates on its own
// subscription-key header, not a bearer token. Verified: this header alone
// returns 200; `Authorization: Bearer` alone returns 401 "missing subscription
// key". Lemonade can carry exactly one auth header, so this is the one.
const DefaultAuthHeaderName = "Ocp-Apim-Subscription-Key"
const DefaultAuthHeaderPrefix = ""

// cloudRecipe is what Lemonade stamps on a model it proxies to a gateway.
const cloudRecipe = "cloud"

// preferredModelHints orders the picker, best first. The first match is what
// the screen selects automatically when nothing has been chosen yet.
//
// Gemma-4-31B leads deliberately: it is currently the ONLY gateway model that
// streams. The others return zero tokens on a streaming request while
// non-streaming works, and the agent path streams by default — so any other
// default hands a new user an agent that produces nothing. It is also on-prem,
// so it carries no per-token cost.
//
// Matched lowercase: the gateway mixes casing across its catalogue.
var preferredModelHints = []string{
	"gemma-4-31b",
	"claude-opus-5",
	"claude-sonnet-5",
}

// preferenceRank is the position in preferredModelHints; unlisted models sort
// last. Explicit ranking rather than alphabetical, which put Claude-Opus-5
// ahead of Gemma-4-31B and so made the one model that cannot stream the
// default.
func preferenceRank(id string) int {
	lowered := strings.ToLower(id)
	for i, hint := range preferredModelHints {
		if strings.Contains(lowered, hint) {
			return i
		}
	}
	return len(preferredModelHints)
}

// Model is a gateway model Lemonade has discovered.
type Model struct {
	ID      string
	Labels  []string
	CtxSize int
}

// Recommended reports whether this model should be surfaced first.
func (m Model) Recommended() bool {
	return preferenceRank(m.ID) < len(preferredModelHints)
}

// Status is the gateway provider's registration and auth state.
type Status struct {
	Installed        bool
	BaseURL          string
	EnvVarSet        bool
	RuntimeKeySet    bool
	ModelsDiscovered int
	// Lemonade's own per-provider warnings (insecure http, discovery
	// failures). Python surfaces these; dropping them here meant a TUI user
	// never saw why a provider was misbehaving.
	Warnings []string
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
		// No start command is named here on purpose: it differs per machine
		// and `lemonade-server serve` no longer exists on a modern install.
		// /setup resolves the right one against this host.
		return nil, fmt.Errorf(
			"Lemonade Server is not reachable on port 13305 or 8000.\n" +
				"Run /setup to start it, or set LEMONADE_BASE_URL to a server " +
				"that is already running")
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
//
// The probe carries LEMONADE_API_KEY. An embedded Lemonade is always launched
// with a key — that is how it locks out other apps — so a probe without one
// gets 401 and the whole gateway screen reports "Lemonade is not reachable"
// against a server that is running fine.
//
// A 401 still counts as found: the server is there, and every later request
// carries the same key, so a genuinely wrong key surfaces on that call with
// Lemonade's own message rather than as a bogus "not reachable".
func DetectLemonadeURL() string {
	probe := &http.Client{Timeout: 2 * time.Second}
	key := strings.TrimSpace(os.Getenv("LEMONADE_API_KEY"))

	reachable := func(base string) bool {
		req, err := http.NewRequest(http.MethodGet, base+"/models", nil)
		if err != nil {
			return false
		}
		if key != "" {
			req.Header.Set("Authorization", "Bearer "+key)
		}
		resp, err := probe.Do(req)
		if err != nil {
			return false
		}
		resp.Body.Close()
		return resp.StatusCode == http.StatusOK ||
			resp.StatusCode == http.StatusUnauthorized
	}

	if env := strings.TrimSpace(os.Getenv("LEMONADE_BASE_URL")); env != "" {
		base := strings.TrimRight(env, "/")
		if !strings.HasSuffix(base, "/api/v1") {
			base += "/api/v1"
		}
		if reachable(base) {
			return base
		}
		return ""
	}
	for _, port := range []string{"13305", "8000"} {
		base := "http://localhost:" + port + "/api/v1"
		if reachable(base) {
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
// wrong URL fails here with the real status rather than surfacing later as an
// empty model list.
//
// Redirects are deliberately not followed. AMD's gateway answers an
// unauthenticated request with `302 -> /login`; following that lands on an
// Okta HTML page — a 200 that is neither JSON nor a model list, which would
// report a correct URL as "not an OpenAI-compatible endpoint" and block
// registration. A redirect and a 401 mean the same thing here: the route is
// real and wants credentials, which is the normal state before the token step.
func (c *Client) Probe(baseURL string) (int, error) {
	url := strings.TrimRight(baseURL, "/") + "/models"
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return 0, fmt.Errorf("%q is not a usable URL: %w", baseURL, err)
	}
	token := strings.TrimSpace(os.Getenv(APIKeyEnv))
	if token == "" {
		token = strings.TrimSpace(os.Getenv("GAIA_GATEWAY_TOKEN"))
	}
	if token != "" {
		// Lemonade refuses to hold a token for an http:// endpoint without an
		// explicit opt-in, and the TUI has no way to give one. Sending it here
		// anyway would put the credential on the wire in the clear, on the one
		// request that runs before any of those checks.
		if strings.HasPrefix(strings.ToLower(url), "http://") {
			return 0, fmt.Errorf(
				"refusing to send your gateway token to %s over plaintext HTTP, "+
					"where anyone on the network path can read it. Use an https:// URL", url)
		}
		req.Header.Set(DefaultAuthHeaderName, DefaultAuthHeaderPrefix+token)
	}

	probe := &http.Client{
		Timeout: probeTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	resp, err := probe.Do(req)
	if err != nil {
		return 0, fmt.Errorf(
			"could not reach the gateway at %s: %w\n"+
				"Check the URL, and that you are on a network that can see it", url, err)
	}
	defer resp.Body.Close()

	switch {
	case resp.StatusCode >= 300 && resp.StatusCode < 400,
		resp.StatusCode == http.StatusUnauthorized,
		resp.StatusCode == http.StatusForbidden:
		// The endpoint is real and wants credentials — continue to the token step.
		return 0, nil
	case resp.StatusCode >= 400:
		return 0, fmt.Errorf(
			"the gateway at %s returned HTTP %d.\n"+
				"Check the base URL includes the API path "+
				"(e.g. .../v1 — AMD's gateway serves /v1, not /api/v1)",
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
	body := map[string]any{
		"backend":  "cloud",
		"provider": Provider,
		"base_url": strings.TrimRight(baseURL, "/"),
		// GAIA's agents speak OpenAI chat completions end to end.
		"wire_format": "openai",
		// The header the gateway actually checks; a bearer token 401s.
		"auth_header_name":   DefaultAuthHeaderName,
		"auth_header_prefix": DefaultAuthHeaderPrefix,
	}
	// Lemonade refuses to hold a token for a plaintext endpoint without this.
	// Registration succeeded without it and `auth` then failed 400, leaving a
	// provider that could never be given a token.
	if isInsecure(baseURL) {
		body["allow_insecure_http"] = true
	}
	err := c.do(http.MethodPost, "install", body, &result)
	return result, err
}

// isInsecure reports whether the gateway is served over plaintext http.
func isInsecure(baseURL string) bool {
	return strings.HasPrefix(strings.ToLower(strings.TrimSpace(baseURL)), "http://")
}

// SetToken hands a token to Lemonade for this session. Lemonade holds it in
// process memory and never writes it to disk.
func (c *Client) SetToken(token, baseURL string) (installResult, error) {
	var result installResult
	body := map[string]any{
		"provider": Provider,
		"api_key":  strings.TrimSpace(token),
	}
	if isInsecure(baseURL) {
		body["allow_insecure_http"] = true
	}
	err := c.do(http.MethodPost, "cloud/auth", body, &result)
	return result, err
}

// Uninstall removes the provider from Lemonade.
func (c *Client) Uninstall() error {
	return c.do(http.MethodPost, "uninstall", map[string]any{
		"backend":  "cloud",
		"provider": Provider,
	}, nil)
}

// ClearToken drops the session token Lemonade is holding.
func (c *Client) ClearToken() error {
	return c.do(http.MethodDelete, "cloud/auth/"+Provider, nil, nil)
}

// Status reads the gateway provider's registration and auth state.
func (c *Client) Status() (Status, error) {
	var info struct {
		Cloud struct {
			Providers []struct {
				Name             string   `json:"name"`
				BaseURL          string   `json:"base_url"`
				EnvVarSet        bool     `json:"env_var_set"`
				RuntimeKeySet    bool     `json:"runtime_key_set"`
				ModelsDiscovered int      `json:"models_discovered"`
				Warnings         []string `json:"warnings"`
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
			Warnings:         p.Warnings,
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
		ri, rj := preferenceRank(models[i].ID), preferenceRank(models[j].ID)
		if ri != rj {
			return ri < rj
		}
		return strings.ToLower(models[i].ID) < strings.ToLower(models[j].ID)
	})
	return models, nil
}
