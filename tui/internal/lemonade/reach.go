// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"time"
)

// GatewayProbeTimeout bounds one reachability check, so an offline network
// answers in seconds rather than at the OS connect timeout.
const GatewayProbeTimeout = 4 * time.Second

// UnreachableError is a gateway host that does not resolve or does not answer.
// It says nothing about the key: no request was sent.
type UnreachableError struct {
	Host string
	Err  error
}

func (e *UnreachableError) Error() string { return fmt.Sprintf("can't reach %s: %v", e.Host, e.Err) }
func (e *UnreachableError) Unwrap() error { return e.Err }

// Brief is the one line a provider list shows for it.
func (e *UnreachableError) Brief() string {
	return "No network available — can't reach " + e.Host + " (needs AMD's network or VPN)"
}

// ErrCannotCheck means a dial from here would not prove anything: Lemonade,
// which makes the gateway's requests, runs elsewhere, or traffic leaves through
// a proxy. The option then stays choosable.
var ErrCannotCheck = errors.New("the gateway is reached through Lemonade on another machine or a proxy, so it is not checked from here")

// proxyEnv names the variables that send this machine's traffic through a proxy.
var proxyEnv = []string{"HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"}

// ProbeGatewayFrom checks the gateway only where the answer holds for the
// requests Lemonade at lemonadeBase will make.
func ProbeGatewayFrom(ctx context.Context, lemonadeBase, base string) error {
	if !isLoopbackHTTP(ResolveBaseURL(lemonadeBase)) {
		return ErrCannotCheck
	}
	for _, name := range proxyEnv {
		if os.Getenv(name) != "" {
			return ErrCannotCheck
		}
	}
	return ProbeGateway(ctx, base)
}

// dialGateway is a var so tests reach no network.
var dialGateway = func(ctx context.Context, addr string) error {
	conn, err := (&net.Dialer{}).DialContext(ctx, "tcp", addr)
	if err != nil {
		return err
	}
	return conn.Close()
}

// ProbeGateway reports whether base's host resolves and accepts a connection.
// An empty base probes the AMD gateway's default address.
func ProbeGateway(ctx context.Context, base string) error {
	if base == "" {
		base = AMDGatewayURL
	}
	u, err := url.Parse(base)
	if err != nil || u.Hostname() == "" {
		return fmt.Errorf("gateway URL %q has no host to check", base)
	}
	port := u.Port()
	if port == "" {
		port = "443"
		if u.Scheme == "http" {
			port = "80"
		}
	}
	ctx, cancel := context.WithTimeout(ctx, GatewayProbeTimeout)
	defer cancel()
	if err := dialGateway(ctx, net.JoinHostPort(u.Hostname(), port)); err != nil {
		return &UnreachableError{Host: u.Hostname(), Err: err}
	}
	return nil
}
