package components

import (
	"os"
	"strings"
	"testing"
)

// resetRenderer puts the package globals back after a test has moved them.
func resetRenderer(t *testing.T) {
	t.Helper()
	t.Cleanup(func() {
		mu.Lock()
		wordWrap = 100
		styleName = "dark"
		built = false
		mu.Unlock()
	})
}

// captureStdout runs fn with os.Stdout replaced, and returns what was written.
func captureStdout(t *testing.T, fn func()) string {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	orig := os.Stdout
	os.Stdout = w
	done := make(chan string, 1)
	go func() {
		var b strings.Builder
		buf := make([]byte, 4096)
		for {
			n, rerr := r.Read(buf)
			b.Write(buf[:n])
			if rerr != nil {
				break
			}
		}
		done <- b.String()
	}()
	fn()
	os.Stdout = orig
	w.Close()
	out := <-done
	r.Close()
	return out
}

// The renderer must never ask the terminal anything after the program starts.
//
// glamour's auto style queries the terminal's background colour (OSC 11) and
// reads the answer off stdin. Rebuilt lazily — on the first markdown render, or
// on the rebuild a window resize triggers — Bubble Tea already owns the input
// reader, so the terminal's reply was typed into the focused input and the user
// watched `11;rgb:1e1e/1e1e/1e1e` appear in the chat prompt.
//
// The property that prevents it: the style is resolved once, up front, and every
// later rebuild uses the resolved name.
func TestRendererNeverQueriesTheTerminalAfterPriming(t *testing.T) {
	resetRenderer(t)
	t.Setenv(EnvStyle, "dark")
	if err := PrimeRenderer(); err != nil {
		t.Fatalf("PrimeRenderer: %v", err)
	}

	// A resize rebuilds the renderer — the exact path that re-queried before.
	written := captureStdout(t, func() {
		SetWordWrap(60)
		if got := RenderMarkdown("# hi"); got == "" {
			t.Error("a rebuilt renderer produced nothing")
		}
	})

	if strings.Contains(written, "\x1b]11;") || strings.Contains(written, "]11;?") {
		t.Fatalf("the renderer wrote a background-colour query while the program owns stdin: %q", written)
	}
}

// GLAMOUR_STYLE is the escape hatch for a terminal that never answers the
// query: it must be honoured verbatim rather than probed over.
func TestExplicitStyleSkipsTheTerminalQuery(t *testing.T) {
	resetRenderer(t)
	t.Setenv(EnvStyle, "notty")
	if got := detectStyle(); got != "notty" {
		t.Errorf("detectStyle() = %q with %s=notty, want notty", got, EnvStyle)
	}
}

// A renderer that cannot be built must say so — the caller prints it — and
// still return the text rather than nothing.
func TestUnbuildableRendererIsReportedAndFallsBackToPlainText(t *testing.T) {
	resetRenderer(t)
	// A directory is unusable as both a style name and a style file.
	t.Setenv(EnvStyle, t.TempDir())
	err := PrimeRenderer()
	if err == nil {
		t.Fatal("an unknown style built successfully")
	}
	if !strings.Contains(err.Error(), "markdown renderer") {
		t.Errorf("the error does not say what could not be built: %v", err)
	}
	if got := RenderMarkdown("# hi"); got != "# hi" {
		t.Errorf("RenderMarkdown = %q, want the raw text back", got)
	}
}

func TestRenderMarkdownRendersWithAResolvedStyle(t *testing.T) {
	resetRenderer(t)
	t.Setenv(EnvStyle, "dark")
	if err := PrimeRenderer(); err != nil {
		t.Fatalf("PrimeRenderer: %v", err)
	}
	out := RenderMarkdown("**bold**")
	if !strings.Contains(out, "bold") {
		t.Errorf("rendered output lost the content: %q", out)
	}
}
