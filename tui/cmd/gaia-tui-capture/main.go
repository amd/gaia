// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Command gaia-tui-capture turns a driven TUI session into evidence a person
// can look at: stills, and a GIF that plays back what happened.
//
// The control API already renders the current frame as SVG and the whole
// history as one animated SVG (GET /control/v1/screen?format=svg and
// /recording). This exists because an animated SVG does not play in every
// viewer — several render only the first frame — so a bug report built on one
// is a bug report nobody can watch. GIF plays everywhere.
//
//	# every frame the session kept, as its own SVG
//	curl -sH "Authorization: Bearer $TOK" "$BASE/control/v1/frames?limit=200" \
//	  | gaia-tui-capture stills -out ./frames
//
//	# rasterise them with whatever the platform has, then:
//	gaia-tui-capture gif -in ./frames -out session.gif
//
// The raster step is deliberately left to the platform (macOS: `qlmanage -t -s
// 1340 -o . f*.svg`; Linux: `rsvg-convert`): shipping a font and a rasteriser
// to turn a debug capture into a picture is a lot of dependency for a tool that
// runs on a developer's machine, beside tools that already do it well.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"image"
	"image/color"
	"image/gif"
	"image/png"
	"io"
	"os"
	"path/filepath"
	"sort"

	"github.com/amd/gaia/tui/internal/control"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	switch os.Args[1] {
	case "stills":
		stills(os.Args[2:])
	case "gif":
		makeGIF(os.Args[2:])
	default:
		usage()
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: gaia-tui-capture stills -out DIR   (reads /frames JSON on stdin)")
	fmt.Fprintln(os.Stderr, "       gaia-tui-capture gif -in DIR -out FILE.gif")
	os.Exit(2)
}

// framesPayload is the shape GET /control/v1/frames returns. A bare array is
// accepted too, so a caller can pipe a filtered list straight in.
type framesPayload struct {
	Frames []control.Frame `json:"frames"`
}

func stills(args []string) {
	fs := flag.NewFlagSet("stills", flag.ExitOnError)
	out := fs.String("out", ".", "directory to write the SVG stills into")
	cols := fs.Int("cols", 0, "terminal width; 0 measures it from the frames")
	rows := fs.Int("rows", 0, "terminal height; 0 measures it from the frames")
	_ = fs.Parse(args)

	raw, err := io.ReadAll(os.Stdin)
	check(err)

	var payload framesPayload
	if err := json.Unmarshal(raw, &payload); err != nil || payload.Frames == nil {
		// A bare array, rather than the endpoint's envelope.
		if err := json.Unmarshal(raw, &payload.Frames); err != nil {
			check(fmt.Errorf("input is neither a /frames response nor an array of frames: %w", err))
		}
	}
	if len(payload.Frames) == 0 {
		check(fmt.Errorf("no frames on stdin — drive the TUI first"))
	}
	check(os.MkdirAll(*out, 0o755))

	for i, f := range payload.Frames {
		// The styled frame where the ring kept one: a still built from the
		// stripped text is a grey wash that looks nothing like the terminal.
		body := f.Raw
		if body == "" {
			body = f.Screen
		}
		doc := control.ScreenSVG(body, *cols, *rows)
		p := filepath.Join(*out, fmt.Sprintf("f%04d.svg", i))
		check(os.WriteFile(p, []byte(doc), 0o644))
	}
	fmt.Printf("wrote %d stills to %s\n", len(payload.Frames), *out)
}

func makeGIF(args []string) {
	fs := flag.NewFlagSet("gif", flag.ExitOnError)
	in := fs.String("in", ".", "directory of rasterised PNG frames")
	out := fs.String("out", "session.gif", "GIF to write")
	delay := fs.Int("delay", 60, "hundredths of a second per frame")
	hold := fs.Int("hold", 250, "hundredths of a second to hold the last frame")
	_ = fs.Parse(args)

	files, err := filepath.Glob(filepath.Join(*in, "*.png"))
	check(err)
	sort.Strings(files)
	if len(files) == 0 {
		check(fmt.Errorf("no PNGs in %s — rasterise the stills first", *in))
	}

	var srcs []image.Image
	counts := map[color.RGBA]int{}
	for _, f := range files {
		fh, err := os.Open(f)
		check(err)
		src, err := png.Decode(fh)
		fh.Close()
		check(err)
		srcs = append(srcs, src)
		b := src.Bounds()
		for y := b.Min.Y; y < b.Max.Y; y++ {
			for x := b.Min.X; x < b.Max.X; x++ {
				r, g, bl, _ := src.At(x, y).RGBA()
				counts[color.RGBA{uint8(r >> 8), uint8(g >> 8), uint8(bl >> 8), 255}]++
			}
		}
	}

	// Terminal output is a handful of flat colours, so an exact palette taken
	// from the frames themselves usually fits inside GIF's 256 — and then the
	// encode is lossless. That matters: the point of the clip is that someone
	// can READ it, and the usual fallback (a fixed palette plus dithering)
	// scatters the antialiased edge of every glyph.
	type cc struct {
		c color.RGBA
		n int
	}
	all := make([]cc, 0, len(counts))
	for c, n := range counts {
		all = append(all, cc{c, n})
	}
	sort.Slice(all, func(i, j int) bool { return all[i].n > all[j].n })
	pal := make(color.Palette, 0, 256)
	for i := 0; i < len(all) && i < 256; i++ {
		pal = append(pal, all[i].c)
	}
	if len(all) > 256 {
		fmt.Fprintf(os.Stderr,
			"note: %d distinct colours quantised to 256 — text may soften slightly\n", len(all))
	}

	g := &gif.GIF{}
	for _, src := range srcs {
		b := src.Bounds()
		dst := image.NewPaletted(b, pal)
		for y := b.Min.Y; y < b.Max.Y; y++ {
			for x := b.Min.X; x < b.Max.X; x++ {
				dst.Set(x, y, src.At(x, y))
			}
		}
		g.Image = append(g.Image, dst)
		g.Delay = append(g.Delay, *delay)
	}
	g.Delay[len(g.Delay)-1] = *hold

	fh, err := os.Create(*out)
	check(err)
	defer fh.Close()
	check(gif.EncodeAll(fh, g))
	fmt.Printf("wrote %s: %d frames, %d colours\n", *out, len(g.Image), len(pal))
}

func check(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "gaia-tui-capture:", err)
		os.Exit(1)
	}
}
