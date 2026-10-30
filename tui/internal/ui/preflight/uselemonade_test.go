// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// starts counts every way the gate could start a Lemonade, so a test can
// prove external mode starts none.
type starts struct{ auto, detect, probe int }

// externalSetup isolates config and runtime, saves doc as config.json, and
// counts start attempts.
func externalSetup(t *testing.T, doc map[string]any) (*starts, string) {
	t.Helper()
	t.Setenv("GAIA_HOME", t.TempDir())
	t.Setenv(lemonadeBaseURLEnv, "")
	t.Setenv("GAIA_LEMONADE_EMBEDDED", "")
	t.Setenv("LEMONADE_API_KEY", "")
	path := filepath.Join(t.TempDir(), "config.json")
	t.Setenv(configFileEnv, path)
	if doc != nil {
		raw, _ := json.Marshal(doc)
		if err := os.WriteFile(path, raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	declinedRunningLemonade.Store(false)
	t.Cleanup(func() { declinedRunningLemonade.Store(false) })
	s := &starts{}
	restoreAuto, restoreDetect, restoreProbe := tryAutoStartLemonade, detectRunningLemonade, probeLemonade
	t.Cleanup(func() {
		tryAutoStartLemonade, detectRunningLemonade, probeLemonade = restoreAuto, restoreDetect, restoreProbe
	})
	tryAutoStartLemonade = func(context.Context) (bool, string, string) {
		s.auto++
		return true, "http://localhost:13305/api/v1", "stub start"
	}
	detectRunningLemonade = func(context.Context) (lemonade.ProbeResult, bool) {
		s.detect++
		return lemonade.ProbeResult{}, false
	}
	probeLemonade = func(context.Context) (string, bool, string) {
		s.probe++
		return "http://localhost:13305/api/v1", false, "stub: nothing answering"
	}
	t.Cleanup(lemonade.StubDaemonTargetForTest(func(context.Context) (lemonade.DaemonTarget, error) {
		return lemonade.DaemonTarget{}, fmt.Errorf("test: no daemon")
	}))
	return s, path
}

func healthServer(t *testing.T, status int, contentType, body string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if contentType != "" {
			w.Header().Set("Content-Type", contentType)
		}
		w.WriteHeader(status)
		fmt.Fprint(w, body)
	}))
	t.Cleanup(srv.Close)
	return srv
}

func externalDoc(url string) map[string]any {
	return map[string]any{"lemonade_mode": "external", "lemonade_base_url": url, "lemonade_api_key_stored": false}
}

// In external mode the row reports the user's server and starts nothing,
// whatever state that server is in.
func TestExternalModeReportsTheServerAndStartsNothing(t *testing.T) {
	closed := httptest.NewServer(http.NotFoundHandler())
	closedURL := closed.URL
	closed.Close()

	for _, tc := range []struct {
		name       string
		url        func(t *testing.T) string
		wantState  State
		wantIn     []string
		wantCmd    string
		claudeMode bool
	}{
		{name: "running", wantState: StateOK, wantIn: []string{"v10.10.0"},
			url: func(t *testing.T) string {
				return healthServer(t, 200, "application/json", `{"status":"ok","version":"10.10.0"}`).URL
			}},
		{name: "down", wantState: StateFailed,
			wantIn: []string{"is not answering", "Start it", "gaia config set lemonade_mode gaia"},
			url:    func(*testing.T) string { return closedURL }},
		{name: "down on a Claude session", wantState: StateUnknown, claudeMode: true,
			wantIn: []string{"is not answering"},
			url:    func(*testing.T) string { return closedURL }},
		{name: "too old", wantState: StateFailed,
			wantIn: []string{"v10.8.0", "v10.9.0", "https://lemonade-server.ai", "gaia config set lemonade_mode gaia"},
			url: func(t *testing.T) string {
				return healthServer(t, 200, "application/json", `{"status":"ok","version":"10.8.0"}`).URL
			}},
		{name: "needs a key", wantState: StateFailed, wantIn: []string{"needs an API key"},
			wantCmd: "gaia lemonade connect ",
			url:     func(t *testing.T) string { return healthServer(t, 401, "", "").URL }},
		{name: "not lemonade", wantState: StateFailed, wantIn: []string{"not a Lemonade Server"},
			url: func(t *testing.T) string { return healthServer(t, 200, "text/html", "<html></html>").URL }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			url := tc.url(t)
			s, _ := externalSetup(t, externalDoc(url))
			installEmbedded(t)

			row := localRunner{opts: LocalOptions{ClaudeMode: tc.claudeMode}}.checkLemonade(context.Background(), localCfg())

			if row.State != tc.wantState {
				t.Fatalf("state = %s, want %s: %+v", row.State.Word(), tc.wantState.Word(), row)
			}
			text := row.Line + " " + row.Detail + " " + row.Remedy.Action
			for _, want := range tc.wantIn {
				if !strings.Contains(text, want) {
					t.Errorf("row text lacks %q:\n%s", want, text)
				}
			}
			if tc.wantCmd != "" && !strings.HasPrefix(row.Remedy.Command, tc.wantCmd) {
				t.Errorf("command = %q, want %q…", row.Remedy.Command, tc.wantCmd)
			}
			if row.Fix != FixNone || row.DeclineFix != FixNone {
				t.Errorf("external mode offered a key that would act: f=%v g=%v", row.Fix, row.DeclineFix)
			}
			if row.State != StateOK && row.Disposition == 0 {
				t.Error("a non-OK row declared no disposition")
			}
			if *s != (starts{}) {
				t.Errorf("external mode tried to start or find a server: %+v", *s)
			}
		})
	}
}

