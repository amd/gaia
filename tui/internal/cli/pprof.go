// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import (
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/pprof"
	"os"
)

var pprofAddr string

// startPprof serves Go's profiler on a loopback address, for measuring the
// TUI's startup and render cost. Loopback only: the profile endpoints expose
// heap contents, which can hold whatever the agent read.
func startPprof(addr string) error {
	if addr == "" {
		return nil
	}
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return fmt.Errorf("--pprof %q: want host:port, e.g. 127.0.0.1:6060: %w", addr, err)
	}
	ip := net.ParseIP(host)
	if host != "localhost" && (ip == nil || !ip.IsLoopback()) {
		return fmt.Errorf("--pprof %q: only a loopback address is allowed (127.0.0.1, ::1 or localhost)", addr)
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("--pprof: cannot listen on %s: %w", addr, err)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/debug/pprof/", pprof.Index)
	mux.HandleFunc("/debug/pprof/cmdline", pprof.Cmdline)
	mux.HandleFunc("/debug/pprof/profile", pprof.Profile)
	mux.HandleFunc("/debug/pprof/symbol", pprof.Symbol)
	mux.HandleFunc("/debug/pprof/trace", pprof.Trace)
	// Stderr, not the screen: the TUI owns stdout once it starts.
	fmt.Fprintf(os.Stderr, "pprof → http://%s/debug/pprof/\n", ln.Addr())
	go func() {
		if err := http.Serve(ln, mux); err != nil && !errors.Is(err, net.ErrClosed) {
			fmt.Fprintf(os.Stderr, "pprof: %v\n", err)
		}
	}()
	return nil
}
