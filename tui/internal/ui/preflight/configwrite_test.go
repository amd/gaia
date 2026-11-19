// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/filelock"
)

func useConfig(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "gaia", "config.json")
	if body != "" {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv(configFileEnv, path)
	return path
}

func readDoc(t *testing.T, path string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	doc := map[string]any{}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("config.json is not valid JSON: %v\n%s", err, raw)
	}
	return doc
}

func TestWriteConfigKeysConcurrentWritersLoseNothing(t *testing.T) {
	path := useConfig(t, `{"profile":"chat"}`)
	const writers = 24

	var wg sync.WaitGroup
	errs := make(chan error, writers)
	for i := 0; i < writers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, err := writeConfigKeys(map[string]any{fmt.Sprintf("key%d", i): i})
			errs <- err
		}(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}

	doc := readDoc(t, path)
	for i := 0; i < writers; i++ {
		if _, ok := doc[fmt.Sprintf("key%d", i)]; !ok {
			t.Errorf("key%d was lost by a concurrent write: %v", i, doc)
		}
	}
	if doc["profile"] != "chat" {
		t.Errorf("a key the Go side never wrote must survive: %v", doc)
	}
}

func TestWriteConfigKeysWaitsForTheLock(t *testing.T) {
	path := useConfig(t, `{"profile":"chat"}`)

	// Python's GaiaConfig.save_keys holds this same lock file around its write.
	held, err := filelock.Acquire(path, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Release()

	done := make(chan error, 1)
	go func() {
		_, err := writeConfigKeys(map[string]any{"last_model": "m"})
		done <- err
	}()

	select {
	case err := <-done:
		t.Fatalf("writeConfigKeys finished while another process held the lock (err=%v)", err)
	case <-time.After(200 * time.Millisecond):
	}
	if _, ok := readDoc(t, path)["last_model"]; ok {
		t.Fatal("the config was written while the lock was held")
	}

	held.Release()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("writeConfigKeys never finished after the lock was released")
	}
	if readDoc(t, path)["last_model"] != "m" {
		t.Error("the write did not land once the lock was free")
	}
}

func TestWriteConfigKeysLeavesNoTempFiles(t *testing.T) {
	path := useConfig(t, "")
	for i := 0; i < 5; i++ {
		if _, err := writeConfigKeys(map[string]any{"n": i}); err != nil {
			t.Fatal(err)
		}
	}
	entries, err := os.ReadDir(filepath.Dir(path))
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if e.Name() != "config.json" && e.Name() != "config.json.lock" {
			t.Errorf("stray file left beside config.json: %s", e.Name())
		}
	}
	if readDoc(t, path)["n"] != float64(4) {
		t.Error("the last write must win")
	}
}

func TestWriteConfigKeysKeepsOwnerOnlyMode(t *testing.T) {
	path := useConfig(t, "")
	if _, err := writeConfigKeys(map[string]any{"a": 1}); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got&0o077 != 0 && runtime.GOOS != "windows" {
		t.Errorf("config.json must stay owner-only, got %v", got)
	}
}

func TestWriteConfigKeysRefusesCorruptFileAndLeavesItAlone(t *testing.T) {
	path := useConfig(t, `{not json`)
	if _, err := writeConfigKeys(map[string]any{"a": 1}); err == nil {
		t.Fatal("a corrupt config.json must not be overwritten")
	}
	raw, _ := os.ReadFile(path)
	if string(raw) != `{not json` {
		t.Errorf("corrupt file was modified: %q", raw)
	}
}
