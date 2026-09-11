package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

// gatewayTokenPath owns the stored token; gatewayAuthPath replays it into
// Lemonade. Both keep the token inside the Python process — it is written once
// on the way in and never read back out over HTTP.
const (
	gatewayTokenPath = "/daemon/v1/gateway/token"
	gatewayAuthPath  = "/daemon/v1/gateway/authenticate"
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
	_, err = daemonCall(http.MethodPost, gatewayTokenPath, body, true,
		"store the gateway token")
	return err
}

// restoreToken replays a previously stored token into Lemonade, which forgets
// it on every restart. It reports whether the gateway is now authenticated.
//
// A failure is not surfaced to the user: the common case is simply that
// nothing was stored, and the token prompt already covers that.
func restoreToken() bool {
	raw, err := daemonCall(http.MethodPost, gatewayAuthPath, nil, false,
		"restore the gateway token")
	if err != nil {
		return false
	}
	var body struct {
		Authenticated bool `json:"authenticated"`
	}
	return json.Unmarshal(raw, &body) == nil && body.Authenticated
}

// daemonCall performs one authenticated daemon request and returns the 2xx
// body. start=false attaches only to a daemon that is already running.
func daemonCall(method, path string, body []byte, start bool, op string) ([]byte, error) {
	dc := daemon.New(daemon.Options{})
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()

	var (
		inst *daemon.Instance
		err  error
	)
	if start {
		inst, err = dc.StartOrAttach(ctx)
	} else {
		inst, err = dc.Attach(ctx)
	}
	if err != nil {
		return nil, fmt.Errorf("could not reach the GAIA daemon: %w", err)
	}

	req := daemon.Request{Method: method, Path: path, Body: body, Op: op}
	if body != nil {
		req.Header = http.Header{"Content-Type": []string{"application/json"}}
	}
	resp, _, err := dc.Do(ctx, inst, req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		// ErrorDetail prefixes the status; IsRouteMissing matches on the bare
		// detail, which is what tells version skew from the route's own refusal.
		full := daemon.ErrorDetail(resp)
		bare := strings.TrimPrefix(full, fmt.Sprintf("HTTP %d: ", resp.StatusCode))
		if daemon.IsRouteMissing(path, resp.StatusCode, bare) {
			return nil, &daemon.RouteMissingError{
				Op:          op,
				Path:        path,
				Alternative: "Run `gaia gateway auth` once to store the token instead",
			}
		}
		// A 503 carries the daemon's platform-specific remedy for an
		// unavailable credential store; pass it through rather than inventing
		// a message.
		return nil, fmt.Errorf("%s", full)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 1<<16))
}
