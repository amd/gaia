// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"image"
	"image/png"
	"math/bits"
	"runtime"
	"syscall"
	"time"
	"unsafe"
)

// Direct Win32 clipboard calls, no cgo — the same approach clipboard.go's own
// doc comment describes for atotto/clipboard's text path, which this mirrors
// rather than reusing directly: atotto's procs are unexported, and its
// package only ever asks for CF_UNICODETEXT.
var (
	imgUser32                     = syscall.MustLoadDLL("user32")
	imgIsClipboardFormatAvailable = imgUser32.MustFindProc("IsClipboardFormatAvailable")
	imgOpenClipboard              = imgUser32.MustFindProc("OpenClipboard")
	imgCloseClipboard             = imgUser32.MustFindProc("CloseClipboard")
	imgGetClipboardData           = imgUser32.MustFindProc("GetClipboardData")
	imgRegisterClipboardFormatW   = imgUser32.MustFindProc("RegisterClipboardFormatW")

	imgKernel32     = syscall.NewLazyDLL("kernel32")
	imgGlobalLock   = imgKernel32.NewProc("GlobalLock")
	imgGlobalUnlock = imgKernel32.NewProc("GlobalUnlock")
	imgGlobalSize   = imgKernel32.NewProc("GlobalSize")
)

const (
	cfBitmap = 2
	cfDIB    = 8
	cfDIBV5  = 17
)

// waitOpenClipboardForImage mirrors atotto's own waitOpenClipboard
// (clipboard_windows.go) — the clipboard is a single, briefly-contended OS
// resource, and another process (or this one's own text-paste path) can hold
// it for a moment.
func waitOpenClipboardForImage() error {
	deadline := time.Now().Add(time.Second)
	var err error
	for time.Now().Before(deadline) {
		if r, _, e := imgOpenClipboard.Call(0); r != 0 {
			return nil
		} else {
			err = e
		}
		time.Sleep(time.Millisecond)
	}
	return err
}

// readClipboardImagePNG reports whether the Windows clipboard currently
// holds an image and, if so, its bytes as a real PNG file — decoding
// whatever raster format Windows actually stored (see decodeDIBToPNG) rather
// than assuming any one app puts a ready-made PNG there.
//
// ok is false whenever there is no image at all (CF_BITMAP/CF_DIB/CF_DIBV5
// all absent) — the normal case for a text copy, where the caller falls
// through to the existing clipboard.ReadAll() text path. ok is true with a
// non-nil err only when an image WAS detected but could not be turned into
// usable bytes — that must surface to the user rather than silently trying
// text next, which would paste something unrelated to the screenshot they
// just took.
func readClipboardImagePNG() (data []byte, ok bool, err error) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()

	pngFormat, _, _ := imgRegisterClipboardFormatW.Call(uintptr(unsafe.Pointer(utf16Ptr("PNG"))))

	hasPNG := pngFormat != 0 && formatAvailable(uintptr(pngFormat))
	hasDIBV5 := formatAvailable(cfDIBV5)
	hasDIB := formatAvailable(cfDIB)
	hasBitmap := formatAvailable(cfBitmap)
	if !hasPNG && !hasDIBV5 && !hasDIB && !hasBitmap {
		return nil, false, nil
	}

	if err := waitOpenClipboardForImage(); err != nil {
		return nil, true, fmt.Errorf("could not open the clipboard: %w", err)
	}
	defer imgCloseClipboard.Call()

	// Many apps that put a DIB on the clipboard (Snip & Sketch, browsers)
	// also register a ready-made "PNG" format alongside it — prefer that
	// when present so screenshots skip the raster decode below entirely.
	if hasPNG {
		if raw, ok := readClipboardHandle(uintptr(pngFormat)); ok {
			return raw, true, nil
		}
	}

	format := uintptr(cfDIBV5)
	if !hasDIBV5 {
		format = cfDIB
	}
	raw, ok := readClipboardHandle(format)
	if !ok {
		return nil, true, fmt.Errorf("the clipboard reports an image but its data could not be read")
	}
	data, err = decodeDIBToPNG(raw)
	if err != nil {
		return nil, true, err
	}
	return data, true, nil
}

