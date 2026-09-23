package client

import (
	"bufio"
	"fmt"
	"os"
	"testing"
	"time"
)

func TestSubprocessCloseHelper(t *testing.T) {
	if len(os.Args) < 3 || os.Args[len(os.Args)-2] != "--close-helper" {
		return
	}
	mode := os.Args[len(os.Args)-1]
	scanner := bufio.NewScanner(os.Stdin)
	if !scanner.Scan() {
		os.Exit(1)
	}
	fmt.Println(`{"type":"answer","content":"ok"}`)
	for scanner.Scan() {
	}
	if mode == "ignore-eof" {
		time.Sleep(time.Hour)
	}
	os.Exit(0)
}

func TestSubprocessClient_CloseAfterCompletedTurn(t *testing.T) {
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1/api/v1")
	for _, mode := range []string{"exit-on-eof", "ignore-eof"} {
		t.Run(mode, func(t *testing.T) {
			c := NewSubprocessClient(exe, []string{"-test.run=^TestSubprocessCloseHelper$", "--", "--close-helper", mode}, false)
			runTurn(t, c, "hello")
			proc := c.proc
			t.Cleanup(func() { _ = proc.kill(); proc.reap() })
			closed := make(chan error, 1)
			go func() { closed <- c.Close() }()
			select {
			case err := <-closed:
				if err != nil {
					t.Fatal(err)
				}
			case <-time.After(3 * closeGrace):
				t.Fatal("Close did not stop the completed turn's child")
			}
			if proc.cmd.ProcessState == nil {
				t.Fatal("Close returned without waiting for the child to exit")
			}
			if mode == "exit-on-eof" && !proc.cmd.ProcessState.Success() {
				t.Fatal("cooperative child was killed instead of exiting on stdin EOF")
			}
			if err := c.Close(); err != nil {
				t.Fatal(err)
			}
		})
	}
}
