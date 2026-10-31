package cards

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestMediaCardShowsPathAndMetadata(t *testing.T) {
	data, _ := json.Marshal(map[string]any{
		"kind": "media", "path": `C:\\Users\\me\\gaia\\image.png`,
		"media_type": "image", "size_bytes": 2_097_152,
		"model": "SD-Turbo", "size": "512x512",
	})
	out := plain(Render("media", data, width80))
	if !strings.Contains(out, "Generated image") || !strings.Contains(out, `C:\\Users\\me`) ||
		!strings.Contains(out, "2.0 MB") || !strings.Contains(out, "512x512") {
		t.Fatalf("media card lost metadata:\n%s", out)
	}
	assertWidth(t, out, width80)
}

func TestMediaCardRejectsMissingPath(t *testing.T) {
	out := plain(Render("media", json.RawMessage(`{"kind":"media","media_type":"audio"}`), width80))
	assertContains(t, out, "Invalid media payload", "path and media_type are required")
}
