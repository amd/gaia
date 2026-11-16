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
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

// clientsPath is where a front end tells the daemon it is using GAIA's
// Lemonade Server (host API 1.4+). The daemon unloads the server's models once
// the last app releases, and stops the server after its idle timeout.
const clientsPath = daemon.APIPrefix + "/lemonade/clients"

// callDaemon is the daemon call, a variable so tests stand in for the daemon.
var callDaemon = daemon.CallContext

// registerTimeout covers a cold daemon start, which the registration may cause.
const registerTimeout = 90 * time.Second

// renewEvery is how often a held registration is repeated, so a daemon that
// restarted while the TUI ran learns of it before its idle timeout fires.
// Mirrors HOLD_RENEW_SECONDS in gaia.llm.lemonade_service. A variable for tests.
var renewEvery = 30 * time.Second

// AppHold is this TUI's registration with the daemon as an app using GAIA's
// Lemonade Server. While it is held the daemon keeps the models loaded.
type AppHold struct {
	name     string
	done     chan struct{}
	stop     chan struct{}
	clientID string
	err      error

	mu        sync.Mutex
	renewErr  error
	stopOnce  sync.Once
	renewDone chan struct{}
}

// HoldForApp registers this process in the background, so a cold daemon start
// never delays the TUI opening, then repeats the registration until Release.
func HoldForApp(name string) *AppHold {
	h := &AppHold{
		name:      name,
		done:      make(chan struct{}),
		stop:      make(chan struct{}),
		renewDone: make(chan struct{}),
	}
	go func() {
		defer close(h.done)
		h.clientID, h.err = h.register(true, "")
		if h.err != nil {
			close(h.renewDone)
			return
		}
		go h.renew()
	}()
	return h
}

// register posts the registration. start=false never starts a daemon.
func (h *AppHold) register(start bool, clientID string) (string, error) {
	fields := map[string]any{"kind": "app", "pids": []int{os.Getpid()}, "name": h.name}
	if clientID != "" {
		fields["client_id"] = clientID
	}
	body, err := json.Marshal(fields)
	if err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(context.Background(), registerTimeout)
	defer cancel()
	out, err := callDaemon(ctx, http.MethodPost, clientsPath, body, start,
		"register with GAIA's Lemonade Server",
		"restart the daemon with `gaia daemon restart` so it frees the "+
			"server's memory when GAIA closes")
	if err != nil {
		return "", err
	}
	var resp struct {
		ClientID string `json:"client_id"`
	}
	if err := json.Unmarshal(out, &resp); err != nil || resp.ClientID == "" {
		return "", fmt.Errorf("the daemon answered the registration without a client id: %q", out)
	}
	return resp.ClientID, nil
}

func (h *AppHold) renew() {
	defer close(h.renewDone)
	ticker := time.NewTicker(renewEvery)
	defer ticker.Stop()
	for {
		select {
		case <-h.stop:
			return
		case <-ticker.C:
			_, err := h.register(false, h.clientID)
			if err != nil && releasedAlready(err) {
				err = nil // no daemon to tell; the next one hears on a later tick
			}
			h.mu.Lock()
			h.renewErr = err
			h.mu.Unlock()
		}
	}
}

// Release ends the registration, so the daemon can unload GAIA's models when
// no other GAIA client is using them. It waits at most wait for a registration
// still in flight.
func (h *AppHold) Release(wait time.Duration) error {
	h.stopOnce.Do(func() { close(h.stop) })
	select {
	case <-h.done:
	case <-time.After(wait):
		return fmt.Errorf("the GAIA daemon had not answered this TUI's registration "+
			"after %s, so it does not know the TUI closed; its idle timeout frees "+
			"Lemonade's memory instead", wait)
	}
	if h.err != nil {
		return fmt.Errorf("could not register this TUI with the GAIA daemon, so it "+
			"does not know the TUI closed; its idle timeout frees Lemonade's memory "+
			"instead: %w", h.err)
	}
	<-h.renewDone
	ctx, cancel := context.WithTimeout(context.Background(), wait)
	defer cancel()
	_, err := callDaemon(ctx, http.MethodDelete, clientsPath+"/"+h.clientID, nil, false,
		"release GAIA's Lemonade Server", "")
	if err == nil || releasedAlready(err) {
		return nil
	}
	h.mu.Lock()
	renewErr := h.renewErr
	h.mu.Unlock()
	if renewErr != nil {
		err = fmt.Errorf("%w (renewing it had also failed: %v)", err, renewErr)
	}
	return fmt.Errorf("could not release this TUI's hold on GAIA's Lemonade "+
		"Server; the daemon frees its models once it sees the TUI has exited: %w", err)
}

// releasedAlready is a call with nobody to answer it: the daemon has exited,
// or dropped the registration (404) when it restarted.
func releasedAlready(err error) bool {
	var notRunning *daemon.NotRunningError
	var stale *daemon.StaleError
	return errors.As(err, &notRunning) || errors.As(err, &stale) ||
		strings.HasPrefix(err.Error(), "HTTP 404")
}
