package components

import (
	"fmt"
	"os"
	"sync"

	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/glamour/styles"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/term"
)

var (
	mu          sync.Mutex
	renderer    *glamour.TermRenderer
	rendererErr error
	built       bool
	wordWrap    = 100
	// styleName is resolved once, by PrimeRenderer, and never re-detected.
	styleName = styles.DarkStyle
)

// PrimeRenderer resolves the style and builds the renderer BEFORE Bubble Tea
// owns stdin. Call it once per process, from the launch path.
//
// glamour's auto style queries the terminal for its background colour and reads
// the reply off stdin; done lazily, Bubble Tea consumes that reply and types it
// into the focused input. An error means plain text, so the caller can say so.
func PrimeRenderer() error {
	mu.Lock()
	defer mu.Unlock()
	styleName = detectStyle()
	built = false
	return buildLocked()
}

// EnvStyle names a glamour style explicitly and skips the terminal query
// entirely. It is glamour's own variable, so a user who already sets it for
// other charm tools gets the same result here.
const EnvStyle = "GLAMOUR_STYLE"

// detectStyle asks the terminal for its background colour, once. A terminal
// that never answers costs termenv's 5s OSC timeout here instead of mid-session;
// GLAMOUR_STYLE skips the query entirely.
func detectStyle() string {
	if explicit := os.Getenv(EnvStyle); explicit != "" {
		return explicit
	}
	// Nothing is watching a pipe or a file, and a query written into one is
	// noise nobody will ever answer.
	if !term.IsTerminal(os.Stdout.Fd()) {
		return styles.NoTTYStyle
	}
	if lipgloss.HasDarkBackground() {
		return styles.DarkStyle
	}
	return styles.LightStyle
}

// buildLocked constructs the renderer from the already-resolved style. It does
// no terminal I/O, so it is safe at any point in the program's life.
func buildLocked() error {
	if built {
		return rendererErr
	}
	built = true
	// WithStylePath, not WithStandardStyle: it resolves a builtin name the same
	// way but also accepts a style FILE, which is what GLAMOUR_STYLE usually
	// holds. Neither queries the terminal once the name is concrete.
	renderer, rendererErr = glamour.NewTermRenderer(
		glamour.WithStylePath(styleName),
		glamour.WithWordWrap(wordWrap),
	)
	if rendererErr != nil {
		renderer = nil
		rendererErr = fmt.Errorf("cannot build the %s markdown renderer: %w", styleName, rendererErr)
	}
	return rendererErr
}

// SetWordWrap re-wraps subsequent renders. It rebuilds the renderer, which is
// why the style must already be resolved — see PrimeRenderer.
func SetWordWrap(width int) {
	mu.Lock()
	defer mu.Unlock()
	if width == wordWrap {
		return
	}
	wordWrap = width
	built = false
}

// RenderMarkdown renders content, returning the raw text when the renderer
// could not be built (PrimeRenderer reported that at startup) or when this one
// document cannot be rendered.
func RenderMarkdown(content string) string {
	mu.Lock()
	defer mu.Unlock()
	if err := buildLocked(); err != nil || renderer == nil || content == "" {
		return content
	}
	out, err := renderer.Render(content)
	if err != nil {
		return content
	}
	return out
}
