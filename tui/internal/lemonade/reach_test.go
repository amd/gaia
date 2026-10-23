// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func stubDial(t *testing.T, err error) *string {
	t.Helper()
	var got string
	old := dialGateway
	dialGateway = func(_ context.Context, addr string) error { got = addr; return err }
	t.Cleanup(func() { dialGateway = old })
	return &got
}

func TestProbeGatewayDialsTheDefaultHostOn443(t *testing.T) {
	addr := stubDial(t, nil)
	if err := ProbeGateway(context.Background(), ""); err != nil {
		t.Fatalf("reachable gateway reported %v", err)
	}
	if *addr != "llm-api.amd.com:443" {
		t.Fatalf("dialed %q", *addr)
	}
}

func TestProbeGatewayUsesAConfiguredHostAndPort(t *testing.T) {
	addr := stubDial(t, nil)
	if err := ProbeGateway(context.Background(), "http://gw.example.test:8080/v1"); err != nil {
		t.Fatal(err)
	}
	if *addr != "gw.example.test:8080" {
		t.Fatalf("dialed %q", *addr)
	}
}

// An offline network must read as no network, never as a bad key.
func TestAnUnreachableGatewaySaysNoNetworkNotKey(t *testing.T) {
	stubDial(t, errors.New("lookup llm-api.amd.com: no such host"))
	err := ProbeGateway(context.Background(), AMDGatewayURL)
	var u *UnreachableError
	if !errors.As(err, &u) || u.Host != "llm-api.amd.com" {
		t.Fatalf("err = %v", err)
	}
	brief := u.Brief()
	if !strings.Contains(brief, "No network available") || strings.Contains(strings.ToLower(brief), "key") {
		t.Fatalf("brief = %q", brief)
	}
}

func TestAGatewayURLWithNoHostIsRefused(t *testing.T) {
	stubDial(t, nil)
	err := ProbeGateway(context.Background(), "not a url")
	var u *UnreachableError
	if err == nil || errors.As(err, &u) {
		t.Fatalf("err = %v, want a URL error, not unreachable", err)
	}
}
