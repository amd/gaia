// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

type daemonCall struct {
	method string
	path   string
	body   []byte
	start  bool
}

// stubDaemonCalls answers every daemon call with reply and records it.
func stubDaemonCalls(t *testing.T, reply func(daemonCall) ([]byte, error)) *[]daemonCall {
	t.Helper()
	var (
		mu    sync.Mutex
		calls []daemonCall
	)
	old := callDaemon
	callDaemon = func(_ context.Context, method, path string, body []byte, start bool, _, _ string) ([]byte, error) {
		call := daemonCall{method, path, body, start}
		mu.Lock()
		calls = append(calls, call)
		mu.Unlock()
		return reply(call)
	}
	t.Cleanup(func() { callDaemon = old })
	return &calls
}

func TestHoldRegistersThisProcessAsAnAppAndReleasesIt(t *testing.T) {
	calls := stubDaemonCalls(t, func(c daemonCall) ([]byte, error) {
		if c.method == http.MethodPost {
			return []byte(`{"client_id":"abc","clients":1}`), nil
		}
		return []byte(`{"clients":0,"unloaded":true}`), nil
	})

	if err := HoldForApp("tui").Release(time.Second); err != nil {
		t.Fatalf("release: %v", err)
	}

	if len(*calls) != 2 {
		t.Fatalf("want register then release, got %+v", *calls)
	}
	reg, rel := (*calls)[0], (*calls)[1]
	if reg.method != http.MethodPost || reg.path != "/daemon/v1/lemonade/clients" || !reg.start {
		t.Fatalf("registration went to %s %s (start=%v)", reg.method, reg.path, reg.start)
	}
	var body struct {
		Kind string `json:"kind"`
		Pids []int  `json:"pids"`
		Name string `json:"name"`
	}
	if err := json.Unmarshal(reg.body, &body); err != nil {
		t.Fatal(err)
	}
	if body.Kind != "app" || len(body.Pids) != 1 || body.Pids[0] != os.Getpid() || body.Name != "tui" {
		t.Fatalf("registered %+v, want this process as an app", body)
	}
	if rel.method != http.MethodDelete || rel.path != "/daemon/v1/lemonade/clients/abc" || rel.start {
		t.Fatalf("release went to %s %s (start=%v); it must never start a daemon",
			rel.method, rel.path, rel.start)
	}
}

func TestHoldIsRenewedSoARestartedDaemonLearnsOfIt(t *testing.T) {
	old := renewEvery
	renewEvery = 10 * time.Millisecond
	t.Cleanup(func() { renewEvery = old })
	calls := stubDaemonCalls(t, func(c daemonCall) ([]byte, error) {
		return []byte(`{"client_id":"abc","clients":1}`), nil
	})

	hold := HoldForApp("tui")
	time.Sleep(100 * time.Millisecond)
	if err := hold.Release(time.Second); err != nil {
		t.Fatalf("release: %v", err)
	}

	var renewals int
	for _, c := range (*calls)[1:] {
		if c.method != http.MethodPost {
			continue
		}
		renewals++
		var body struct {
			ClientID string `json:"client_id"`
		}
		if err := json.Unmarshal(c.body, &body); err != nil || body.ClientID != "abc" {
			t.Fatalf("a renewal must repeat the client id, sent %s", c.body)
		}
		if c.start {
			t.Fatal("a renewal must never start a daemon")
		}
	}
	if renewals == 0 {
		t.Fatal("the registration was never renewed")
	}
	if last := (*calls)[len(*calls)-1]; last.method != http.MethodDelete {
		t.Fatalf("release must be the last call, got %s", last.method)
	}
}

func TestReleaseWithNothingLeftToReleaseIsQuiet(t *testing.T) {
	for name, err := range map[string]error{
		"daemon gone":     fmt.Errorf("could not reach the GAIA daemon: %w", &daemon.NotRunningError{Path: "x"}),
		"already dropped": errors.New("HTTP 404: No GAIA client abc is registered"),
	} {
		t.Run(name, func(t *testing.T) {
			stubDaemonCalls(t, func(c daemonCall) ([]byte, error) {
				if c.method == http.MethodPost {
					return []byte(`{"client_id":"abc"}`), nil
				}
				return nil, err
			})
			if got := HoldForApp("tui").Release(time.Second); got != nil {
				t.Fatalf("want nil, got %v", got)
			}
		})
	}
}

func TestAFailedRegistrationIsReportedAtExit(t *testing.T) {
	calls := stubDaemonCalls(t, func(daemonCall) ([]byte, error) {
		return nil, errors.New("HTTP 503: refused")
	})
	err := HoldForApp("tui").Release(time.Second)
	if err == nil || !strings.Contains(err.Error(), "refused") {
		t.Fatalf("want the refusal surfaced, got %v", err)
	}
	if len(*calls) != 1 {
		t.Fatalf("nothing to release after a failed registration, got %+v", *calls)
	}
}

func TestReleaseDoesNotHangOnASlowRegistration(t *testing.T) {
	block := make(chan struct{})
	t.Cleanup(func() { close(block) })
	stubDaemonCalls(t, func(daemonCall) ([]byte, error) {
		<-block
		return nil, errors.New("too late")
	})
	started := time.Now()
	if err := HoldForApp("tui").Release(50 * time.Millisecond); err == nil {
		t.Fatal("want an error naming the unfinished registration")
	}
	if time.Since(started) > time.Second {
		t.Fatal("exit waited on the registration")
	}
}