func formatAvailable(format uintptr) bool {
	r, _, _ := imgIsClipboardFormatAvailable.Call(format)
	return r != 0
}

// readClipboardHandle copies out the bytes behind a clipboard handle for
// format. The clipboard must already be open.
func readClipboardHandle(format uintptr) ([]byte, bool) {
	h, _, _ := imgGetClipboardData.Call(format)
	if h == 0 {
		return nil, false
	}
	size, _, _ := imgGlobalSize.Call(h)
	if size == 0 {
		return nil, false
	}
	l, _, _ := imgGlobalLock.Call(h)
	if l == 0 {
		return nil, false
	}
	defer imgGlobalUnlock.Call(h)

	buf := make([]byte, size)
	// `uintptr(unsafe.Pointer(nil)) + l` is not pointer arithmetic — l is a
	// plain address GlobalLock handed back, unrelated to nil — it is the
	// shape go vet's unsafeptr check requires to accept a uintptr-to-Pointer
	// conversion at all (see unsafe.Pointer rule 3, "conversion of a Pointer
	// to a uintptr and back, with arithmetic"). The memory behind l is a
	// native Win32 heap block the Go GC never sees or moves, so the
	// conversion itself is safe regardless; this only satisfies the checker.
	p := unsafe.Pointer(uintptr(unsafe.Pointer(nil)) + l)
	copy(buf, unsafe.Slice((*byte)(p), size))
	return buf, true
}

func utf16Ptr(s string) *uint16 {
	p, err := syscall.UTF16PtrFromString(s)
	if err != nil {
		// Every caller here passes a constant ASCII literal ("PNG") — this
		// can only fail on an embedded NUL, which none of them contain.
		panic(fmt.Sprintf("clipboardimage_windows: UTF16PtrFromString(%q): %v", s, err))
	}
	return p
}

// bitmapHeader is the leading 40 bytes shared by BITMAPINFOHEADER,
// BITMAPV4HEADER and BITMAPV5HEADER — every field this decoder needs. A
// CF_DIB handle's data begins with this header directly (no BITMAPFILEHEADER
// — that 14-byte prefix belongs to a .bmp FILE, not the clipboard blob);
// CF_DIBV5 begins with the same layout, just followed by more fields this
// decoder does not need.
type bitmapHeader struct {
	Size          uint32
	Width         int32
	Height        int32
	Planes        uint16
	BitCount      uint16
	Compression   uint32
	SizeImage     uint32
	XPelsPerMeter int32
	YPelsPerMeter int32
	ClrUsed       uint32
	ClrImportant  uint32
}

const (
	biRGB       = 0
	biBitFields = 3
)

