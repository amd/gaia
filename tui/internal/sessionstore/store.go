// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package sessionstore reads the listing the flagship agent keeps beside each
// session's transcript, so the TUI can show past sessions and reopen one.
//
// The agent owns every file here (gaia_agent/session_index.py). For a session
// <id> it keeps <id>.sqlite3 (what the model replays), <id>.json (title, kind,
// status, owning pid) and <id>.jsonl (one line per finished turn). The TUI
// only reads the last two and picks the <id> it passes back as --history-file.
// The layout is pinned by tests/fixtures/stdio/gaia_stdio_wire.json.
package sessionstore

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/control"
)

// IndexVersion is the only <id>.json layout this build reads.
const IndexVersion = 1

// KindChat is an interactive chat — the only kind listed by default. Eval and
// scheduled runs carry another kind and appear only when every kind is asked for.
const KindChat = "chat"

// Status is where a session stands, as the agent last wrote it, corrected for
// an owner that is no longer alive.
type Status string

const (
	StatusRunning Status = "running"
	// StatusWaiting is a turn parked on a permission prompt.
	StatusWaiting Status = "waiting"
	StatusDone    Status = "done"
	StatusFailed  Status = "failed"
)

// Session is one listed session.
type Session struct {
	ID        string
	Title     string
	Kind      string
	Status    Status
	CreatedAt time.Time
	UpdatedAt time.Time
	// PID is the agent process that last wrote the index; 0 once it exited cleanly.
	PID   int
	Turns int
	// Live is true while an agent holds the session's lock: it is open in some
	// window. The lock, not PID, decides — it dies with the process, and a
	// reused pid cannot fake it.
	Live bool
	// Interrupted means the index still said running or waiting when its agent
	// went away — the turn never finished. Status is then StatusFailed.
	Interrupted bool
}

// Turn is one finished exchange, as <id>.jsonl recorded it.
type Turn struct {
	User   string `json:"user"`
	Answer string `json:"answer,omitempty"`
	Error  string `json:"error,omitempty"`
}

type indexFile struct {
	Version   int     `json:"version"`
	ID        string  `json:"id"`
	Title     string  `json:"title"`
	Kind      string  `json:"kind"`
	Status    string  `json:"status"`
	CreatedAt float64 `json:"created_at"`
	UpdatedAt float64 `json:"updated_at"`
	PID       int     `json:"pid"`
	Turns     int     `json:"turns"`
}

var idPattern = regexp.MustCompile(`^[0-9a-f]{8,64}$`)

// Dir is where the agent keeps session transcripts: <TUI home>/history.
func Dir() (string, error) {
	home, err := control.Dir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, "history"), nil
}

// ValidID reports whether id is a stem the agent lists and this package reads.
func ValidID(id string) bool { return idPattern.MatchString(id) }

// NewID mints a session id the agent accepts as a file stem.
func NewID() (string, error) {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("cannot mint a session id: %w", err)
	}
	return hex.EncodeToString(buf), nil
}

// HistoryPath is the transcript the agent is launched with for session id.
func HistoryPath(dir, id string) string {
	return filepath.Join(dir, id+".sqlite3")
}

// LockPath is the file the agent holding session id keeps locked.
func LockPath(dir, id string) string {
	return filepath.Join(dir, id+".lock")
}

// Held reports whether another process holds a lock file. daemon.FileHeld is
// the real one; tests pass a fake.
type Held func(lockPath string) (bool, error)

// ListOptions narrows List.
type ListOptions struct {
	// AllKinds lists eval, scheduled and every other kind too, not just chats.
	AllKinds bool
	// Held tells an open session from a finished one. Required.
	Held Held
}

// Listing is List's answer: the sessions it could read, and one line for each
// index it could not, so a damaged file is reported rather than skipped.
type Listing struct {
	Sessions []Session
	Problems []string
}

// List reads every session index in dir. Sessions waiting on the user come
// first, then running ones, then the rest, newest first within each group.
// Sessions with no finished turn are not listed: there is nothing to reopen.
// A dir that does not exist yet is an empty listing.
func List(dir string, opts ListOptions) (Listing, error) {
	if opts.Held == nil {
		return Listing{}, errors.New("sessionstore.List needs a Held func to tell open sessions from finished ones")
	}
	entries, err := os.ReadDir(dir)
	if errors.Is(err, os.ErrNotExist) {
		return Listing{}, nil
	}
	if err != nil {
		return Listing{}, fmt.Errorf("cannot list sessions in %s: %w", dir, err)
	}
	var out Listing
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || filepath.Ext(name) != ".json" || !idPattern.MatchString(strings.TrimSuffix(name, ".json")) {
			continue
		}
		s, err := readIndex(filepath.Join(dir, name), opts.Held)
		if err != nil {
			out.Problems = append(out.Problems, err.Error())
			continue
		}
		if s.Turns == 0 && s.Status != StatusRunning && s.Status != StatusWaiting {
			continue
		}
		if !opts.AllKinds && s.Kind != KindChat {
			continue
		}
		out.Sessions = append(out.Sessions, s)
	}
	sort.SliceStable(out.Sessions, func(i, j int) bool {
		a, b := out.Sessions[i], out.Sessions[j]
		if ra, rb := rank(a.Status), rank(b.Status); ra != rb {
			return ra < rb
		}
		if !a.UpdatedAt.Equal(b.UpdatedAt) {
			return a.UpdatedAt.After(b.UpdatedAt)
		}
		return a.ID < b.ID
	})
	return out, nil
}

