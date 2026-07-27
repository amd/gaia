package control

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// EnvHome overrides the directory holding control.json. Tests set it so a run
// never clobbers the user's real TUI registration, and so concurrent tests stay
// isolated. Read on every call — never cached — so a subprocess spawned with a
// different environment resolves its own directory.
const EnvHome = "GAIA_TUI_HOME"

// Info is the discovery record persisted to control.json.
type Info struct {
	PID        int     `json:"pid"`
	Port       int     `json:"port"`
	Token      string  `json:"token"`
	Host       string  `json:"host"`
	Service    string  `json:"service"`
	APIVersion string  `json:"api_version"`
	StartedAt  float64 `json:"started_at"`
	Version    string  `json:"version"`
}

// Dir returns the directory holding control.json.
func Dir() (string, error) {
	if override := os.Getenv(EnvHome); override != "" {
		return override, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("cannot locate the home directory for %s: %w (set %s to choose one explicitly)", filepath.Join("~", ".gaia", "tui"), err, EnvHome)
	}
	return filepath.Join(home, ".gaia", "tui"), nil
}

// FilePath returns the full path of the discovery file.
func FilePath() (string, error) {
	dir, err := Dir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "control.json"), nil
}

// WriteInfo persists info to control.json at mode 0600.
//
// Written to a uniquely named temp file in the same directory, fsynced, then
// renamed over the target, so a crash mid-write can never leave a half-written
// (and therefore trusted) file. The temp file is created O_EXCL at 0600 so the
// token is never briefly world-readable.
func WriteInfo(info Info) error {
	path, err := FilePath()
	if err != nil {
		return err
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("cannot create %s: %w", dir, err)
	}

	payload, err := json.MarshalIndent(info, "", "  ")
	if err != nil {
		return fmt.Errorf("cannot encode the control discovery record: %w", err)
	}

	tmp := filepath.Join(dir, fmt.Sprintf(".control.json.%d.tmp", os.Getpid()))
	// Clear a leftover temp from a prior crashed writer with the same pid, so
	// the O_EXCL create below always makes a fresh file at 0600 rather than
	// failing (or, without O_EXCL, inheriting the old file's permissions).
	if err := os.Remove(tmp); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("cannot clear the stale temp file %s: %w", tmp, err)
	}
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_EXCL|os.O_TRUNC, 0o600)
	if err != nil {
		return fmt.Errorf("cannot create %s: %w", tmp, err)
	}
	if _, err := f.Write(payload); err != nil {
		f.Close()
		os.Remove(tmp)
		return fmt.Errorf("cannot write %s: %w", tmp, err)
	}
	if err := f.Sync(); err != nil {
		f.Close()
		os.Remove(tmp)
		return fmt.Errorf("cannot flush %s: %w", tmp, err)
	}
	if err := f.Close(); err != nil {
		os.Remove(tmp)
		return fmt.Errorf("cannot close %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return fmt.Errorf("cannot install %s: %w", path, err)
	}
	// Rename preserves the temp's mode on POSIX; re-assert for platforms where
	// it is not carried across.
	if err := os.Chmod(path, 0o600); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("cannot restrict permissions on %s: %w", path, err)
	}
	return nil
}

// ReadInfo loads control.json. A missing file returns (nil, nil) — that is the
// normal "no TUI is running" answer, not an error.
func ReadInfo() (*Info, error) {
	path, err := FilePath()
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(path) // #nosec G304 -- path is derived from the user's home dir
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("cannot read %s: %w", path, err)
	}
	var info Info
	if err := json.Unmarshal(raw, &info); err != nil {
		return nil, fmt.Errorf("%w: %s: %v", ErrMalformed, path, err)
	}
	return &info, nil
}

// ErrMalformed marks a discovery file that exists but cannot be parsed. It is
// the only read failure that justifies deleting it — an unreadable file (a
// permission blip, EIO) may still belong to a running TUI.
var ErrMalformed = errors.New("the control discovery file is present but malformed")

// RemoveInfo deletes control.json, but only when it still records onlyPID — so
// a shutting-down TUI never clobbers the registration of a newer one that
// already reclaimed the slot.
func RemoveInfo(onlyPID int) error {
	path, err := FilePath()
	if err != nil {
		return err
	}
	info, err := ReadInfo()
	if err != nil {
		return err
	}
	if info == nil {
		return nil
	}
	if info.PID != onlyPID {
		return nil
	}
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("cannot remove %s: %w", path, err)
	}
	return nil
}

// ClearStale removes a discovery file whose owning process is gone — a TUI
// killed with SIGKILL leaves one that still advertises a pid, a port and a
// valid token. A file owned by a LIVE pid is left alone; it may be another
// running TUI's. alive is injected for tests; pass daemon.PIDAlive.
func ClearStale(alive func(int) bool) (bool, error) {
	info, err := ReadInfo()
	if errors.Is(err, ErrMalformed) {
		// Unparsable can never identify a running TUI, so it goes. Re-read
		// first: a TUI that registered since is writing valid JSON, and its
		// file must survive.
		if again, rerr := ReadInfo(); rerr == nil && again != nil {
			return false, nil
		}
		path, perr := FilePath()
		if perr != nil {
			return false, perr
		}
		if rmErr := os.Remove(path); rmErr != nil && !os.IsNotExist(rmErr) {
			return false, fmt.Errorf("cannot remove the malformed control file %s: %w (it could not be parsed: %v)", path, rmErr, err)
		}
		return true, nil
	}
	if err != nil {
		// Could not be READ (a permission blip, EIO). It may belong to a running
		// TUI, so it is reported, never deleted.
		return false, err
	}
	if info == nil {
		return false, nil
	}
	if info.PID == os.Getpid() || alive(info.PID) {
		return false, nil
	}
	// RemoveInfo re-reads and deletes only while the pid still matches, so a TUI
	// that registered between the read above and this call keeps its file.
	if err := RemoveInfo(info.PID); err != nil {
		return false, err
	}
	return true, nil
}

// newToken mints a 256-bit bearer token. Never logged, never printed.
func newToken() (string, error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("cannot generate a control auth token: %w", err)
	}
	return hex.EncodeToString(buf), nil
}
