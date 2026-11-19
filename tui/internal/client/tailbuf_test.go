package client

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/amd/gaia/tui/internal/event"
)

func TestTailBufferUnderLimitIsVerbatim(t *testing.T) {
	b := newTailBuffer(16)
	for _, s := range []string{"", "abc", "\n", "def"} {
		if n, err := b.Write([]byte(s)); n != len(s) || err != nil {
			t.Fatalf("Write(%q) = %d, %v", s, n, err)
		}
	}
	if got := b.String(); got != "abc\ndef" {
		t.Fatalf("got %q", got)
	}
}

func TestTailBufferExactlyAtLimitDropsNothing(t *testing.T) {
	b := newTailBuffer(8)
	b.Write([]byte("12345678"))
	if got := b.String(); got != "12345678" {
		t.Fatalf("got %q", got)
	}
}

func TestTailBufferKeepsTheLastLinesAndMarksTheDrop(t *testing.T) {
	b := newTailBuffer(32)
	for i := 0; i < 1000; i++ {
		fmt.Fprintf(b, "line %04d\n", i)
	}
	got := b.String()
	if !strings.HasPrefix(got, stderrDroppedMarker) {
		t.Fatalf("drop not marked: %q", got)
	}
	body := strings.TrimPrefix(got, stderrDroppedMarker)
	if len(body) > 32 {
		t.Fatalf("kept %d bytes, limit 32", len(body))
	}
	if !strings.HasSuffix(body, "line 0999\n") {
		t.Fatalf("last line lost: %q", body)
	}
	if !strings.HasPrefix(body, "line ") {
		t.Fatalf("tail should start on a whole line: %q", body)
	}
	if cap(b.buf) > 4*32 {
		t.Fatalf("backing array grew to %d bytes for a 32-byte limit", cap(b.buf))
	}
}

func TestTailBufferSingleWriteLargerThanLimit(t *testing.T) {
	b := newTailBuffer(10)
	b.Write([]byte(strings.Repeat("x", 100) + "END"))
	got := strings.TrimPrefix(b.String(), stderrDroppedMarker)
	if got != "xxxxxxxEND" {
		t.Fatalf("got %q", got)
	}
}

// A cut that lands exactly after a newline keeps the whole line that follows.
func TestTailBufferCleanCutKeepsTheFirstWholeLine(t *testing.T) {
	cases := map[string]struct {
		write func(b *tailBuffer)
		want  string
	}{
		// 30 bytes: trimmed in Write at 25 > 2*12, then cut again in String.
		"many writes": {func(b *tailBuffer) {
			for i := 0; i < 5; i++ {
				fmt.Fprintf(b, "line%d\n", i)
			}
		}, "line3\nline4\n"},
		"one write":    {func(b *tailBuffer) { b.Write([]byte("line0\nline1\nline2\n")) }, "line1\nline2\n"},
		"mid-line cut": {func(b *tailBuffer) { b.Write([]byte("line0\nline1\nline2\nxy")) }, "line2\nxy"},
	}
	for name, tc := range cases {
		b := newTailBuffer(12)
		tc.write(b)
		got := b.String()
		if !strings.HasPrefix(got, stderrDroppedMarker) {
			t.Errorf("%s: drop not marked: %q", name, got)
		}
		if body := strings.TrimPrefix(got, stderrDroppedMarker); body != tc.want {
			t.Errorf("%s: got %q, want %q", name, body, tc.want)
		}
	}
}

// A cut through a multi-byte rune must not hand the UI an invalid prefix.
func TestTailBufferDoesNotStartMidRune(t *testing.T) {
	b := newTailBuffer(7)
	b.Write([]byte(strings.Repeat("é", 20)))
	got := b.String()
	if !utf8.ValidString(got) {
		t.Fatalf("invalid UTF-8 tail: %q", got)
	}
	if !strings.HasSuffix(got, "é") {
		t.Fatalf("got %q", got)
	}
}

func TestTailBufferConcurrentWrites(t *testing.T) {
	b := newTailBuffer(64)
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 500; i++ {
				b.Write([]byte("0123456789\n"))
				_ = b.String()
			}
		}()
	}
	wg.Wait()
	if body := strings.TrimPrefix(b.String(), stderrDroppedMarker); len(body) > 64 {
		t.Fatalf("kept %d bytes, limit 64", len(body))
	}
}

const noisyExitAgentSrc = `package main

import (
	"bufio"
	"fmt"
	"os"
)

func main() {
	bufio.NewReader(os.Stdin).ReadString('\n')
	fmt.Fprintln(os.Stderr, "FIRST LINE")
	for i := 0; i < 20000; i++ {
		fmt.Fprintf(os.Stderr, "noise %05d ............................................\n", i)
	}
	fmt.Fprintln(os.Stderr, "Traceback: the real reason")
	os.Exit(1)
}
`

// A child that floods stderr before dying is reported with the END of its
// output, which is what explains the exit, and not with megabytes of it.
func TestSubprocessClient_ExitReportsBoundedStderrTail(t *testing.T) {
	isolateSubprocessConnection(t)
	old := subprocessLemonadePorts
	subprocessLemonadePorts = nil
	t.Cleanup(func() { subprocessLemonadePorts = old })

	bin := buildAgentFrom(t, t.TempDir(), "noisy_exit_agent", noisyExitAgentSrc)
	c := NewSubprocessClient(bin, nil, false)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	ch, err := c.Send(ctx, "hello")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	var msg string
	for evt := range ch {
		if ae, ok := evt.(event.AgentErrorEvent); ok {
			msg = ae.Content
		}
	}
	if msg == "" {
		t.Fatal("expected an AgentErrorEvent for the exit")
	}
	if !strings.Contains(msg, exitedOutputLabel+"\n"+stderrDroppedMarker) {
		t.Fatalf("dropped output is not marked: %.300q", msg)
	}
	if !strings.HasSuffix(strings.TrimRight(msg, "\r\n"), "Traceback: the real reason") {
		t.Fatalf("last stderr line missing: ...%q", msg[max(0, len(msg)-200):])
	}
	if strings.Contains(msg, "FIRST LINE") {
		t.Fatal("output from before the cap should have been dropped")
	}
	if len(msg) > agentStderrCap+1024 {
		t.Fatalf("error message is %d bytes, cap is %d", len(msg), agentStderrCap)
	}
}
