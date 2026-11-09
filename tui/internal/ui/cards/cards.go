// Package cards renders a `tool_result`'s typed `render` card into terminal
// text.
//
// The `/query` SSE contract (docs/spec/agent-ui-query-sse-contract.md §4.2/§4.3)
// lets a sidecar declare how its result should be drawn with six generic
// primitives any agent can emit with no client work. §7 makes the receiving
// end's obligations explicit: an unknown key degrades to a visible generic card
// with a raw dump, and a payload that fails its schema says so. A card is never
// dropped and never blanks the message.
//
// Of the six primitives this package draws four. `image` and `audio` cannot be
// shown or played in a terminal, so they degrade to a caption, the saved path
// and (for audio) the length.
package cards

import (
	"encoding/json"
	"strings"
)

// maxCardRows bounds the card's interior. 22 interior rows plus two borders is
// 24 lines — an 80x24 terminal's whole screen. Beyond it a card stops being a
// card and becomes a scroll trap, so the overflow becomes "+N more" instead.
const maxCardRows = 22

// Render draws the card for a tool_result's `render` key at the given outer
// width. It always returns something visible: unknown keys and malformed
// payloads become explicit fallback cards rather than empty strings.
//
// width is the total width the card may occupy, borders included.
func Render(renderKey string, data json.RawMessage, width int) string {
	key := strings.TrimSpace(renderKey)
	if key == "" {
		return renderUnsupported("(none)", data, width)
	}
	switch key {
	case "table":
		return renderTable(data, width)
	case "key_value":
		return renderKeyValue(data, width)
	case "list":
		return renderList(data, width)
	case "image":
		return renderImage(data, width)
	case "audio":
		return renderAudio(data, width)
	case "diff":
		return renderDiff(data, width)
	default:
		return renderUnsupported(key, data, width)
	}
}
