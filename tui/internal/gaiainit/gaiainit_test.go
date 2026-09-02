package gaiainit

import (
	"fmt"
	"os"
	"testing"
	"time"
)

func TestHelperProcess(t *testing.T) {
	if os.Getenv("GAIA_TUI_INIT_TEST_HELPER") != "1" {
		return
	}
	for i := 0; i < 128; i++ {
		fmt.Fprintf(os.Stdout, "stdout line %d\n", i)
		fmt.Fprintf(os.Stderr, "stderr line %d\n", i)
	}
	select {}
}

func TestStartCancellationReapsChildAfterReadersAreFull(t *testing.T) {
	oldBinary := Binary
	Binary = func() (string, error) { return os.Args[0], nil }
	t.Cleanup(func() { Binary = oldBinary })
	t.Setenv("GAIA_TUI_INIT_TEST_HELPER", "1")

	ch, cancel, err := Start(false)
	if err != nil {
		t.Fatalf("Start: %v", err)
	}
	t.Cleanup(cancel)

	cancelled := false
	deadline := time.After(5 * time.Second)
	for {
		select {
		case evt, ok := <-ch:
			if !ok {
				if !cancelled {
					t.Fatal("init stream closed before cancellation")
				}
				return
			}
			if !evt.Done && !cancelled {
				cancel()
				cancelled = true
			}
		case <-deadline:
			t.Fatal("cancelled init did not close its event stream")
		}
	}
}
