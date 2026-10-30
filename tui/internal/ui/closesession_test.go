package ui

import (
	"bytes"
	"errors"
	"strings"
	"testing"
	"time"
)

type closerFunc func() error

func (f closerFunc) Close() error { return f() }

// A quit that lingers while the agent saves memories must say so, or it looks
// hung; a quick quit must print nothing.
func TestCloseSessionExplainsOnlyASlowQuit(t *testing.T) {
	var out bytes.Buffer
	if err := closeSession(closerFunc(func() error { return nil }), &out, time.Second); err != nil {
		t.Fatal(err)
	}
	if out.Len() != 0 {
		t.Fatalf("a quick quit printed %q", out.String())
	}

	want := errors.New("agent process did not exit after termination")
	err := closeSession(closerFunc(func() error {
		time.Sleep(200 * time.Millisecond)
		return want
	}), &out, 20*time.Millisecond)
	if !errors.Is(err, want) {
		t.Fatalf("the close error was lost: %v", err)
	}
	if !strings.Contains(out.String(), savingMemoriesNotice) {
		t.Fatalf("a slow quit gave no feedback: %q", out.String())
	}
}
