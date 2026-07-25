package daemon

import "fmt"

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

// StaleError means instance.json exists but must not be trusted: it is corrupt,
// its pid is dead, or the recorded port answers as something other than our
// daemon. Reason names which check failed.
type StaleError struct {
	Path   string
	Reason string
}

func (e *StaleError) Error() string {
	return fmt.Sprintf(
		"the recorded GAIA daemon at %s cannot be trusted: %s. "+
			"Reclaim it with `gaia daemon restart`, then retry; "+
			"the daemon log is at %s.", e.Path, e.Reason, logPathForMessage())
}

// VersionError means the running daemon speaks a contract this client cannot
// use — a MAJOR skew, or a MINOR below the agents/relay floor. The remedy is
// always a daemon restart: an app update replaced the client while the old
// daemon kept running.
type VersionError struct {
	Have   string
	Reason string
}

func (e *VersionError) Error() string {
	return fmt.Sprintf(
		"the running GAIA daemon speaks host API v%s: %s. "+
			"Run `gaia daemon restart`, then retry.", e.Have, e.Reason)
}

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
