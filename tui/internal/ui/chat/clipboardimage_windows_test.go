// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"bytes"
	"encoding/binary"
	"image"
	"image/color"
	"image/png"
	"testing"
)

// buildDIBHeader writes a bitmapHeader in the exact wire layout
// decodeDIBToPNG expects, so these tests exercise the real parsing rather
// than a hand-simplified stand-in for it.
func buildDIBHeader(t *testing.T, h bitmapHeader) []byte {
	t.Helper()
	var buf bytes.Buffer
	if err := binary.Write(&buf, binary.LittleEndian, h); err != nil {
		t.Fatalf("could not build a synthetic DIB header: %v", err)
	}
	return buf.Bytes()
}

func decodePNG(t *testing.T, data []byte) image.Image {
	t.Helper()
	img, err := png.Decode(bytes.NewReader(data))
	if err != nil {
		t.Fatalf("decodeDIBToPNG produced an unreadable PNG: %v", err)
	}
	return img
}

// assertPixel compares against the UNMULTIPLIED components — color.Color's
// own RGBA() method returns alpha-premultiplied values, which would make a
// correctly-decoded translucent pixel look wrong here (e.g. R=128 at A=127
// premultiplies to ~64). NRGBAModel.Convert reverses that.
func assertPixel(t *testing.T, img image.Image, x, y int, want color.NRGBA) {
	t.Helper()
	got := color.NRGBAModel.Convert(img.At(x, y)).(color.NRGBA)
	if got != want {
		t.Errorf("pixel (%d,%d) = %+v, want %+v", x, y, got, want)
	}
}

// A 24-bit BI_RGB DIB (Win+Shift+S and Paint both produce this shape for a
// flattened screenshot) round-trips through decodeDIBToPNG pixel-for-pixel,
// including the bottom-up row order every uncompressed BMP/DIB uses.
func Test24BitBIRGBDecodesCorrectly(t *testing.T) {
	header := buildDIBHeader(t, bitmapHeader{
		Size: 40, Width: 2, Height: 2, Planes: 1, BitCount: 24, Compression: biRGB,
	})
	// Bottom-up storage: the FIRST row in the file is the BOTTOM of the
	// image. Row stride is DWORD-aligned: 2px * 3B = 6B, padded to 8B.
	fileRow0 := []byte{ // bottom scanline (image y=1): blue, white
		255, 0, 0, // B,G,R = blue (B=255,G=0,R=0)
		255, 255, 255, // B,G,R = white
		0, 0, // row padding
	}
	fileRow1 := []byte{ // top scanline (image y=0): red, green
		0, 0, 255, // B,G,R = red
		0, 255, 0, // B,G,R = green
		0, 0, // row padding
	}
	raw := append(append(header, fileRow0...), fileRow1...)

	png, err := decodeDIBToPNG(raw)
	if err != nil {
		t.Fatalf("decodeDIBToPNG: %v", err)
	}
	img := decodePNG(t, png)

	assertPixel(t, img, 0, 0, color.NRGBA{255, 0, 0, 255}) // red
	assertPixel(t, img, 1, 0, color.NRGBA{0, 255, 0, 255}) // green
	assertPixel(t, img, 0, 1, color.NRGBA{0, 0, 255, 255}) // blue
	assertPixel(t, img, 1, 1, color.NRGBA{255, 255, 255, 255})
}

// A 32-bit BI_BITFIELDS DIB with an explicit alpha mask (the shape a
// BITMAPV5HEADER screenshot commonly uses) decodes using those masks, alpha
// included, when at least one pixel actually uses it.
func Test32BitBitFieldsWithRealAlphaDecodesCorrectly(t *testing.T) {
	header := buildDIBHeader(t, bitmapHeader{
		Size: 124, Width: 1, Height: -1, // top-down (negative height)
		Planes: 1, BitCount: 32, Compression: biBitFields,
	})
	// V5 header: masks live INSIDE the 124-byte header at fixed offsets
	// 40/44/48/52 — pad out to 124 bytes total, then one BGRA-masked pixel.
	full := make([]byte, 124)
	copy(full, header)
	binary.LittleEndian.PutUint32(full[40:44], 0x00FF0000) // red mask
	binary.LittleEndian.PutUint32(full[44:48], 0x0000FF00) // green mask
	binary.LittleEndian.PutUint32(full[48:52], 0x000000FF) // blue mask
	binary.LittleEndian.PutUint32(full[52:56], 0xFF000000) // alpha mask
	pixel := []byte{0x40, 0x00, 0x80, 0x7F}                // B=0x40 G=0x00 R=0x80 A=0x7F
	raw := append(full, pixel...)

	png, err := decodeDIBToPNG(raw)
	if err != nil {
		t.Fatalf("decodeDIBToPNG: %v", err)
	}
	img := decodePNG(t, png)
	assertPixel(t, img, 0, 0, color.NRGBA{0x80, 0x00, 0x40, 0x7F})
}

// The well-known Windows clipboard quirk this decoder specifically guards
// against: a 32-bit DIB that nominally HAS an alpha mask but every pixel's
// alpha byte is 0 (several apps never populate it) must be treated as fully
// opaque, not fully transparent — the literal bytes would otherwise hand the
// agent an invisible PNG.
func Test32BitAllZeroAlphaIsTreatedAsOpaque(t *testing.T) {
	header := buildDIBHeader(t, bitmapHeader{
		Size: 124, Width: 1, Height: -1, Planes: 1, BitCount: 32, Compression: biBitFields,
	})
	full := make([]byte, 124)
	copy(full, header)
	binary.LittleEndian.PutUint32(full[40:44], 0x00FF0000)
	binary.LittleEndian.PutUint32(full[44:48], 0x0000FF00)
	binary.LittleEndian.PutUint32(full[48:52], 0x000000FF)
	binary.LittleEndian.PutUint32(full[52:56], 0xFF000000)
	pixel := []byte{0x00, 0x00, 0xFF, 0x00} // red, alpha byte 0
	raw := append(full, pixel...)

	png, err := decodeDIBToPNG(raw)
	if err != nil {
		t.Fatalf("decodeDIBToPNG: %v", err)
	}
	img := decodePNG(t, png)
	assertPixel(t, img, 0, 0, color.NRGBA{255, 0, 0, 255}) // opaque, not invisible
}

func TestDecodeDIBRejectsUnsupportedBitDepth(t *testing.T) {
	header := buildDIBHeader(t, bitmapHeader{Size: 40, Width: 1, Height: 1, Planes: 1, BitCount: 8, Compression: biRGB})
	if _, err := decodeDIBToPNG(header); err == nil {
		t.Fatal("expected an error for an 8-bit (paletted) DIB, got nil")
	}
}

func TestDecodeDIBRejectsTruncatedData(t *testing.T) {
	header := buildDIBHeader(t, bitmapHeader{Size: 40, Width: 4, Height: 4, Planes: 1, BitCount: 24, Compression: biRGB})
	if _, err := decodeDIBToPNG(header); err == nil { // header only, no pixel data
		t.Fatal("expected an error for truncated pixel data, got nil")
	}
}

func TestDecodeDIBRejectsTooShortInput(t *testing.T) {
	if _, err := decodeDIBToPNG([]byte{1, 2, 3}); err == nil {
		t.Fatal("expected an error for data shorter than a header, got nil")
	}
}
