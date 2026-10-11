// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"

	"github.com/amd/gaia/tui/internal/daemon"
)

// ErrRestarting is GAIA's own Lemonade being down with the daemon unable to
// bring it back yet. Nothing was sent anywhere.
var ErrRestarting = errors.New(
	"GAIA's Lemonade Server is restarting and is not back yet. Try again in a " +
		"moment; if this persists, run `gaia daemon status` and check the daemon log")

// lemonadeEnsurePath is the daemon verb that starts GAIA's server if it is
// stopped and answers once it is healthy (host API 1.2+).
const lemonadeEnsurePath = daemon.APIPrefix + "/lemonade/ensure"

// ensureEmbedded asks the daemon to bring GAIA's server back and waits for its
// answer. A variable so tests stand in for the daemon.
var ensureEmbedded = func(ctx context.Context) error {
	_, err := daemon.CallContext(ctx, http.MethodPost, lemonadeEnsurePath, nil, true,
		"start GAIA's Lemonade Server", "")
	return err
}

// StubEnsureEmbeddedForTest replaces the daemon call so tests in other packages
// never reach, or start, a real daemon.
func StubEnsureEmbeddedForTest(fn func(context.Context) error) (restore func()) {
	old := ensureEmbedded
	ensureEmbedded = fn
	return func() { ensureEmbedded = old }
}

// configuredURL is the server the user chose with LEMONADE_BASE_URL. Values
// exported from GAIA's own credentials file (GAIA_LEMONADE_EMBEDDED=1) describe
// GAIA's restartable server, not a choice — Python's configured_lemonade_url.
func configuredURL() string {
	if strings.TrimSpace(os.Getenv("GAIA_LEMONADE_EMBEDDED")) == "1" {
		return ""
	}
	return strings.TrimSpace(os.Getenv("LEMONADE_BASE_URL"))
}

// GAIARunsLemonade reports whether GAIA's own embedded server is the one to
// use: it is installed and LEMONADE_BASE_URL names no other. Mirrors Python's
// gaia_runs_lemonade().
func GAIARunsLemonade() bool {
	return configuredURL() == "" && EmbeddedInstalled()
}

// CredentialBaseURL is where a cloud provider key may be sent, mirroring
// Python's LemonadeClient.credential_base_url():
//
//   - LEMONADE_BASE_URL set: that server (or base, when given).
//   - GAIA's own server not installed: the system Lemonade at base or the
//     standard address.
//   - Otherwise only GAIA's own running server. While it restarts, the daemon
//     is asked to bring it back and its answer awaited; if it is still not
//     back, ErrRestarting. A key is never sent to the default port to see what
//     answers there, because someone else's Lemonade may.
//
// A loopback base while GAIA runs its server is GAIA's (perhaps from before a
// restart), so the key follows it to its current port. Any other address is
// refused: the user picks another server with LEMONADE_BASE_URL.
func CredentialBaseURL(ctx context.Context, base string) (string, error) {
	base = strings.TrimSpace(base)
	if !GAIARunsLemonade() {
		return ResolveBaseURL(base), nil
	}
	if base != "" && !isLoopbackHTTP(normalizeBaseURL(base)) {
		return "", fmt.Errorf(
			"%s is not GAIA's Lemonade Server, so GAIA did not send it a cloud key. "+
				"To use that server instead, set LEMONADE_BASE_URL to it", normalizeBaseURL(base))
	}
	state := ReadEmbedded()
	if state == nil {
		if err := ensureEmbedded(ctx); err != nil {
			return "", fmt.Errorf("%w (the daemon said: %v)", ErrRestarting, err)
		}
		if state = ReadEmbedded(); state == nil {
			return "", ErrRestarting
		}
	}
	return fmt.Sprintf("http://localhost:%d/api/v1", state.Port), nil
}

func isLoopbackHTTP(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "http" || u.User != nil {
		return false
	}
	host := u.Hostname()
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}
