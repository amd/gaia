// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import (
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
)

func TestPprofIsOffByDefault(t *testing.T) {
	if err := startPprof(""); err != nil {
		t.Fatalf("empty address must be a no-op, got %v", err)
	}
}

func TestPprofRefusesANonLoopbackAddress(t *testing.T) {
	for _, addr := range []string{"0.0.0.0:6060", "192.168.1.5:6060", "example.com:6060"} {
		err := startPprof(addr)
		if err == nil || !strings.Contains(err.Error(), "loopback") {
			t.Errorf("%s: want a loopback refusal, got %v", addr, err)
		}
	}
}

func TestPprofRefusesAnAddressWithoutAPort(t *testing.T) {
	if err := startPprof("127.0.0.1"); err == nil {
		t.Fatal("want an error for a missing port")
	}
}

func TestPprofServesTheIndexOnLoopback(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	ln.Close()
	if err := startPprof(addr); err != nil {
		t.Fatal(err)
	}
	resp, err := http.Get("http://" + addr + "/debug/pprof/")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 || !strings.Contains(string(body), "goroutine") {
		t.Fatalf("status %d, body %.120q", resp.StatusCode, body)
	}
}