// A key saved for the user's server must reach it; a key the daemon cannot
// hand over is the row's error, not a silent 401.
func TestExternalModeSendsTheSavedKey(t *testing.T) {
	var auth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth = r.Header.Get("Authorization")
		if auth != "Bearer saved-key" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		fmt.Fprint(w, `{"status":"ok","version":"10.10.0"}`)
	}))
	defer srv.Close()
	doc := externalDoc(srv.URL)
	doc["lemonade_api_key_stored"] = true
	externalSetup(t, doc)

	row := localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.State != StateFailed || !strings.Contains(row.Detail, "could not get it from the GAIA daemon") {
		t.Fatalf("an unfetchable key was not the row's error: %+v", row)
	}

	key := "saved-key"
	t.Cleanup(lemonade.StubDaemonTargetForTest(func(context.Context) (lemonade.DaemonTarget, error) {
		return lemonade.DaemonTarget{Mode: "external", Source: "config", APIKeyStored: true, APIKey: &key}, nil
	}))
	row = localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.State != StateOK || auth != "Bearer saved-key" {
		t.Fatalf("row %+v, Authorization %q", row, auth)
	}
}

// The daemon runner must not ask the daemon to start anything for the user's
// own server either.
func TestDaemonRunnerStartsNothingInExternalMode(t *testing.T) {
	externalSetup(t, externalDoc("http://localhost:8000"))
	f := newFake().with("GET /v1/email/init", 503, initUnreachable)

	rep := Check(context.Background(), f, EmailConfig())

	if f.called(http.MethodPost, "/daemon/v1/lemonade/start") {
		t.Fatal("external mode asked the daemon to start Lemonade")
	}
	row := rowByKey(t, rep, KeyLemonade)
	if row.State != StateFailed || !strings.Contains(row.Detail, "gaia config set lemonade_mode gaia") {
		t.Fatalf("row = %+v", row)
	}
}

func rowByKey(t *testing.T, rep Report, key string) Row {
	t.Helper()
	for _, r := range rep.Rows {
		if r.Key == key {
			return r
		}
	}
	t.Fatalf("no %s row", key)
	return Row{}
}

func TestDetectionOffersOnlyARealLemonade(t *testing.T) {
	html := healthServer(t, 200, "text/html", "<html>router admin</html>")
	otherJSON := healthServer(t, 200, "application/json", `{"status":"ok","uptime":3}`)
	old := healthServer(t, 200, "application/json", `{"status":"ok","version":"10.8.0"}`)
	keyed := healthServer(t, 401, "", "")
	good := healthServer(t, 200, "application/json", `{"status":"ok","version":"10.10.0"}`)
	installed := func() bool { return true }
	notInstalled := func() bool { return false }

	for _, tc := range []struct {
		name      string
		bases     []string
		installed func() bool
		want      string
	}{
		{name: "a web page is not offered", bases: []string{html.URL}, installed: installed},
		{name: "other JSON is skipped for the next port", bases: []string{otherJSON.URL, good.URL},
			installed: installed, want: good.URL + "/api/v1"},
		{name: "too old is not offered", bases: []string{old.URL}, installed: installed},
		{name: "a 401 counts where Lemonade is installed", bases: []string{keyed.URL},
			installed: installed, want: keyed.URL + "/api/v1"},
		{name: "a 401 proves nothing where it is not", bases: []string{keyed.URL}, installed: notInstalled},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r, ok := detectLemonadeAt(context.Background(), tc.bases, tc.installed)
			if tc.want == "" {
				if ok {
					t.Fatalf("offered %+v", r)
				}
				return
			}
			if !ok || r.URL != tc.want {
				t.Fatalf("got %+v ok=%v, want %s", r, ok, tc.want)
			}
		})
	}
}

