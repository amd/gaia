// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestConcurrentWritesKeepEveryKey(t *testing.T) {
	withHome(t)
	var wg sync.WaitGroup
	errs := make(chan error, 20)
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			errs <- writeKey(fmt.Sprintf("k%d", i), i)
		}(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	path, _ := ConfigPath()
	raw, err := readRaw(path)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 20; i++ {
		if _, ok := raw[fmt.Sprintf("k%d", i)]; !ok {
			t.Errorf("k%d was lost by a concurrent write: %v", i, raw)
		}
	}
	left, _ := filepath.Glob(filepath.Join(filepath.Dir(path), "update-config*"))
	if len(left) != 1 {
		t.Errorf("temp or lock files left behind: %v", left)
	}
}

func TestStaleLockIsTakenOver(t *testing.T) {
	withHome(t)
	path, _ := ConfigPath()
	os.MkdirAll(filepath.Dir(path), 0o755)
	lock := path + ".lock"
	os.WriteFile(lock, nil, 0o644)
	old := time.Now().Add(-time.Minute)
	os.Chtimes(lock, old, old)
	if err := SetAutoUpdate(true); err != nil {
		t.Fatalf("a crashed app's lock blocked the save: %v", err)
	}
}

func TestHeldLockFailsLoudly(t *testing.T) {
	withHome(t)
	path, _ := ConfigPath()
	os.MkdirAll(filepath.Dir(path), 0o755)
	os.WriteFile(path+".lock", nil, 0o644)
	err := SetAutoUpdate(true)
	if err == nil || !strings.Contains(err.Error(), "busy") {
		t.Fatalf("want a busy error while another app holds the lock, got %v", err)
	}
}

func TestReadOnlySettingsFolderReadsAndFailsSavesPlainly(t *testing.T) {
	if runtime.GOOS == "windows" || os.Geteuid() == 0 {
		t.Skip("needs a POSIX folder that refuses new files")
	}
	withHome(t)
	path, _ := ConfigPath()
	dir := filepath.Dir(path)
	os.MkdirAll(dir, 0o755)
	os.WriteFile(path, []byte(`{"autoUpdate": true}`), 0o644)
	os.Chmod(dir, 0o555)
	t.Cleanup(func() { os.Chmod(dir, 0o755) })

	start := time.Now()
	cfg, err := ReadConfig()
	if err != nil || !cfg.AutoUpdate {
		t.Fatalf("a read-only folder must still read the settings: %+v, %v", cfg, err)
	}
	err = SetAutoUpdate(false)
	if err == nil || strings.Contains(err.Error(), "busy") || !strings.Contains(err.Error(), "permissions") {
		t.Fatalf("want a permissions error, not busy, got %v", err)
	}
	if time.Since(start) > lockWait {
		t.Errorf("waited %v for a lock that can never be taken", time.Since(start))
	}
}
