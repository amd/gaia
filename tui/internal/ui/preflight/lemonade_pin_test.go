package preflight

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// envStub is a `gaia` that records the LEMONADE_BASE_URL it was given, prints
// body, and exits with code. It returns the stub's path and a reader for what
// it recorded.
func envStub(t *testing.T, code int, body string) (string, func() string) {
	t.Helper()
	dir := t.TempDir()
	out := filepath.Join(dir, "out.json")
	seen := filepath.Join(dir, "seen.txt")
	if err := os.WriteFile(out, []byte(body+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	var path, script string
	if runtime.GOOS == "windows" {
		path = filepath.Join(dir, "gaia-stub.bat")
		script = fmt.Sprintf("@echo off\r\necho [%%LEMONADE_BASE_URL%%]> \"%s\"\r\ntype \"%s\"\r\nexit /b %d\r\n", seen, out, code)
	} else {
		path = filepath.Join(dir, "gaia-stub.sh")
		script = fmt.Sprintf("#!/bin/sh\necho \"[$LEMONADE_BASE_URL]\" > '%s'\ncat '%s'\nexit %d\n", seen, out, code)
	}
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return path, func() string {
		raw, err := os.ReadFile(seen)
		if err != nil {
			t.Fatalf("the stub never ran: %v", err)
		}
		return strings.TrimSpace(string(raw))
	}
}

// systemLemonadeAnswering is a machine where a system Lemonade answers on
// 13305 and GAIA's own server is not running.
func systemLemonadeAnswering(t *testing.T) string {
	t.Helper()
	const base = "http://localhost:13305/api/v1"
	t.Setenv("GAIA_HOME", t.TempDir())
	t.Setenv(lemonadeBaseURLEnv, "")
	orig := probeLemonade
	t.Cleanup(func() { probeLemonade = orig })
	probeLemonade = func(context.Context) (string, bool, string) { return base, true, "stub" }
	return base
}

// The gate started the system Lemonade, the user connected the AMD gateway on
// it, and the setup check then started GAIA's own server on another port — so
// the gateway read "not connected". Setup must check the server the gate is on.
func TestSetupCheckStaysOnTheServerTheGateIsUsing(t *testing.T) {
	base := systemLemonadeAnswering(t)
	stub, seen := envStub(t, 0, embedderLoadsJSON)
	stubGaiaInit(t, func() (string, error) { return stub, nil })
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		return []lemonade.Model{{ID: "amd.deepseek-v4.1-flash", Recipe: "cloud"}}, nil
	})

	row := modelRow(localRunner{opts: LocalOptions{Model: "amd.deepseek-v4.1-flash"}})

	if row.State != StateOK {
		t.Fatalf("row = %+v", row)
	}
	if got := seen(); got != "["+base+"]" {
		t.Fatalf("`gaia init --check` ran with LEMONADE_BASE_URL=%s, want %s", got, base)
	}
}

// The setup the model row's f key runs must stay there too, or it installs and
// starts a second server and moves GAIA onto it.
func TestSetupFixStaysOnTheServerTheGateIsUsing(t *testing.T) {
	base := systemLemonadeAnswering(t)
	stub, seen := envStub(t, 0, "")
	stubGaiaInit(t, func() (string, error) { return stub, nil })

	r := localRunner{opts: LocalOptions{Model: "amd.deepseek-v4.1-flash"}}
	if res := r.Fix(context.Background(), localCfg(), FixRunSetup, nil); res.Err != nil {
		t.Fatalf("fix failed: %+v", res)
	}
	if got := seen(); got != "["+base+"]" {
		t.Fatalf("`gaia init` ran with LEMONADE_BASE_URL=%s, want %s", got, base)
	}
}

// GAIA's own running server, a configured URL, or nothing answering: setup
// already resolves the right server, and pinning would change what it does.
func TestSetupIsNotPinnedWhenItAlreadyResolvesTheServer(t *testing.T) {
	t.Run("nothing answering", func(t *testing.T) {
		systemLemonadeAnswering(t)
		probeLemonade = func(context.Context) (string, bool, string) {
			return "http://localhost:13305/api/v1", false, "stub"
		}
		if env := pinnedLemonadeEnv(context.Background()); env != nil {
			t.Fatalf("pinned %v with no server answering", env)
		}
	})
	t.Run("configured URL", func(t *testing.T) {
		systemLemonadeAnswering(t)
		t.Setenv(lemonadeBaseURLEnv, "http://localhost:9999")
		if env := pinnedLemonadeEnv(context.Background()); env != nil {
			t.Fatalf("pinned %v over the user's own LEMONADE_BASE_URL", env)
		}
	})
	t.Run("GAIA's own server running", func(t *testing.T) {
		systemLemonadeAnswering(t)
		dir := filepath.Join(os.Getenv("GAIA_HOME"), "lemonade")
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		state := fmt.Sprintf(`{"pid": %d, "port": 64965, "api_key": "k"}`, os.Getpid())
		if err := os.WriteFile(filepath.Join(dir, "state.json"), []byte(state), 0o600); err != nil {
			t.Fatal(err)
		}
		if env := pinnedLemonadeEnv(context.Background()); env != nil {
			t.Fatalf("pinned %v while GAIA's own server is the one in use", env)
		}
	})
}

// A cloud model the server cannot serve is a chat problem; the row it lands on
// must not be labelled "Embeddings" beside an embedder that loaded.
func TestACloudChatRowIsNotLabelledEmbeddings(t *testing.T) {
	cloud := localRunner{opts: LocalOptions{Model: "amd.deepseek-v4.1-flash"}}
	for _, row := range cloud.Rows(localCfg()) {
		if row.Key == KeyModel && row.Label == "Embeddings" {
			t.Fatalf("a cloud session's model row is labelled %q", row.Label)
		}
	}
	claude := localRunner{opts: LocalOptions{ClaudeMode: true}}
	for _, row := range claude.Rows(localCfg()) {
		if row.Key == KeyModel && row.Label != "Embeddings" {
			t.Fatalf("a Claude session's model row is labelled %q, want Embeddings", row.Label)
		}
	}

	stubGaiaInit(t, func() (string, error) { return jsonStub(t, 0, embedderLoadsJSON), nil })
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) { return nil, nil })
	row := modelRow(cloud)
	if row.State != StateFailed || !strings.Contains(row.Detail, "embedding model loads") {
		t.Fatalf("the failure does not say the embedder is fine and chat is not: %+v", row)
	}
}
