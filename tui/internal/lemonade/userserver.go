// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

// GAIA uses a server the user runs (lemonade_mode=external) without changing
// how it is set up: loading models on it is fine, registering providers,
// handing it keys, or unloading and deleting models is not. Mirrors Python's
// gaia.llm.lemonade_choice guard, so both front ends refuse the same requests.

import (
	"fmt"
	"net/http"
	"strings"
)

// IsSetupChange reports whether a request changes a Lemonade server's setup
// rather than using it. path is relative to the API base, e.g. "/install".
func IsSetupChange(method, path string) bool {
	if method == http.MethodGet || method == http.MethodHead {
		return false
	}
	route, _, _ := strings.Cut(path, "?")
	route = strings.Trim(route, "/")
	switch route {
	case "install", "uninstall", "delete", "unload":
		return true
	}
	return route == "cloud/auth" || strings.HasPrefix(route, "cloud/auth/")
}

// describeSetupChange names what a refused request would have done, for the
// "so it did not ..." clause of the error.
func describeSetupChange(method, path string) string {
	route, _, _ := strings.Cut(strings.Trim(path, "/"), "?")
	switch {
	case route == "install":
		return "register a cloud provider there"
	case route == "uninstall":
		return "remove a cloud provider from it"
	case route == "delete":
		return "delete a model from it"
	case route == "unload":
		return "unload a model on it"
	case method == http.MethodDelete:
		return "remove a cloud key from it"
	}
	return "hand it a cloud key"
}

// RefuseSetupChange is nil unless GAIA is set to use the user's own server, in
// which case it is the error for a request that would have done what (e.g.
// "register the AMD LLM Gateway there"). An unreadable choice is its own error:
// nothing is sent while GAIA cannot tell whose server it is.
func RefuseSetupChange(what string) error {
	base, active, err := SavedExternal()
	if !active {
		return nil
	}
	if err != nil {
		return err
	}
	return fmt.Errorf("GAIA is set to use your Lemonade Server at %s and doesn't change its "+
		"setup, so it did not %s. Do that on your server yourself, or %s",
		base, what, SwitchBackHint)
}

// RefuseRequest is RefuseSetupChange for one HTTP request, plus the Local only
// refusal of cloud keys and providers; nil for requests that only use the
// server. path is relative to the API base.
func RefuseRequest(method, path string) error {
	if !IsSetupChange(method, path) {
		return nil
	}
	if err := refuseCloudSetup(method, path); err != nil {
		return err
	}
	return RefuseSetupChange(describeSetupChange(method, path))
}