func TestTheOfferAppearsOnlyBeforeAnyoneChose(t *testing.T) {
	found := lemonade.ProbeResult{URL: "http://localhost:13305/api/v1", Status: lemonade.ProbeOK, Version: "10.10.0"}
	for _, tc := range []struct {
		name     string
		doc      map[string]any
		env      string
		embedded bool
		offer    bool
	}{
		{name: "fresh machine", offer: true},
		{name: "chose GAIA's own", doc: map[string]any{"lemonade_mode": "gaia"}},
		{name: "LEMONADE_BASE_URL set", env: "http://localhost:13305"},
		{name: "GAIA's own installed", embedded: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s, _ := externalSetup(t, tc.doc)
			t.Setenv(lemonadeBaseURLEnv, tc.env)
			if tc.embedded {
				installEmbedded(t)
			}
			detectRunningLemonade = func(context.Context) (lemonade.ProbeResult, bool) {
				s.detect++
				return found, true
			}

			row := localRunner{}.checkLemonade(context.Background(), localCfg())

			if got := row.Fix == FixUseRunningLemonade; got != tc.offer {
				t.Fatalf("offered=%v, want %v: %+v", got, tc.offer, row)
			}
			if !tc.offer {
				return
			}
			if row.State != StateUnknown || row.DeclineFix != FixUseGAIALemonade {
				t.Fatalf("the offer does not hold for a choice: %+v", row)
			}
			for _, want := range []string{found.URL, "does not change your server's settings",
				"gaia config set lemonade_mode gaia"} {
				if !strings.Contains(row.Detail+" "+row.Remedy.Action, want) {
					t.Errorf("offer lacks %q", want)
				}
			}
			if s.auto != 0 || s.probe != 0 {
				t.Errorf("the offer started or probed GAIA's server first: %+v", *s)
			}
		})
	}

	// A key-protected server gets the connect command, not a one-key accept.
	externalSetup(t, nil)
	detectRunningLemonade = func(context.Context) (lemonade.ProbeResult, bool) {
		return lemonade.ProbeResult{URL: found.URL, Status: lemonade.ProbeNeedsKey}, true
	}
	row := localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.Fix != FixNone || row.Remedy.Command != "gaia lemonade connect "+found.URL {
		t.Fatalf("keyed offer: %+v", row)
	}
}

func TestAcceptingAndDecliningTheOfferSaveTheChoice(t *testing.T) {
	found := lemonade.ProbeResult{URL: "http://localhost:13305/api/v1", Status: lemonade.ProbeOK, Version: "10.10.0"}
	for _, tc := range []struct {
		name      string
		kind      FixKind
		want      map[string]any
		reconfig  int
		reconfErr error
		wantErrIn string
	}{
		{name: "accept", kind: FixUseRunningLemonade, reconfig: 1, want: map[string]any{
			"lemonade_mode": "external", "lemonade_base_url": found.URL, "lemonade_api_key_stored": false}},
		{name: "accept, old daemon", kind: FixUseRunningLemonade, reconfig: 1,
			reconfErr: fmt.Errorf("stub: route missing"), wantErrIn: "gaia daemon restart",
			want: map[string]any{"lemonade_mode": "external", "lemonade_base_url": found.URL}},
		// Declining saves nothing: GAIA's own is not installed yet.
		{name: "decline", kind: FixUseGAIALemonade, want: map[string]any{"lemonade_mode": nil}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, path := externalSetup(t, map[string]any{"default_model": "Gemma-4-E4B-it-GGUF", "full_access": true})
			detectRunningLemonade = func(context.Context) (lemonade.ProbeResult, bool) { return found, true }
			calls := 0
			t.Cleanup(lemonade.StubReconfigureForTest(func(context.Context) ([]byte, error) {
				calls++
				if tc.reconfErr != nil {
					return nil, tc.reconfErr
				}
				return []byte(`{"mode":"external","base_url":"` + found.URL + `","action":"none"}`), nil
			}))

			res := localRunner{}.Fix(context.Background(), localCfg(), tc.kind, nil)

			if tc.wantErrIn == "" && res.Err != nil {
				t.Fatalf("fix failed: %v", res.Err)
			}
			if tc.wantErrIn != "" && !strings.Contains(res.Diagnosis.String(), tc.wantErrIn) {
				t.Fatalf("diagnosis %q lacks %q", res.Diagnosis.String(), tc.wantErrIn)
			}
			if calls != tc.reconfig {
				t.Errorf("reconfigure called %d times, want %d", calls, tc.reconfig)
			}
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			var doc map[string]any
			if err := json.Unmarshal(raw, &doc); err != nil {
				t.Fatal(err)
			}
			for k, v := range tc.want {
				if doc[k] != v {
					t.Errorf("%s = %v, want %v", k, doc[k], v)
				}
			}
			if doc["default_model"] != "Gemma-4-E4B-it-GGUF" || doc["full_access"] != true {
				t.Errorf("other keys were not preserved: %v", doc)
			}
		})
	}
}

