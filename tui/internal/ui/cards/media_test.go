package cards

import "testing"

func TestAudioFallbackShowsPathAndLength(t *testing.T) {
	out := Render("audio", raw(t, `{"src":"/api/media/audio/music_20261101_1a2b.wav","caption":"calm piano loop","path":"/home/u/.gaia/cache/audio/generations/music_20261101_1a2b.wav","duration_seconds":95.4}`), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out, "[audio: calm piano loop]", "music_20261101_1a2b.wav", "Length: 1:35", "Not played")
	assertNotContains(t, out, "Unsupported card", "Invalid audio payload")
}

func TestAudioFallbackWithoutOptionalFields(t *testing.T) {
	out := Render("audio", raw(t, `{"src":"/api/media/audio/sfx.wav"}`), width80)
	assertContains(t, out, "[audio: untitled]", "Not played")
	assertNotContains(t, out, "Length:", "Saved:", "Invalid audio payload")

	zero := Render("audio", raw(t, `{"src":"/api/media/audio/sfx.wav","duration_seconds":0}`), width80)
	assertContains(t, zero, "Length: 0:00")
}

func TestAudioRejectsAnythingButAGeneratedFile(t *testing.T) {
	for _, src := range []string{
		"",
		"https://example.com/a.wav",
		"file:///etc/passwd",
		"/api/media/audio/",
		"/api/media/audio/../secret.wav",
		"/api/media/audio/sub/a.wav",
		"/api/media/audio/%2e%2e",
		"/api/media/audio/..%2Fsecret.wav",
		"/api/media/audio/..%5Csecret.wav",
		"/api/media/audio/C%3A%5Cx.wav",
		"/api/media/audio/a.wav%00",
		"/api/media/audio/%zz.wav",
		"/api/media/image/a.wav",
		"/api/files/uploads/a.wav",
	} {
		out := Render("audio", raw(t, `{"src":`+quote(src)+`}`), width80)
		assertContains(t, out, "Invalid audio payload")
	}
}

func TestAudioRejectsBadDuration(t *testing.T) {
	for _, d := range []string{`-1`, `"95"`} {
		out := Render("audio", raw(t, `{"src":"/api/media/audio/a.wav","duration_seconds":`+d+`}`), width80)
		assertContains(t, out, "Invalid audio payload")
	}
}

func TestGeneratedImageShowsCaptionAndPath(t *testing.T) {
	out := Render("image", raw(t, `{"src":"/api/media/image/sunset_SD-Turbo_1.png","alt":"sunset","caption":"sunset","path":"/home/u/.gaia/cache/sd/images/sunset_SD-Turbo_1.png"}`), width80)
	assertContains(t, out, "[image: sunset]", "sunset_SD-Turbo_1.png", "Not shown")
	assertNotContains(t, out, "Invalid image payload")

	// An image ref is only good for images: an audio ref is not one.
	bad := Render("image", raw(t, `{"src":"/api/media/audio/x.png"}`), width80)
	assertContains(t, bad, "Invalid image payload")
	trav := Render("image", raw(t, `{"src":"/api/media/image/..%2F..%2Fx.png"}`), width80)
	assertContains(t, trav, "Invalid image payload")
}
