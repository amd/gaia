package gateway

import (
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/amd/gaia/tui/internal/daemon"
)

// gatewayTokenPath owns the stored token; gatewayAuthPath replays it into
// Lemonade. Both keep the token inside the Python process — it is written once
// on the way in and never read back out over HTTP.
const (
	gatewayTokenPath = "/daemon/v1/gateway/token"
	gatewayAuthPath  = "/daemon/v1/gateway/authenticate"
	tokenAlternative = "Run `gaia gateway auth` once to store the token instead"
)

// rememberToken asks the daemon to keep the token in the OS credential store.
//
// The store is Python-only and the two keyring libraries do not interoperate:
// verified on Windows, a value written by go-keyring is invisible to
// python-keyring and vice versa, because they compose different Credential
// Manager target names. So the TUI cannot write the token itself, and without
// this a token typed here survived one session while the same token entered
// through `gaia gateway auth` persisted.
//
// The token goes over authenticated loopback to a process owned by the same
// user — the channel the TUI already uses, and no wider than the loopback call
// it makes to Lemonade with the same value a moment earlier. It is never
// logged and never written to a TUI file.
func rememberToken(token string) error {
	body, err := json.Marshal(map[string]string{"token": token})
	if err != nil {
		return fmt.Errorf("could not encode the request: %w", err)
	}
	// Starting the daemon is warranted here: the user explicitly asked to
	// connect, and "the token vanished because a background process happened
	// to be down" is exactly the surprise this route exists to remove.
	_, err = daemon.Call(http.MethodPost, gatewayTokenPath, body, true,
		"store the gateway token", tokenAlternative)
	return err
}

// restoreToken replays a previously stored token into Lemonade, which forgets
// it on every restart. It reports whether the gateway is now authenticated.
//
// A failure is not surfaced to the user: the common case is simply that
// nothing was stored, and the token prompt already covers that.
func restoreToken() bool {
	raw, err := daemon.Call(http.MethodPost, gatewayAuthPath, nil, false,
		"restore the gateway token", tokenAlternative)
	if err != nil {
		return false
	}
	var body struct {
		Authenticated bool `json:"authenticated"`
	}
	return json.Unmarshal(raw, &body) == nil && body.Authenticated
}
