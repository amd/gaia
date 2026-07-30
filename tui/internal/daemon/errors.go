package daemon

import (
	"fmt"
	"net/http"
	"strings"
)

// NotRunningError means no daemon has registered itself at all — instance.json
// is absent. Distinct from StaleError so a caller can decide to start one.
type NotRunningError struct {
	Path string
}

func (e *NotRunningError) Error() string {
	return fmt.Sprintf(
		"no GAIA daemon is registered (%s does not exist). "+
			"Start one with `gaia daemon start`.", e.Path)
}

// StaleKind classifies WHY a recorded instance cannot be trusted, because the
// remedy differs: a garbage record is reclaimed by starting a fresh daemon,
// whereas our own live-but-wedged daemon must not be silently replaced.
type StaleKind int

const (
	// StaleCorrupt — the file is missing fields, unreadable, or not JSON.
	StaleCorrupt StaleKind = iota
	// StalePIDDead — the recorded pid is gone.
	StalePIDDead
	// StaleUnresponsive — the pid is alive but the recorded port does not answer
	// as a healthy daemon. Probably our own daemon gone bad.
	StaleUnresponsive
	// StaleForeign — the port answers as a different service, or as a different
	// pid. After a hard crash the OS reused the pid AND something else took the
	// freed port, so the record is garbage and can be reclaimed.
	StaleForeign
)

// StaleError means instance.json exists but must not be trusted: it is corrupt,
// its pid is dead, or the recorded port answers as something other than our
// daemon. Reason names which check failed; Kind drives the recovery decision.
type StaleError struct {
	Path   string
	Reason string
	Kind   StaleKind
}

func (e *StaleError) Error() string {
	return fmt.Sprintf(
		"the recorded GAIA daemon at %s cannot be trusted: %s. "+
			"Reclaim it with `gaia daemon restart`, then retry; "+
			"the daemon log is at %s.", e.Path, e.Reason, logPathForMessage())
}

// VersionError means the running daemon speaks a contract this client cannot
// use — a MAJOR skew, or a MINOR below the agents/relay floor.
//
// The version is a property of the installed GAIA core, not of the running
// process, so a restart relaunches the same one. Aligning the two means
// upgrading the core.
type VersionError struct {
	Have   string
	Want   string
	Reason string
}

func (e *VersionError) Error() string {
	return fmt.Sprintf(
		"the running GAIA daemon speaks host API v%s, but this app needs v%s or newer: %s. %s",
		e.Have, e.Want, e.Reason, UpgradeCoreHint)
}

// UpgradeCoreHint is the remedy for any host-API skew. It says a restart is not
// the fix, so a user who tries one does not loop on it.
const UpgradeCoreHint = "Upgrade the installed GAIA core so it matches this app: " +
	"`pip install --upgrade amd-gaia`, or re-run the installer from https://amd-gaia.ai. " +
	"The version comes from the installed core, so `gaia daemon restart` brings the same one back."

// StartError means we could not bring a daemon up (lock contention, launch
// failure, or it never registered in time).
type StartError struct {
	Reason string
}

func (e *StartError) Error() string {
	return fmt.Sprintf(
		"could not start the GAIA daemon: %s. Inspect the daemon log at %s, "+
			"or run `gaia daemon start` in a terminal to see the failure directly.",
		e.Reason, logPathForMessage())
}

// RouteMissingError means the running daemon has no such route at all.
//
// A bare 404 from /daemon/v1/* is version skew — the daemon is an older build
// than the client talking to it — not a refusal that route issued. The two read
// identically at the call site and mean opposite things: a 404 the ROUTE sends
// is about the thing being asked for; a 404 because the route is absent is
// about the daemon. Reporting the first as the second sends the user to check a
// remote service over a local restart.
type RouteMissingError struct {
	// Op is what the caller was trying to do, e.g. "read the Agent Hub catalog".
	Op string
	// Path is the route the daemon does not have.
	Path string
	// Alternative is an optional way through in the meantime. Callers that have
	// one set it; the message reads fine without.
	Alternative string
}

func (e *RouteMissingError) Error() string {
	alternative := ""
	if e.Alternative != "" {
		alternative = " " + e.Alternative + "."
	}
	// Diagnosis first, and in the first few words: the hub renders this in a
	// one-row status bar that truncates at the terminal width, and "it is your
	// background service, not the Agent Hub" is the part that must survive.
	return fmt.Sprintf(
		"the GAIA background service is older than this GAIA: it has no %s route, so it "+
			"cannot %s. %s%s The daemon log is at %s.",
		e.Path, e.Op, UpgradeCoreHint, alternative, logPathForMessage())
}

// IsRouteMissing reports whether a daemon answer describes a route the daemon
// does not have, rather than one that answered with its own refusal.
//
// The discriminator is the detail: a route that exists explains itself (the
// install route's 404 names the agent and the missing sidecar spec), while an
// absent one gets the web framework's bare "Not Found".
func IsRouteMissing(path string, status int, detail string) bool {
	if status != http.StatusNotFound || !strings.HasPrefix(path, APIPrefix) {
		return false
	}
	switch strings.ToLower(strings.TrimSpace(detail)) {
	case "", "not found", "404 not found", "404: not found":
		return true
	}
	return false
}

// RequestError is a transport or protocol failure talking to a live daemon.
// Op names the call ("ensure the 'email' sidecar", "stream the 'email' query").
type RequestError struct {
	Op     string
	Detail string
}

func (e *RequestError) Error() string {
	return fmt.Sprintf(
		"could not %s: %s. Check `gaia daemon status` and the daemon log at %s.",
		e.Op, e.Detail, logPathForMessage())
}
