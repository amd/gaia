// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package homeassistant

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
)

const callerToken = "caller-token-123"

// recorded is one request the fake daemon saw.
type recorded struct {
	method, path, auth, contentType string
	body                            []byte
}

// fakeDaemon answers every request with status and body, recording it.
func fakeDaemon(t *testing.T, status int, body string) (*Client, *[]recorded) {
	t.Helper()
	var seen []recorded
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		seen = append(seen, recorded{
			method: r.Method, path: r.URL.Path, auth: r.Header.Get("Authorization"),
			contentType: r.Header.Get("Content-Type"), body: raw,
		})
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}))
	t.Cleanup(srv.Close)
	host, portText, err := net.SplitHostPort(strings.TrimPrefix(srv.URL, "http://"))
	if err != nil {
		t.Fatal(err)
	}
	port, _ := strconv.Atoi(portText)
	inst := &daemon.Instance{Host: host, Port: port, Token: callerToken}
	c := &Client{
		dc:     daemon.New(daemon.Options{}),
		ensure: func(context.Context) (*daemon.Instance, error) { return inst, nil },
	}
	return c, &seen
}

func only(t *testing.T, seen *[]recorded) recorded {
	t.Helper()
	if len(*seen) != 1 {
		t.Fatalf("want exactly one request, got %d", len(*seen))
	}
	return (*seen)[0]
}

func assertCall(t *testing.T, r recorded, method, path string) {
	t.Helper()
	if r.method != method || r.path != path {
		t.Errorf("request = %s %s, want %s %s", r.method, r.path, method, path)
	}
	if r.auth != "Bearer "+callerToken {
		t.Errorf("Authorization = %q, want the caller token", r.auth)
	}
}

func TestStatusIsAGetThatParsesTheAnswer(t *testing.T) {
	c, seen := fakeDaemon(t, 200,
		`{"connected":true,"url":"http://ha.local:8123","active":true,"enabled":true}`)
	s, err := c.Status(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	r := only(t, seen)
	assertCall(t, r, http.MethodGet, "/v1/gaia/home-assistant")
	if len(r.body) != 0 {
		t.Errorf("a GET must carry no body, got %q", r.body)
	}
	if !s.Connected || s.URL != "http://ha.local:8123" || !s.Active || s.Enabled == nil || !*s.Enabled {
		t.Errorf("status = %+v", s)
	}
}

func TestStatusReadsANullURLAsEmpty(t *testing.T) {
	c, _ := fakeDaemon(t, 200, `{"connected":false,"url":null,"active":false}`)
	s, err := c.Status(context.Background())
	if err != nil || s.Connected || s.URL != "" || s.Enabled != nil {
		t.Fatalf("status = %+v, err = %v", s, err)
	}
}

func TestConnectPutsExactlyTheURLAndToken(t *testing.T) {
	c, seen := fakeDaemon(t, 200,
		`{"connected":true,"url":"http://ha.local:8123","active":true,"enabled":true,"tool_count":28,"detail":"Connected: 28 Home Assistant tools."}`)
	s, err := c.Connect(context.Background(), "http://ha.local:8123", "secret-ha-token")
	if err != nil {
		t.Fatal(err)
	}
	r := only(t, seen)
	assertCall(t, r, http.MethodPut, "/v1/gaia/home-assistant")
	if r.contentType != "application/json" {
		t.Errorf("Content-Type = %q", r.contentType)
	}
	var body map[string]any
	if err := json.Unmarshal(r.body, &body); err != nil {
		t.Fatalf("body is not JSON: %q", r.body)
	}
	// The sidecar forbids extra fields, so the shape is the whole contract.
	if len(body) != 2 || body["url"] != "http://ha.local:8123" || body["token"] != "secret-ha-token" {
		t.Errorf("body = %v, want exactly url and token", body)
	}
	if s.ToolCount != 28 || s.Detail != "Connected: 28 Home Assistant tools." {
		t.Errorf("status = %+v", s)
	}
}

func TestConnectRefusalCarriesTheSidecarsWordsAndNotTheToken(t *testing.T) {
	const detail = "Home Assistant rejected the access token. Create a new long-lived access token in your Home Assistant profile (Security tab) and paste it here."
	c, _ := fakeDaemon(t, 400, `{"detail":"`+detail+`"}`)
	_, err := c.Connect(context.Background(), "http://ha.local:8123", "secret-ha-token")
	var se *StatusError
	if !errors.As(err, &se) || se.Status != 400 {
		t.Fatalf("err = %v, want a 400 StatusError", err)
	}
	if err.Error() != detail {
		t.Errorf("error = %q, want the detail verbatim", err.Error())
	}
	if strings.Contains(err.Error(), "secret-ha-token") {
		t.Error("the token leaked into the error")
	}
}

func TestAValidationListIsNotShownAsText(t *testing.T) {
	c, _ := fakeDaemon(t, 422, `{"detail":[{"loc":["body","token"],"msg":"field required"}]}`)
	_, err := c.Connect(context.Background(), "http://ha.local:8123", "t")
	if err == nil || !strings.Contains(err.Error(), "HTTP 422") || strings.Contains(err.Error(), "loc") {
		t.Fatalf("err = %v", err)
	}
}

func TestTestIsAPostToTheTestRoute(t *testing.T) {
	c, seen := fakeDaemon(t, 200, `{"ok":false,"detail":"Home Assistant isn't answering."}`)
	res, err := c.Test(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	r := only(t, seen)
	assertCall(t, r, http.MethodPost, "/v1/gaia/home-assistant/test")
	if res.OK || res.Detail != "Home Assistant isn't answering." {
		t.Errorf("result = %+v", res)
	}
}

func TestDisconnectIsADelete(t *testing.T) {
	c, seen := fakeDaemon(t, 200,
		`{"connected":false,"url":null,"active":false,"detail":"GAIA forgot this Home Assistant."}`)
	s, err := c.Disconnect(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	assertCall(t, only(t, seen), http.MethodDelete, "/v1/gaia/home-assistant")
	if s.Connected || s.Detail != "GAIA forgot this Home Assistant." {
		t.Errorf("status = %+v", s)
	}
}

func TestAnAgentWithoutTheRouteIsCalledOld(t *testing.T) {
	c, _ := fakeDaemon(t, 404, `{"detail":"Not Found"}`)
	if _, err := c.Status(context.Background()); !errors.Is(err, ErrTooOld) {
		t.Fatalf("err = %v, want ErrTooOld", err)
	}
}

func TestAnUnreachableDaemonSaysWhatFailed(t *testing.T) {
	c := &Client{
		dc: daemon.New(daemon.Options{}),
		ensure: func(context.Context) (*daemon.Instance, error) {
			return nil, errors.New("daemon down")
		},
	}
	_, err := c.Status(context.Background())
	if err == nil || !strings.Contains(err.Error(), "read the Home Assistant connection") ||
		!strings.Contains(err.Error(), "daemon down") {
		t.Fatalf("err = %v", err)
	}
}
