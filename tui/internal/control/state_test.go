package control

import (
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/charmbracelet/lipgloss"
)

func TestPlainScreenStripsANSI(t *testing.T) {
	styled := lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("150")).Render("Agent Hub")
	if !strings.Contains(styled, "\x1b[") {
		t.Skip("lipgloss produced no escape sequences in this environment; nothing to strip")
	}
	plain := PlainScreen(styled)
	if plain != "Agent Hub" {
		t.Errorf("PlainScreen(%q) = %q, want %q", styled, plain, "Agent Hub")
	}
	if strings.Contains(plain, "\x1b") {
		t.Errorf("PlainScreen left an escape sequence in %q", plain)
	}
}

func TestPlainScreenTrimsPaddingAndTrailingBlanks(t *testing.T) {
	raw := "Installed   \nBash        \n\n\n"
	want := "Installed\nBash"
	if got := PlainScreen(raw); got != want {
		t.Errorf("PlainScreen(%q) = %q, want %q", raw, got, want)
	}
}

func TestPlainScreenKeepsAnsiFormatSeparate(t *testing.T) {
	raw := "\x1b[1mbold\x1b[0m"
	if got := PlainScreen(raw); got != "bold" {
		t.Errorf("PlainScreen = %q, want %q", got, "bold")
	}
}

func TestRecordFrameDedupesAndSequences(t *testing.T) {
	st := NewState(nil)
	st.recordFrame("one")
	st.recordFrame("one")
	st.recordFrame("two")

	raw, seq, _ := st.Current()
	if raw != "two" {
		t.Errorf("Current screen = %q, want %q", raw, "two")
	}
	if seq != 2 {
		t.Errorf("seq = %d, want 2 (identical consecutive frames must not advance it)", seq)
	}
	frames, latest, _ := st.Frames(0, 10)
	if len(frames) != 2 || latest != 2 {
		t.Fatalf("Frames returned %d frames (latest %d), want 2 (latest 2)", len(frames), latest)
	}
	if frames[0].Screen != "one" || frames[1].Screen != "two" {
		t.Errorf("frames = %+v, want [one two] in order", frames)
	}
}

func TestFramesSinceAndLimit(t *testing.T) {
	st := NewState(nil)
	for i := 0; i < 10; i++ {
		st.recordFrame(fmt.Sprintf("frame %d", i))
	}
	frames, latest, truncated := st.Frames(7, 20)
	if latest != 10 {
		t.Errorf("latest_seq = %d, want 10", latest)
	}
	if len(frames) != 3 {
		t.Fatalf("since=7 returned %d frames, want 3", len(frames))
	}
	if frames[0].Seq != 8 {
		t.Errorf("first frame seq = %d, want 8", frames[0].Seq)
	}
	if truncated {
		t.Error("truncated should be false when nothing was dropped")
	}

	limited, _, truncated := st.Frames(0, 2)
	if len(limited) != 2 || !truncated {
		t.Errorf("limit=2 returned %d frames (truncated %v), want 2 and truncated=true", len(limited), truncated)
	}
}

func TestFrameRingIsBounded(t *testing.T) {
	st := NewState(nil)
	for i := 0; i < maxFrames+50; i++ {
		st.recordFrame(fmt.Sprintf("frame %d", i))
	}
	frames, latest, truncated := st.Frames(0, 0)
	if len(frames) != maxFrames {
		t.Errorf("ring holds %d frames, want %d", len(frames), maxFrames)
	}
	if latest != maxFrames+50 {
		t.Errorf("latest_seq = %d, want %d", latest, maxFrames+50)
	}
	if !truncated {
		t.Error("truncated should be true once the ring has dropped frames")
	}
}

// TestFrameCacheUnderConcurrentAccess is the real shape of production: Bubble
// Tea writes frames from its event loop while HTTP handlers read. Run with
// -race to make a missing lock fail.
func TestFrameCacheUnderConcurrentAccess(t *testing.T) {
	st := NewState(nil)
	var wg sync.WaitGroup
	stop := make(chan struct{})

	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; ; i++ {
			select {
			case <-stop:
				return
			default:
			}
			st.recordFrame(fmt.Sprintf("frame %d", i))
			st.setSnapshot(Snapshot{View: "hub", HubTabIndex: i % 3})
			st.SetSize(80+i%40, 24)
		}
	}()

	for r := 0; r < 4; r++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			deadline := time.Now().Add(200 * time.Millisecond)
			for time.Now().Before(deadline) {
				raw, seq, snap := st.Current()
				_ = PlainScreen(raw)
				_ = seq
				_ = snap.View
				st.Frames(0, 5)
				st.Size()
				st.UptimeMS()
			}
		}()
	}

	time.Sleep(200 * time.Millisecond)
	close(stop)
	wg.Wait()

	if _, seq, _ := st.Current(); seq == 0 {
		t.Error("no frames were recorded")
	}
}

func TestChangedBroadcastsOnNewFrame(t *testing.T) {
	st := NewState(nil)
	ch := st.Changed()
	select {
	case <-ch:
		t.Fatal("Changed() fired before anything changed")
	default:
	}

	st.recordFrame("hello")
	select {
	case <-ch:
	case <-time.After(time.Second):
		t.Fatal("Changed() did not fire after a new frame")
	}
}

func TestChangedBroadcastsOnSnapshotChangeOnly(t *testing.T) {
	st := NewState(nil)
	st.setSnapshot(Snapshot{View: "hub"})

	ch := st.Changed()
	st.setSnapshot(Snapshot{View: "hub"}) // identical — must not wake waiters
	select {
	case <-ch:
		t.Fatal("an identical snapshot woke waiters")
	default:
	}

	st.setSnapshot(Snapshot{View: "chat", Agent: "email"})
	select {
	case <-ch:
	case <-time.After(time.Second):
		t.Fatal("a state change with no repaint did not wake waiters — POST /wait on a state matcher would hang")
	}
}