// decodeDIBToPNG turns raw CF_DIB/CF_DIBV5 bytes into a PNG file. Kept as a
// pure function of bytes-in/bytes-out (no clipboard access) so it is unit
// testable with a synthetic DIB — see clipboardimage_windows_test.go — unlike
// readClipboardImagePNG, which needs a real Windows clipboard.
//
// Supports the shapes GDI-based screenshot tools (Win+Shift+S/Snip & Sketch,
// Paint, browser "copy image") actually produce: 24-bit BI_RGB, and 32-bit
// either BI_RGB or BI_BITFIELDS. Anything else (paletted, 16-bit, RLE
// compression) returns an actionable error rather than a corrupted image.
func decodeDIBToPNG(raw []byte) ([]byte, error) {
	if len(raw) < 40 {
		return nil, fmt.Errorf("clipboard image data is too short to be a bitmap (%d bytes)", len(raw))
	}
	var h bitmapHeader
	if err := binary.Read(bytes.NewReader(raw[:40]), binary.LittleEndian, &h); err != nil {
		return nil, fmt.Errorf("could not read the clipboard bitmap header: %w", err)
	}

	width := int(h.Width)
	height := int(h.Height)
	topDown := height < 0
	if topDown {
		height = -height
	}
	if width <= 0 || height <= 0 {
		return nil, fmt.Errorf("clipboard bitmap has an invalid size (%dx%d)", width, height)
	}
	if h.BitCount != 24 && h.BitCount != 32 {
		return nil, fmt.Errorf("clipboard bitmap is %d-bit — only 24-bit and 32-bit are supported", h.BitCount)
	}
	if h.Compression != biRGB && h.Compression != biBitFields {
		return nil, fmt.Errorf("clipboard bitmap uses an unsupported compression (%d)", h.Compression)
	}

	redMask, greenMask, blueMask, alphaMask := uint32(0x00FF0000), uint32(0x0000FF00), uint32(0x000000FF), uint32(0)
	pixelOffset := int(h.Size)
	if h.Compression == biBitFields {
		if h.Size <= 40 {
			// Classic BITMAPINFOHEADER (40 bytes): the three DWORD masks
			// follow it directly, and pixel data follows THOSE.
			if len(raw) < 52 {
				return nil, fmt.Errorf("clipboard bitmap is missing its BI_BITFIELDS masks")
			}
			redMask = binary.LittleEndian.Uint32(raw[40:44])
			greenMask = binary.LittleEndian.Uint32(raw[44:48])
			blueMask = binary.LittleEndian.Uint32(raw[48:52])
			pixelOffset = 52
		} else {
			// BITMAPV4HEADER/V5HEADER: the masks are fixed fields INSIDE the
			// (larger) header itself, at offsets 40/44/48/52.
			if len(raw) < 56 {
				return nil, fmt.Errorf("clipboard bitmap header is truncated")
			}
			redMask = binary.LittleEndian.Uint32(raw[40:44])
			greenMask = binary.LittleEndian.Uint32(raw[44:48])
			blueMask = binary.LittleEndian.Uint32(raw[48:52])
			alphaMask = binary.LittleEndian.Uint32(raw[52:56])
		}
	}

	rowSize := ((int(h.BitCount)*width + 31) / 32) * 4
	need := pixelOffset + rowSize*height
	if len(raw) < need {
		return nil, fmt.Errorf("clipboard bitmap data is truncated: have %d bytes, need %d", len(raw), need)
	}

	img := image.NewNRGBA(image.Rect(0, 0, width, height))
	anyAlpha := false
	for y := 0; y < height; y++ {
		srcRow := y
		if !topDown {
			srcRow = height - 1 - y
		}
		rowStart := pixelOffset + srcRow*rowSize
		for x := 0; x < width; x++ {
			var r, g, b, a byte
			if h.BitCount == 24 {
				px := raw[rowStart+x*3 : rowStart+x*3+3]
				b, g, r, a = px[0], px[1], px[2], 255
			} else {
				px := binary.LittleEndian.Uint32(raw[rowStart+x*4 : rowStart+x*4+4])
				r = extractChannel(px, redMask)
				g = extractChannel(px, greenMask)
				b = extractChannel(px, blueMask)
				if alphaMask != 0 {
					a = extractChannel(px, alphaMask)
					if a != 0 {
						anyAlpha = true
					}
				} else {
					a = 255
				}
			}
			o := img.PixOffset(x, y)
			img.Pix[o], img.Pix[o+1], img.Pix[o+2], img.Pix[o+3] = r, g, b, a
		}
	}

	// A well-known Windows clipboard quirk: several apps write a 32-bit DIB
	// with an alpha mask but leave every alpha byte 0, which is not "fully
	// transparent" as written — it means "this app didn't populate alpha,
	// ignore it". Trusting it verbatim would hand the agent an invisible PNG.
	if h.BitCount == 32 && alphaMask != 0 && !anyAlpha {
		for i := 3; i < len(img.Pix); i += 4 {
			img.Pix[i] = 255
		}
	}

	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		return nil, fmt.Errorf("could not encode the clipboard image as PNG: %w", err)
	}
	return buf.Bytes(), nil
}

// extractChannel pulls one 8-bit colour channel out of a packed pixel value
// using mask, scaling up if the channel isn't already 8 bits wide (rare in
// practice — every format this decoder targets uses 8-bit channels — but
// cheap to get right rather than assume).
func extractChannel(pixel, mask uint32) byte {
	if mask == 0 {
		return 0
	}
	shift := bits.TrailingZeros32(mask)
	width := bits.OnesCount32(mask)
	value := (pixel & mask) >> shift
	if width == 8 {
		return byte(value)
	}
	maxVal := uint32(1)<<width - 1
	return byte(value * 255 / maxVal)
}
