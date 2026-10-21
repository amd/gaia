// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package sessionstore

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

var epoch = time.Date(2026, 10, 15, 14, 0, 0, 0, time.UTC)

func writeIndex(t *testing.T, dir string, f indexFile) {
	t.Helper()
	if f.Version == 0 {
		f.Version = IndexVersion
	}
	if f.Kind == "" {
		f.Kind = KindChat
	}
	raw, err := json.Marshal(f)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, f.ID+".json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
}

func at(d time.Duration) float64 { return float64(epoch.Add(d).UnixNano()) / 1e9 }

func noneHeld(string) (bool, error) { return false, nil }

// heldIDs is a Held where exactly these sessions are open.
func heldIDs(ids ...string) Held {
	return func(lock string) (bool, error) {
		for _, id := range ids {
			if filepath.Base(lock) == id+".lock" {
				return true, nil
			}
		}
		return false, nil
	}
}

func ids(ss []Session) string {
	var out []string
	for _, s := range ss {
		out = append(out, s.ID)
	}
	return strings.Join(out, ",")
}

func TestWaitingSessionsComeFirstThenRunningThenNewest(t *testing.T) {
	dir := t.TempDir()
	writeIndex(t, dir, indexFile{ID: "aaaaaaaa", Status: "done", UpdatedAt: at(-time.Minute), Turns: 1})
	writeIndex(t, dir, indexFile{ID: "bbbbbbbb", Status: "done", UpdatedAt: at(-time.Hour), Turns: 1})
	writeIndex(t, dir, indexFile{ID: "cccccccc", Status: "waiting", PID: 7, UpdatedAt: at(-2 * time.Hour), Turns: 1})
	writeIndex(t, dir, indexFile{ID: "dddddddd", Status: "running", PID: 8, UpdatedAt: at(-3 * time.Hour), Turns: 0})
	writeIndex(t, dir, indexFile{ID: "eeeeeeee", Status: "failed", UpdatedAt: at(0), Turns: 1})

	got, err := List(dir, ListOptions{Held: heldIDs("cccccccc", "dddddddd")})
	if err != nil {
		t.Fatal(err)
	}
	if want := "cccccccc,dddddddd,eeeeeeee,aaaaaaaa,bbbbbbbb"; ids(got.Sessions) != want {
		t.Fatalf("order = %s, want %s", ids(got.Sessions), want)
	}
	if !got.Sessions[0].Live || got.Sessions[0].Status != StatusWaiting {
		t.Fatalf("waiting session = %+v", got.Sessions[0])
	}
}

func TestEvalAndScheduledRunsAreHiddenUnlessAllKindsAreAsked(t *testing.T) {
	dir := t.TempDir()
	writeIndex(t, dir, indexFile{ID: "aaaaaaaa", Kind: "chat", Status: "done", Turns: 1})
	writeIndex(t, dir, indexFile{ID: "bbbbbbbb", Kind: "eval", Status: "done", Turns: 1})
	writeIndex(t, dir, indexFile{ID: "cccccccc", Kind: "scheduled", Status: "waiting", PID: 3, Turns: 1})

	held := heldIDs("aaaaaaaa", "bbbbbbbb", "cccccccc")
	def, err := List(dir, ListOptions{Held: held})
	if err != nil {
		t.Fatal(err)
	}
	if ids(def.Sessions) != "aaaaaaaa" {
		t.Fatalf("default list = %s, want only the chat", ids(def.Sessions))
	}
	all, err := List(dir, ListOptions{AllKinds: true, Held: held})
	if err != nil {
		t.Fatal(err)
	}
	if ids(all.Sessions) != "cccccccc,aaaaaaaa,bbbbbbbb" {
		t.Fatalf("all kinds = %s", ids(all.Sessions))
	}
}

