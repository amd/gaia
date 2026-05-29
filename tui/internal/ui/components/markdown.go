package components

import (
	"sync"

	"github.com/charmbracelet/glamour"
)

var (
	renderer     *glamour.TermRenderer
	rendererOnce sync.Once
	wordWrap     = 100
)

func initRenderer() {
	var err error
	renderer, err = glamour.NewTermRenderer(
		glamour.WithAutoStyle(),
		glamour.WithWordWrap(wordWrap),
	)
	if err != nil {
		renderer = nil
	}
}

func SetWordWrap(width int) {
	wordWrap = width
	rendererOnce = sync.Once{}
}

func RenderMarkdown(content string) string {
	rendererOnce.Do(initRenderer)
	if renderer == nil || content == "" {
		return content
	}
	out, err := renderer.Render(content)
	if err != nil {
		return content
	}
	return out
}