func rank(s Status) int {
	switch s {
	case StatusWaiting:
		return 0
	case StatusRunning:
		return 1
	}
	return 2
}

// Get reads one session by id, or by a unique prefix of one.
func Get(dir, idOrPrefix string, held Held) (Session, error) {
	all, err := List(dir, ListOptions{AllKinds: true, Held: held})
	if err != nil {
		return Session{}, err
	}
	var hits []Session
	for _, s := range all.Sessions {
		if s.ID == idOrPrefix {
			return s, nil
		}
		if strings.HasPrefix(s.ID, idOrPrefix) {
			hits = append(hits, s)
		}
	}
	switch len(hits) {
	case 1:
		return hits[0], nil
	case 0:
		return Session{}, fmt.Errorf("no session %q in %s — run /sessions (or --resume with no id) to see the ones there are", idOrPrefix, dir)
	}
	return Session{}, fmt.Errorf("%q matches %d sessions — give more of the id", idOrPrefix, len(hits))
}

func readIndex(path string, held Held) (Session, error) {
	raw, err := os.ReadFile(path) // #nosec G304 -- a file inside the TUI's own history dir
	if err != nil {
		return Session{}, fmt.Errorf("cannot read session index %s: %w", path, err)
	}
	var f indexFile
	if err := json.Unmarshal(raw, &f); err != nil {
		return Session{}, fmt.Errorf("session index %s is not valid JSON (%v) — delete it to drop the session from the list; its transcript is kept", path, err)
	}
	if f.Version != IndexVersion {
		return Session{}, fmt.Errorf("session index %s has layout version %d; this GAIA reads %d — update GAIA", path, f.Version, IndexVersion)
	}
	if want := strings.TrimSuffix(filepath.Base(path), ".json"); f.ID != want {
		return Session{}, fmt.Errorf("session index %s names session %q, not %q — delete it to drop the session from the list", path, f.ID, want)
	}
	s := Session{
		ID:        f.ID,
		Title:     f.Title,
		Kind:      f.Kind,
		Status:    Status(f.Status),
		CreatedAt: unixTime(f.CreatedAt),
		UpdatedAt: unixTime(f.UpdatedAt),
		PID:       f.PID,
		Turns:     f.Turns,
	}
	switch s.Status {
	case StatusRunning, StatusWaiting, StatusDone, StatusFailed:
	default:
		return Session{}, fmt.Errorf("session index %s has unknown status %q — update GAIA", path, f.Status)
	}
	live, err := held(strings.TrimSuffix(path, ".json") + ".lock")
	if err != nil {
		return Session{}, fmt.Errorf("cannot tell whether session %s is open: %w", f.ID, err)
	}
	s.Live = live
	if !s.Live && (s.Status == StatusRunning || s.Status == StatusWaiting) {
		s.Status = StatusFailed
		s.Interrupted = true
	}
	return s, nil
}

func unixTime(sec float64) time.Time {
	return time.Unix(0, int64(sec*float64(time.Second)))
}

// Transcript reads the finished turns of session id, oldest first.
func Transcript(dir, id string) ([]Turn, error) {
	path := filepath.Join(dir, id+".jsonl")
	f, err := os.Open(path) // #nosec G304 -- a file inside the TUI's own history dir
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("cannot read the transcript of session %s: %w", id, err)
	}
	defer f.Close()
	var turns []Turn
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 64*1024*1024)
	for n := 1; sc.Scan(); n++ {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var t Turn
		if err := json.Unmarshal([]byte(line), &t); err != nil {
			return nil, fmt.Errorf("transcript %s line %d is not valid JSON: %w", path, n, err)
		}
		turns = append(turns, t)
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("cannot read the transcript %s: %w", path, err)
	}
	return turns, nil
}

// Age is how long ago t was, in the fewest characters that still read clearly.
func Age(now, t time.Time) string {
	d := now.Sub(t)
	switch {
	case d < time.Minute:
		return "just now"
	case d < time.Hour:
		return fmt.Sprintf("%dm ago", int(d/time.Minute))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh ago", int(d/time.Hour))
	case d < 30*24*time.Hour:
		return fmt.Sprintf("%dd ago", int(d/(24*time.Hour)))
	}
	return t.Local().Format("Jan 2")
}