func TestATurnWhoseAgentDiedIsShownAsFailed(t *testing.T) {
	dir := t.TempDir()
	writeIndex(t, dir, indexFile{ID: "aaaaaaaa", Status: "waiting", PID: 99, Turns: 2})
	got, err := List(dir, ListOptions{Held: noneHeld})
	if err != nil {
		t.Fatal(err)
	}
	s := got.Sessions[0]
	if s.Status != StatusFailed || !s.Interrupted || s.Live {
		t.Fatalf("session = %+v, want failed + interrupted", s)
	}
}

func TestAnEmptySessionIsNotListed(t *testing.T) {
	dir := t.TempDir()
	writeIndex(t, dir, indexFile{ID: "aaaaaaaa", Status: "done", Turns: 0})
	got, err := List(dir, ListOptions{Held: noneHeld})
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Sessions) != 0 {
		t.Fatalf("listed %v", got.Sessions)
	}
}

func TestADamagedIndexIsReportedNotSkipped(t *testing.T) {
	dir := t.TempDir()
	writeIndex(t, dir, indexFile{ID: "aaaaaaaa", Status: "done", Turns: 1})
	if err := os.WriteFile(filepath.Join(dir, "bbbbbbbb.json"), []byte("{"), 0o600); err != nil {
		t.Fatal(err)
	}
	writeIndex(t, dir, indexFile{ID: "cccccccc", Version: 9, Status: "done", Turns: 1})
	writeIndex(t, dir, indexFile{ID: "dddddddd", Status: "paused", Turns: 1})
	// Not an index: the agent's temp files and unrelated names are ignored.
	for _, name := range []string{".aaaaaaaa.json.x.tmp", "notes.json", "aaaaaaaa.jsonl"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("{"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	got, err := List(dir, ListOptions{Held: noneHeld})
	if err != nil {
		t.Fatal(err)
	}
	if ids(got.Sessions) != "aaaaaaaa" {
		t.Fatalf("sessions = %s", ids(got.Sessions))
	}
	joined := strings.Join(got.Problems, "\n")
	for _, want := range []string{"bbbbbbbb.json is not valid JSON", "layout version 9", `unknown status "paused"`} {
		if !strings.Contains(joined, want) {
			t.Errorf("problems lack %q:\n%s", want, joined)
		}
	}
}

func TestAMissingDirIsAnEmptyListing(t *testing.T) {
	got, err := List(filepath.Join(t.TempDir(), "none"), ListOptions{Held: noneHeld})
	if err != nil || len(got.Sessions) != 0 || len(got.Problems) != 0 {
		t.Fatalf("got %+v, %v", got, err)
	}
}

func TestListRefusesWithoutALivenessCheck(t *testing.T) {
	if _, err := List(t.TempDir(), ListOptions{}); err == nil {
		t.Fatal("List without Held must refuse rather than guess")
	}
}

// A pid left behind by a killed agent, now reused by some other process, must
// not lock the session out: only the agent's lock says a session is open.
func TestAStalePidDoesNotMakeASessionOpen(t *testing.T) {
	dir := t.TempDir()
	writeIndex(t, dir, indexFile{ID: "aaaaaaaa", Status: "done", PID: os.Getpid(), Turns: 1})
	got, err := List(dir, ListOptions{Held: noneHeld})
	if err != nil {
		t.Fatal(err)
	}
	if got.Sessions[0].Live {
		t.Fatal("a live pid with no lock read as an open session")
	}
}

func TestALockThatCannotBeTestedIsReported(t *testing.T) {
	dir := t.TempDir()
	writeIndex(t, dir, indexFile{ID: "aaaaaaaa", Status: "done", Turns: 1})
	broken := func(string) (bool, error) { return false, fmt.Errorf("access denied") }
	got, err := List(dir, ListOptions{Held: broken})
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Sessions) != 0 || len(got.Problems) != 1 || !strings.Contains(got.Problems[0], "access denied") {
		t.Fatalf("got %+v", got)
	}
}

func TestGetResolvesAUniquePrefix(t *testing.T) {
	dir := t.TempDir()
	writeIndex(t, dir, indexFile{ID: "abc12345", Status: "done", Turns: 1})
	writeIndex(t, dir, indexFile{ID: "abd12345", Status: "done", Turns: 1})
	if s, err := Get(dir, "abc", noneHeld); err != nil || s.ID != "abc12345" {
		t.Fatalf("Get(abc) = %+v, %v", s, err)
	}
	if _, err := Get(dir, "ab", noneHeld); err == nil || !strings.Contains(err.Error(), "matches 2 sessions") {
		t.Fatalf("ambiguous prefix: %v", err)
	}
	if _, err := Get(dir, "ffff", noneHeld); err == nil || !strings.Contains(err.Error(), "no session") {
		t.Fatalf("unknown id: %v", err)
	}
}

func TestTranscriptReadsTurnsInOrder(t *testing.T) {
	dir := t.TempDir()
	body := `{"user":"hi","answer":"hello"}` + "\n\n" + `{"user":"run it","error":"Lemonade is not running"}` + "\n"
	if err := os.WriteFile(filepath.Join(dir, "aaaaaaaa.jsonl"), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := Transcript(dir, "aaaaaaaa")
	if err != nil {
		t.Fatal(err)
	}
	want := []Turn{{User: "hi", Answer: "hello"}, {User: "run it", Error: "Lemonade is not running"}}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("got %+v", got)
	}
	if err := os.WriteFile(filepath.Join(dir, "bbbbbbbb.jsonl"), []byte("{\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Transcript(dir, "bbbbbbbb"); err == nil || !strings.Contains(err.Error(), "line 1") {
		t.Fatalf("damaged transcript: %v", err)
	}
}

func TestNewIDIsAValidStem(t *testing.T) {
	id, err := NewID()
	if err != nil {
		t.Fatal(err)
	}
	if !idPattern.MatchString(id) {
		t.Fatalf("id %q would not be listed", id)
	}
}

func TestAge(t *testing.T) {
	for d, want := range map[time.Duration]string{
		10 * time.Second: "just now",
		5 * time.Minute:  "5m ago",
		3 * time.Hour:    "3h ago",
		50 * time.Hour:   "2d ago",
	} {
		if got := Age(epoch, epoch.Add(-d)); got != want {
			t.Errorf("Age(-%v) = %q, want %q", d, got, want)
		}
	}
}

// The index is written by gaia_agent/session_index.py; both sides read the
// layout from the same fixture.
func TestIndexLayoutMatchesTheWireContract(t *testing.T) {
	path := filepath.Join("..", "..", "..", "tests", "fixtures", "stdio", "gaia_stdio_wire.json")
	raw, err := os.ReadFile(path) // #nosec G304 -- fixed relative path to a checked-in fixture
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		SessionIndex struct {
			Version          int      `json:"version"`
			DefaultKind      string   `json:"default_kind"`
			Statuses         []string `json:"statuses"`
			MetaFields       []string `json:"meta_fields"`
			TranscriptFields []string `json:"transcript_fields"`
		} `json:"session_index"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	c := fixture.SessionIndex
	if c.Version != IndexVersion || c.DefaultKind != KindChat {
		t.Fatalf("contract version %d kind %q; reader %d %q", c.Version, c.DefaultKind, IndexVersion, KindChat)
	}
	if got := fmt.Sprint(c.Statuses); got != fmt.Sprint([]Status{StatusRunning, StatusWaiting, StatusDone, StatusFailed}) {
		t.Fatalf("statuses %s", got)
	}
	if got, want := jsonTags(indexFile{}), sorted(c.MetaFields); got != want {
		t.Fatalf("index fields %s, contract %s", got, want)
	}
	if got, want := jsonTags(Turn{}), sorted(c.TranscriptFields); got != want {
		t.Fatalf("transcript fields %s, contract %s", got, want)
	}
}

func jsonTags(v any) string {
	t := reflect.TypeOf(v)
	var keys []string
	for i := 0; i < t.NumField(); i++ {
		keys = append(keys, strings.Split(t.Field(i).Tag.Get("json"), ",")[0])
	}
	return sorted(keys)
}

func sorted(s []string) string {
	c := append([]string(nil), s...)
	sort.Strings(c)
	return strings.Join(c, ",")
}
