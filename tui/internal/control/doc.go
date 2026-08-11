// Package control exposes a loopback HTTP API that drives the *running* TUI.
//
// It is not a headless replica: keys and text are injected into the live
// [tea.Program] with Send, and the screen served back is the exact frame the
// human at the keyboard is looking at. That makes it possible for an assistant
// to navigate the TUI while the user watches the same session.
//
// The server binds 127.0.0.1 only, requires a bearer token on every request,
// and advertises itself through ~/.gaia/tui/control.json (mode 0600) so a
// client can find it without a fixed port.
package control

const (
	// ServiceID identifies this server in the discovery file and /status so a
	// client can tell a recycled port from a real TUI.
	ServiceID = "gaia-tui-control"

	// APIVersion is the wire contract version carried in the URL and /status.
	APIVersion = "v1"

	// APIPrefix is the common prefix for every control endpoint.
	APIPrefix = "/control/v1"

	// AuthScheme is the Authorization header scheme clients must use.
	AuthScheme = "Bearer"

	// Host is the only interface the control server ever binds.
	Host = "127.0.0.1"

	// ReservedPort is never bound. 4001 is reserved repo-wide.
	ReservedPort = 4001
)
