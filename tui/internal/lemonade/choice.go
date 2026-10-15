// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

// Which Lemonade Server GAIA uses: its own private one, or one the user runs.
//
// The choice lives in config.json, written by Python (gaia.llm.lemonade_choice)
// or by the TUI's first-run offer:
//
//   - lemonade_mode: "gaia", "external", or absent (nobody chose; behaves as gaia)
//   - lemonade_base_url: the user's server, canonical form ending in /api/v1
//   - lemonade_api_key_stored: its key is in the Python OS credential store,
//     which Go cannot read, so the daemon hands it over (GET /lemonade/target)
//
// LEMONADE_BASE_URL still wins. In external mode GAIA starts no Lemonade at all.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

const (
	ModeGAIA     = "gaia"
	ModeExternal = "external"
)

// MinExternalVersion is the oldest Lemonade GAIA adopts as the user's server.
// Must equal INIT_PROFILES["gaia"]["min_lemonade_version"] in
// src/gaia/installer/init_command.py (a drift test pins the two).
const MinExternalVersion = "10.9.0"

// SwitchBackHint is how a user goes back to GAIA's own server.
const SwitchBackHint = "run `gaia config set lemonade_mode gaia` to use GAIA's own instead"

// Choice is the saved Lemonade setting. A zero Mode means nobody chose yet.
type Choice struct {
	Mode      string
	BaseURL   string
	KeyStored bool
	Path      string
}

// ConfigPathFrom mirrors gaia.config: GAIA_CONFIG_FILE, then GAIA_CONFIG_DIR,
// then GAIA_HOME, then ~/.gaia. "" when no home directory resolves.
func ConfigPathFrom(getenv func(string) string, homeDir func() (string, error)) string {
	if file := strings.TrimSpace(getenv("GAIA_CONFIG_FILE")); file != "" {
		return file
	}
	for _, name := range []string{"GAIA_CONFIG_DIR", "GAIA_HOME"} {
		if dir := strings.TrimSpace(getenv(name)); dir != "" {
			return filepath.Join(dir, "config.json")
		}
	}
	home, err := homeDir()
	if err != nil || home == "" {
		return ""
	}
	return filepath.Join(home, ".gaia", "config.json")
}

