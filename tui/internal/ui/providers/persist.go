// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"

	"github.com/amd/gaia/tui/internal/daemon"
)

// Lemonade holds a pasted key in memory only, so without these a key typed
// into one TUI was gone for the next TUI and after every Lemonade restart. The
// daemon keeps it in the OS credential store (go-keyring cannot read what the
// Python side writes, so the TUI cannot do it itself) and replays it into
// Lemonade on request. The key goes over authenticated loopback and is never
// read back out.
const keyAlternative = "Set LEMONADE_<PROVIDER>_API_KEY in Lemonade's environment instead"

func keyPath(provider, action string) string {
	return "/daemon/v1/providers/" + url.PathEscape(provider) + "/" + action
}

// callDaemon, rememberKey, forgetKey and restoreKey are variables so tests can
// stand in for the daemon.
var callDaemon = daemon.Call

var rememberKey = func(provider, key string) error {
	body, err := json.Marshal(map[string]string{"key": key})
	if err != nil {
		return fmt.Errorf("could not encode the request: %w", err)
	}
	// Starting the daemon is warranted: the user just asked to connect.
	_, err = callDaemon(http.MethodPost, keyPath(provider, "key"), body, true,
		"keep the "+provider+" key", keyAlternative)
	return err
}

var forgetKey = func(provider string) error {
	_, err := callDaemon(http.MethodDelete, keyPath(provider, "key"), nil, true,
		"forget the "+provider+" key", keyAlternative)
	return err
}

// restoreKey replays a kept key into Lemonade. Nothing stored is not an error.
// The daemon is started if it is down: it alone can read the credential store,
// and without it a saved key reads as "key needed".
var restoreKey = func(provider string) error {
	_, err := callDaemon(http.MethodPost, keyPath(provider, "authenticate"), nil, true,
		"restore the "+provider+" key", keyAlternative)
	return err
}
