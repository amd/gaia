// Package daemon is the TUI's thin client for the GAIA daemon: single-instance
// discovery, the start-or-attach handshake, and authenticated calls against the
// daemon's HTTP control plane (`/daemon/v1/*`) and agent relay (`/v1/<agent>/*`).
//
// It is the Go counterpart of src/gaia/daemon/{paths,instance,client}.py and
// deliberately mirrors their trust rules: a recorded instance is trusted only
// when its pid is alive AND a token-authed status probe answers with the daemon's
// service id and the recorded pid — a freed port that some unrelated process
// grabbed after a crash is a real failure mode, not a theoretical one.
//
// Every failure returns an actionable error naming what failed, what to run, and
// which log to read. There is no silent fallback to a direct sidecar connection:
// the TUI only ever holds the DAEMON client token, never a sidecar bearer.
package daemon

import (
	"fmt"
	"os"
	"path/filepath"
)

// EnvHome overrides the daemon state directory (tests, and any non-default
// install). Read on every call, never cached — a subprocess spawned with a
// different env must resolve its own directory.
const EnvHome = "GAIA_DAEMON_HOME"

// HostDir returns the directory holding instance.json, the start lock, and the
// daemon log.
func HostDir() (string, error) {
	if override := os.Getenv(EnvHome); override != "" {
		return override, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf(
			"cannot resolve the home directory to locate ~/.gaia/host: %w — "+
				"set %s to the daemon state directory and retry", err, EnvHome)
	}
	return filepath.Join(home, ".gaia", "host"), nil
}

// InstancePath returns the single-instance registry file (pid + port + client token).
func InstancePath() (string, error) {
	dir, err := HostDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "instance.json"), nil
}

// LockPath returns the advisory lock that serializes daemon start, so two
// concurrent callers spawn exactly one daemon.
func LockPath() (string, error) {
	dir, err := HostDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "instance.lock"), nil
}

// LogPath returns the daemon stdout/stderr log (what `gaia daemon logs` tails).
func LogPath() (string, error) {
	dir, err := HostDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "daemon.log"), nil
}

// logPathForMessage returns LogPath() for embedding in an error string, or a
// human placeholder if even the home directory is unresolvable.
func logPathForMessage() string {
	p, err := LogPath()
	if err != nil {
		return "~/.gaia/host/daemon.log"
	}
	return p
}

// ensureHostDir creates the host directory if missing and returns it.
func ensureHostDir() (string, error) {
	dir, err := HostDir()
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", fmt.Errorf("cannot create the daemon state directory %s: %w", dir, err)
	}
	return dir, nil
}
