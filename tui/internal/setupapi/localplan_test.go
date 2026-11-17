// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package setupapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
)

// The TUI offers cloud chat its own way, so every plan it reads must be what a
// local setup downloads: the backend's default leaves the chat model out while
// chat runs on a cloud model, which would hide a download the run still makes.
func TestEveryPlanReadAsksForTheLocalSetupPlan(t *testing.T) {
	var mu sync.Mutex
	queries := map[string]url.Values{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == daemon.APIPrefix+"/status" {
			_ = json.NewEncoder(w).Encode(map[string]any{"service": daemon.ServiceID, "pid": os.Getpid(), "api_version": "1.1"})
			return
		}
		mu.Lock()
		queries[r.Method+" "+r.URL.Path] = r.URL.Query()
		mu.Unlock()
		_, _ = w.Write([]byte(`{"lemonade":{},"chat_model":{"id":"m"}}`))
	}))
	t.Cleanup(srv.Close)

	dir := t.TempDir()
	t.Setenv(daemon.EnvHome, dir)
	u, _ := url.Parse(srv.URL)
	port, _ := strconv.Atoi(u.Port())
	raw, _ := json.Marshal(daemon.Instance{PID: os.Getpid(), Port: port, Token: "t", Host: daemon.DefaultHost, APIVersion: "1.1", Service: daemon.ServiceID, StartedAt: 1})
	if err := os.WriteFile(filepath.Join(dir, "instance.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}

	d := NewAttachedDaemon()
	ctx := context.Background()
	calls := []func() error{
		func() error { _, err := d.Plan(ctx); return err },
		func() error { _, _, err := d.ChooseChat(ctx, "m", false); return err },
		func() error { _, err := d.ChooseVision(ctx, nil); return err },
		func() error { _, err := d.Skip(ctx, "m", true); return err },
		func() error { _, err := d.Remove(ctx, "m"); return err },
	}
	for _, call := range calls {
		if err := call(); err != nil {
			t.Fatal(err)
		}
	}
	for _, route := range []string{"GET /plan", "POST /chat-model", "POST /vision-model", "POST /models/skip", "POST /models/remove"} {
		method, path, _ := strings.Cut(route, " ")
		q, ok := queries[method+" "+setupPath+path]
		if !ok {
			t.Fatalf("%s was never called; saw %v", route, queries)
		}
		if got := q["skip_chat_model"]; len(got) != 1 || got[0] != "false" {
			t.Errorf("%s skip_chat_model = %v, want [false]", route, got)
		}
	}
}