func TestAnUnreadableChoiceIsTheRowNotAStart(t *testing.T) {
	s, path := externalSetup(t, nil)
	if err := os.WriteFile(path, []byte(`{"lemonade_mode":"mine"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	row := localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.State != StateFailed || !strings.Contains(row.Detail, path) {
		t.Fatalf("row = %+v", row)
	}
	if *s != (starts{}) {
		t.Errorf("an unreadable choice tried to start or find a server: %+v", *s)
	}
}

// Answering the offer with g must not save "gaia" before GAIA's server exists,
// and the next check must not pass the user's server off as GAIA's.
func TestDecliningTheOfferNeverCountsTheUsersServerAsGAIAs(t *testing.T) {
	found := lemonade.ProbeResult{URL: "http://localhost:13305/api/v1", Status: lemonade.ProbeOK, Version: "10.10.0"}
	s, path := externalSetup(t, map[string]any{"default_model": "Gemma-4-E4B-it-GGUF"})
	detectRunningLemonade = func(context.Context) (lemonade.ProbeResult, bool) {
		s.detect++
		return found, true
	}
	// The user's server answers on the default port the whole time.
	probeLemonade = func(context.Context) (string, bool, string) {
		s.probe++
		return found.URL, true, "GET " + found.URL + "/models -> 200"
	}

	if row := (localRunner{}).checkLemonade(context.Background(), localCfg()); row.Fix != FixUseRunningLemonade {
		t.Fatalf("expected the offer first, got %+v", row)
	}
	if res := (localRunner{}).Fix(context.Background(), localCfg(), FixUseGAIALemonade, nil); res.Err != nil {
		t.Fatalf("g failed: %v", res.Err)
	}
	raw, _ := os.ReadFile(path)
	if strings.Contains(string(raw), "lemonade_mode") {
		t.Fatalf("g saved a choice before GAIA's server was installed: %s", raw)
	}

	*s = starts{}
	row := localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.State == StateOK {
		t.Fatalf("the user's server passed for GAIA's own: %+v", row)
	}
	if row.Fix != FixInstallServer || !strings.Contains(row.Remedy.Command, "--gaia-lemonade") {
		t.Fatalf("want the install step that saves GAIA's own once it runs, got %+v", row)
	}
	if *s != (starts{}) {
		t.Errorf("the check probed, detected or started a server: %+v", *s)
	}
}

// A "gaia" saved earlier, with GAIA's server never installed, is the same case.
func TestASavedGAIAChoiceWithoutTheServerIsNotTheUsersServer(t *testing.T) {
	s, _ := externalSetup(t, map[string]any{"lemonade_mode": "gaia"})
	probeLemonade = func(context.Context) (string, bool, string) {
		s.probe++
		return "http://localhost:13305/api/v1", true, "stub: someone answers"
	}
	row := localRunner{}.checkLemonade(context.Background(), localCfg())
	if row.State == StateOK || row.Fix != FixInstallServer {
		t.Fatalf("row = %+v", row)
	}
	if *s != (starts{}) {
		t.Errorf("the check probed, detected or started a server: %+v", *s)
	}
}