// ReadChoice reads the saved setting. A missing file is not an error; an
// unreadable or invalid one is, because Python refuses it too.
func ReadChoice() (Choice, error) {
	path := ConfigPathFrom(os.Getenv, os.UserHomeDir)
	if path == "" {
		return Choice{}, nil
	}
	c := Choice{Path: path}
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return c, nil
	}
	if err != nil {
		return c, fmt.Errorf("GAIA cannot read its config at %s: %v. Fix its permissions, or delete it", path, err)
	}
	var doc struct {
		Mode      json.RawMessage `json:"lemonade_mode"`
		BaseURL   json.RawMessage `json:"lemonade_base_url"`
		KeyStored json.RawMessage `json:"lemonade_api_key_stored"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return c, fmt.Errorf("GAIA's config at %s is not valid JSON (%v). Fix it, or delete it", path, err)
	}
	if err := decodeOptional(doc.Mode, &c.Mode); err != nil || (c.Mode != "" && c.Mode != ModeGAIA && c.Mode != ModeExternal) {
		return c, fmt.Errorf("GAIA's config at %s: lemonade_mode must be \"gaia\" or \"external\". "+
			"Fix or delete the lemonade_mode key in %s", path, path)
	}
	if err := decodeOptional(doc.BaseURL, &c.BaseURL); err != nil {
		return c, fmt.Errorf("GAIA's config at %s: lemonade_base_url must be a URL string. "+
			"Fix it with `gaia lemonade connect <url>`, or delete the key", path)
	}
	if err := decodeOptional(doc.KeyStored, &c.KeyStored); err != nil {
		return c, fmt.Errorf("GAIA's config at %s: lemonade_api_key_stored must be true or false (unquoted). "+
			"Fix it by hand, or delete the key", path)
	}
	c.BaseURL = strings.TrimSpace(c.BaseURL)
	return c, nil
}

// decodeOptional leaves out untouched for an absent key or a JSON null.
func decodeOptional(raw json.RawMessage, out any) error {
	if len(raw) == 0 || string(raw) == "null" {
		return nil
	}
	return json.Unmarshal(raw, out)
}

// ExternalURL is the user's server when external mode is saved, else "".
func (c Choice) ExternalURL() (string, error) {
	if c.Mode != ModeExternal {
		return "", nil
	}
	if c.BaseURL == "" {
		return "", fmt.Errorf("lemonade_mode is 'external' but lemonade_base_url is not set. "+
			"Run `gaia lemonade connect <url>`, or %s", SwitchBackHint)
	}
	return NormalizeExternalURL(c.BaseURL)
}

// NormalizeExternalURL mirrors lemonade_choice.normalize_external_url: an
// http(s) URL with a host and nothing a key could leak through; a bare origin
// gains /api/v1.
func NormalizeExternalURL(raw string) (string, error) {
	value := strings.TrimSpace(raw)
	u, err := url.Parse(value)
	switch {
	case err != nil:
		return "", fmt.Errorf("%q is not a valid Lemonade URL: %v", value, err)
	case u.Scheme != "http" && u.Scheme != "https":
		return "", fmt.Errorf("%q is not an http:// or https:// address", value)
	case u.Hostname() == "":
		return "", fmt.Errorf("%q has no host name", value)
	case u.User != nil:
		return "", fmt.Errorf("the Lemonade URL carries credentials; give the API key separately")
	case u.RawQuery != "" || u.Fragment != "":
		return "", fmt.Errorf("%q has a query or fragment; give just the server address", value)
	}
	return normalizeBaseURL(value), nil
}

// envURL is LEMONADE_BASE_URL when it is the user's choice. Values exported
// from GAIA's own credentials file (GAIA_LEMONADE_EMBEDDED=1) are not.
func envURL() string {
	if ownCredentials() {
		return ""
	}
	return strings.TrimSpace(os.Getenv("LEMONADE_BASE_URL"))
}

func ownCredentials() bool {
	return strings.TrimSpace(os.Getenv("GAIA_LEMONADE_EMBEDDED")) == "1"
}

// SavedExternal reports whether the saved choice, not LEMONADE_BASE_URL,
// picks the user's own server, and which. active with a non-nil err means the
// choice is unusable or unreadable: start nothing, and say so.
func SavedExternal() (base string, active bool, err error) {
	if envURL() != "" {
		return "", false, nil
	}
	c, err := ReadChoice()
	if err != nil {
		return "", true, err
	}
	if c.Mode != ModeExternal {
		return "", false, nil
	}
	base, err = c.ExternalURL()
	return base, true, err
}

// ExternalModeActive reports whether GAIA must start no Lemonade of any kind,
// including when the saved choice cannot be read (ChoiceError says why).
func ExternalModeActive() bool {
	_, active, _ := SavedExternal()
	return active
}

// ChoiceError is why the saved choice is unusable, nil when it is fine or
// LEMONADE_BASE_URL overrides it. Callers that send requests check it first.
func ChoiceError() error {
	_, _, err := SavedExternal()
	return err
}

// savedExternalURL is the saved server, "" when there is none or it is
// unusable (SavedExternal reports why).
func savedExternalURL() string {
	base, active, err := SavedExternal()
	if !active || err != nil {
		return ""
	}
	return base
}

// --- the key for the user's server -----------------------------------------

// DaemonTarget is the daemon's GET /daemon/v1/lemonade/target answer.
type DaemonTarget struct {
	Mode         string  `json:"mode"`
	Source       string  `json:"source"`
	BaseURL      *string `json:"base_url"`
	SavedBaseURL *string `json:"saved_base_url"`
	APIKeyStored bool    `json:"api_key_stored"`
	APIKey       *string `json:"api_key"`
}

const (
	lemonadeTargetPath      = daemon.APIPrefix + "/lemonade/target"
	lemonadeReconfigurePath = daemon.APIPrefix + "/lemonade/reconfigure"
)

// fetchDaemonTarget asks the daemon (host API 1.4+) which server GAIA uses,
// with the key saved for the user's own. A var so tests never reach a daemon.
var fetchDaemonTarget = func(ctx context.Context) (DaemonTarget, error) {
	if testing.Testing() {
		return DaemonTarget{}, errors.New("a test reached the real GAIA daemon; stub it with lemonade.StubDaemonTargetForTest")
	}
	body, err := daemon.CallContext(ctx, http.MethodGet, lemonadeTargetPath, nil, true,
		"read the API key saved for your Lemonade Server",
		"Run `gaia daemon restart` so it matches this GAIA")
	if err != nil {
		return DaemonTarget{}, err
	}
	var t DaemonTarget
	if err := json.Unmarshal(body, &t); err != nil {
		return DaemonTarget{}, fmt.Errorf("the GAIA daemon answered %s with something unreadable: %v", lemonadeTargetPath, err)
	}
	return t, nil
}

// StubDaemonTargetForTest replaces the daemon call and clears the key cache.
func StubDaemonTargetForTest(fn func(context.Context) (DaemonTarget, error)) (restore func()) {
	old := fetchDaemonTarget
	fetchDaemonTarget = fn
	forgetExternalKey()
	return func() {
		fetchDaemonTarget = old
		forgetExternalKey()
	}
}

// externalKey caches the daemon-fetched key, stamped with config.json's
// mtime and size: `gaia lemonade connect` rewrites the file, which drops it.
var externalKey struct {
	mu    sync.Mutex
	base  string
	stamp string
	key   string
}

func forgetExternalKey() {
	externalKey.mu.Lock()
	externalKey.base, externalKey.stamp, externalKey.key = "", "", ""
	externalKey.mu.Unlock()
}

func configStamp(path string) string {
	info, err := os.Stat(path)
	if err != nil {
		return ""
	}
	return fmt.Sprintf("%d/%d", info.ModTime().UnixNano(), info.Size())
}

// savedExternalKey is the stored key when target is the saved external
// server and a key was saved for it. ok=false means no saved key applies.
func savedExternalKey(ctx context.Context, target string) (key string, ok bool, err error) {
	c, err := ReadChoice()
	if err != nil || c.Mode != ModeExternal || !c.KeyStored {
		return "", false, nil
	}
	saved, err := c.ExternalURL()
	if err != nil {
		return "", false, nil
	}
	if norm, nerr := NormalizeExternalURL(target); nerr != nil || !strings.EqualFold(norm, saved) {
		return "", false, nil
	}

	stamp := configStamp(c.Path)
	externalKey.mu.Lock()
	defer externalKey.mu.Unlock()
	if externalKey.base == saved && externalKey.key != "" && stamp != "" && externalKey.stamp == stamp {
		return externalKey.key, true, nil
	}
	t, err := fetchDaemonTarget(ctx)
	if err != nil {
		return "", false, fmt.Errorf("GAIA saved an API key for your Lemonade Server at %s but could not get it "+
			"from the GAIA daemon (%v). Run `gaia daemon status`, or re-enter it with `gaia lemonade connect %s`",
			saved, err, saved)
	}
	if t.APIKey == nil || strings.TrimSpace(*t.APIKey) == "" {
		using := "GAIA's own server"
		if t.BaseURL != nil && *t.BaseURL != "" {
			using = *t.BaseURL + " (from " + t.Source + ")"
		}
		return "", false, fmt.Errorf("GAIA saved an API key for your Lemonade Server at %s, but the GAIA daemon "+
			"did not hand it over: it is using %s. Run `gaia daemon restart` so it reads the saved setting",
			saved, using)
	}
	externalKey.base, externalKey.stamp, externalKey.key = saved, stamp, strings.TrimSpace(*t.APIKey)
	return externalKey.key, true, nil
}

// --- telling a running daemon ------------------------------------------------

// reconfigureDaemon asks a RUNNING daemon to follow the saved choice; it
// never starts one. A var so tests never reach a daemon.
var reconfigureDaemon = func(ctx context.Context) ([]byte, error) {
	if testing.Testing() {
		return nil, errors.New("a test reached the real GAIA daemon; stub it with lemonade.StubReconfigureForTest")
	}
	return daemon.CallContext(ctx, http.MethodPost, lemonadeReconfigurePath, nil, false,
		"switch which Lemonade Server GAIA uses", "")
}

// StubReconfigureForTest replaces the daemon call.
func StubReconfigureForTest(fn func(context.Context) ([]byte, error)) (restore func()) {
	old := reconfigureDaemon
	reconfigureDaemon = fn
	return func() { reconfigureDaemon = old }
}

// ApplyChoiceToDaemon makes a running daemon follow a just-saved choice. No
// daemon running is fine: the next one reads the setting.
func ApplyChoiceToDaemon(ctx context.Context, mode, base string) error {
	ctx, cancel := context.WithTimeout(ctx, 150*time.Second)
	defer cancel()
	body, err := reconfigureDaemon(ctx)
	var notRunning *daemon.NotRunningError
	var stale *daemon.StaleError
	var missing *daemon.RouteMissingError
	switch {
	case err == nil:
	case errors.As(err, &notRunning):
		return nil
	case errors.As(err, &stale) && stale.Kind == daemon.StalePIDDead:
		return nil
	case errors.As(err, &missing):
		return fmt.Errorf("the running GAIA daemon is older than this GAIA and cannot switch Lemonade " +
			"Servers while it runs. Run `gaia daemon restart` so it uses the new setting")
	default:
		return fmt.Errorf("the GAIA daemon could not switch Lemonade Server (%v). "+
			"Run `gaia daemon restart` so it uses the new setting", err)
	}
	var got DaemonTarget
	if err := json.Unmarshal(body, &got); err != nil {
		return fmt.Errorf("the GAIA daemon answered %s with something unreadable (%v). "+
			"Run `gaia daemon restart` so it uses the new setting", lemonadeReconfigurePath, err)
	}
	gotBase := ""
	if got.BaseURL != nil {
		gotBase = *got.BaseURL
	}
	if got.Mode != mode || (mode == ModeExternal && !strings.EqualFold(normalizeBaseURL(gotBase), base)) {
		where := "GAIA's own server"
		if gotBase != "" {
			where = gotBase
		}
		return fmt.Errorf("the GAIA daemon still uses %s (it was started with a different setting). "+
			"Run `gaia daemon restart` so it uses the new one", where)
	}
	return nil
}

// --- what answers at a URL ---------------------------------------------------

// Probe outcomes, as lemonade_choice.probe names them.
const (
	ProbeOK          = "ok"
	ProbeNeedsKey    = "needs_key"
	ProbeBadKey      = "bad_key"
	ProbeTooOld      = "too_old"
	ProbeNotLemonade = "not_lemonade"
	ProbeUnreachable = "unreachable"
)

// ProbeResult is what answered at URL, and whether GAIA can use it.
type ProbeResult struct {
	URL     string
	Status  string
	Version string
	Minimum string
	Detail  string
}

// Message is one plain sentence and the next step, for the saved server.
func (r ProbeResult) Message() string {
	switch r.Status {
	case ProbeOK:
		return fmt.Sprintf("Lemonade Server v%s is running at %s.", r.Version, r.URL)
	case ProbeNeedsKey:
		return fmt.Sprintf("The Lemonade Server at %s needs an API key. Run `gaia lemonade connect %s` to save its key.",
			r.URL, r.URL)
	case ProbeBadKey:
		return fmt.Sprintf("The Lemonade Server at %s rejected the saved API key. Run `gaia lemonade connect %s` to enter it again.",
			r.URL, r.URL)
	case ProbeTooOld:
		return fmt.Sprintf("The Lemonade Server at %s is v%s; GAIA needs v%s or newer. Upgrade it from "+
			"https://lemonade-server.ai, or %s.", r.URL, r.Version, r.Minimum, SwitchBackHint)
	case ProbeNotLemonade:
		return fmt.Sprintf("Something answers at %s, but it is not a Lemonade Server (%s). Check the address, or %s.",
			r.URL, r.Detail, SwitchBackHint)
	}
	return fmt.Sprintf("Your Lemonade Server at %s is not answering. Start it, or run "+
		"`gaia config set lemonade_mode gaia` to use GAIA's own.", r.URL)
}

// Probe asks <base>/health what is there. Only a JSON body with status "ok"
// and a version counts as Lemonade; HTML or any other JSON does not.
func Probe(ctx context.Context, base, key string) ProbeResult {
	return probe(ctx, base, key, 3*time.Second)
}

// DetectProbe is Probe for scanning ports: a closed one is given up on fast
// (Windows otherwise retries a refused connect for about two seconds).
func DetectProbe(ctx context.Context, base string) ProbeResult {
	return probe(ctx, base, "", 500*time.Millisecond)
}

func probe(ctx context.Context, base, key string, connect time.Duration) ProbeResult {
	r := ProbeResult{URL: normalizeBaseURL(strings.TrimSpace(base)), Minimum: MinExternalVersion}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, r.URL+"/health", nil)
	if err != nil {
		r.Status, r.Detail = ProbeUnreachable, err.Error()
		return r
	}
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	httpc := &http.Client{
		Transport:     &http.Transport{DialContext: (&net.Dialer{Timeout: connect}).DialContext},
		Timeout:       10 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	resp, err := httpc.Do(req)
	if err != nil {
		r.Status, r.Detail = ProbeUnreachable, firstLine(err.Error())
		return r
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden:
		r.Status = ProbeNeedsKey
		if key != "" {
			r.Status = ProbeBadKey
		}
		return r
	case resp.StatusCode != http.StatusOK:
		r.Status, r.Detail = ProbeNotLemonade, fmt.Sprintf("HTTP %d", resp.StatusCode)
		return r
	}
	var body map[string]any
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&body); err != nil {
		kind := strings.TrimSpace(strings.Split(resp.Header.Get("Content-Type"), ";")[0])
		if kind == "" {
			kind = "not JSON"
		}
		r.Status, r.Detail = ProbeNotLemonade, kind
		return r
	}
	version, _ := body["version"].(string)
	if body["status"] != "ok" || !versionAtLeast(version, "0") {
		r.Status, r.Detail = ProbeNotLemonade, "no Lemonade health answer"
		return r
	}
	r.Version = strings.TrimPrefix(strings.TrimSpace(version), "v")
	if !versionAtLeast(version, MinExternalVersion) {
		r.Status = ProbeTooOld
		return r
	}
	r.Status = ProbeOK
	return r
}

func firstLine(s string) string {
	s, _, _ = strings.Cut(s, "\n")
	if len(s) > 160 {
		s = s[:160]
	}
	return s
}
