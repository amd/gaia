package test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A cheap net under a large deletion.
//
// Every screen-facing file under internal/ui/ is grepped for the hub's
// vocabulary. A partial deletion — a tab label left in a renderer, an "i
// install" hint in a footer, a vote prompt — compiles perfectly and only shows
// up when someone launches the TUI and reads it. This fails the build instead.
//
// Deliberately source-level rather than screen-level: a rendered-frame check can
// only see the screens a test happens to visit.
func TestNoScreenSourceStillSpeaksHub(t *testing.T) {
	banned := []string{
		"Coming Soon",
		"Available (",
		"Installed (",
		"Agent Hub",
		"vote",
		"Vote",
	}

	root := filepath.Join(repoTUIDir(t), "internal", "ui")
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		raw, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		text := string(raw)
		for _, phrase := range banned {
			if strings.Contains(text, phrase) {
				rel, _ := filepath.Rel(root, path)
				t.Errorf("internal/ui/%s still contains %q — the hub is gone, so this is either "+
					"dead text or a screen that still talks about it", filepath.ToSlash(rel), phrase)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walking %s: %v", root, err)
	}
}
