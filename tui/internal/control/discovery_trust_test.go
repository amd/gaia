package control

import (
	"encoding/json"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// writeRecord plants a discovery file the way a previous TUI would have.
func writeRecord(t *testing.T, pid, port int) string {
	t.Helper()
	path, err := FilePath()
	if err != nil {
		t.Fatalf("FilePath: %v", err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	raw, err := json.Marshal(Info{
		PID: pid, Port: port, Token: "deadbeef", Host: Host,
		Service: ServiceID, APIVersion: APIVersion,
		StartedAt: float64(time.Now().Unix()),
	})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	return path
}

func fileExists(t *testing.T, path string) bool {
	t.Helper()
	_, err := os.Stat(path)
	if err == nil {
		return true
	}
	if os.IsNotExist(err) {
		return false
	}
	t.Fatalf("stat %s: %v", path, err)
	return false
}

// A TUI that was killed leaves a file that still reads as live: a recorded pid,
// a recorded port, a valid token. A client then authenticates against a port the
// OS has since handed to something else. The next TUI start must sweep it.
func TestClearStaleRemovesADeadRecord(t *testing.T) {
	t.Setenv(EnvHome, t.TempDir())
	path := writeRecord(t, 424242, 8775)

	removed, err := ClearStale(func(int) bool { return false })
	if err != nil {
		t.Fatalf("ClearStale: %v", err)
	}
	if !removed {
		t.Error("ClearStale reported nothing removed for a dead record")
	}
	if fileExists(t, path) {
		t.Error("a dead TUI's discovery file survived the sweep")
	}
}

// The other half of the same rule: a file whose process is still alive belongs
// to a TUI that can still be driven, so stealing it would be the same lie in
// reverse.
func TestClearStaleKeepsALiveRecord(t *testing.T) {
	t.Setenv(EnvHome, t.TempDir())
	path := writeRecord(t, 424242, 8775)

	removed, err := ClearStale(func(int) bool { return true })
	if err != nil {
		t.Fatalf("ClearStale: %v", err)
	}
	if removed {
		t.Error("ClearStale removed the registration of a running TUI")
	}
	if !fileExists(t, path) {
		t.Error("a live TUI's discovery file was deleted")
	}
}

// A record that cannot be parsed can never identify a running TUI, so it must
// not be left sitting where a client will try to read it.
func TestClearStaleRemovesAMalformedRecord(t *testing.T) {
	t.Setenv(EnvHome, t.TempDir())
	path, err := FilePath()
	if err != nil {
		t.Fatalf("FilePath: %v", err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}

	removed, err := ClearStale(func(int) bool { return true })
	if err != nil {
		t.Fatalf("ClearStale: %v", err)
	}
	if !removed || fileExists(t, path) {
		t.Error("a malformed discovery file was left in place")
	}
}

// Nothing to sweep is not an error — that is the normal first-run state.
func TestClearStaleWithNoFile(t *testing.T) {
	t.Setenv(EnvHome, t.TempDir())
	removed, err := ClearStale(func(int) bool { return false })
	if err != nil {
		t.Fatalf("ClearStale on a fresh machine: %v", err)
	}
	if removed {
		t.Error("ClearStale claims to have removed a file that never existed")
	}
}

// `kill <pid>`, a closing shell, a session teardown: the deferred Stop never
// runs, and the file left behind is what made a client talk to a dead port.
func TestTerminationSignalRemovesTheDiscoveryFile(t *testing.T) {
	srv, _ := newTestServer(t)
	path := srv.DiscoveryPath()
	if !fileExists(t, path) {
		t.Fatalf("the server did not publish %s", path)
	}

	quit := make(chan struct{})
	sigs := make(chan os.Signal, 1)
	go srv.WatchTermination(sigs, func() { close(quit) })
	sigs <- syscall.SIGTERM

	select {
	case <-quit:
	case <-time.After(3 * time.Second):
		t.Fatal("SIGTERM did not reach the program: nothing asked the TUI to quit")
	}
	if fileExists(t, path) {
		t.Error("SIGTERM left a discovery file that still advertises a pid, a port and a token")
	}
}

// The watcher must unwind when the caller closes the channel on a normal exit,
// rather than leaking a goroutine for the life of the process.
func TestWatchTerminationReturnsWhenTheChannelCloses(t *testing.T) {
	srv, _ := newTestServer(t)

	done := make(chan struct{})
	sigs := make(chan os.Signal, 1)
	go func() {
		srv.WatchTermination(sigs, func() { t.Error("quit was called without a signal") })
		close(done)
	}()
	close(sigs)

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("WatchTermination did not return when its channel closed")
	}
	if !fileExists(t, srv.DiscoveryPath()) {
		t.Error("closing the channel removed the discovery file of a still-running TUI")
	}
}
