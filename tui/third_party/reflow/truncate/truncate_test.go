package truncate

import (
	"bytes"
	"errors"
	"testing"

	"github.com/muesli/reflow/ansi"
)

func TestTruncate(t *testing.T) {
	t.Parallel()

	tt := []struct {
		width    uint
		tail     string
		in       string
		expected string
	}{
		// No-op, should pass through:
		{
			10,
			"",
			"foo",
			"foo",
		},
		// Basic truncate:
		{
			3,
			"",
			"foobar",
			"foo",
		},
		// Truncate with tail:
		{
			4,
			".",
			"foobar",
			"foo.",
		},
		// Same width:
		{
			3,
			"",
			"foo",
			"foo",
		},
		// Tail is longer than width:
		{
			2,
			"...",
			"foo",
			"...",
		},
		// Spaces only:
		{
			2,
			"…",
			"    ",
			" …",
		},
		// Double-width runes:
		{
			2,
			"",
			"你好",
			"你",
		},
		// Double-width rune is dropped if it is too wide:
		{
			1,
			"",
			"你",
			"",
		},
		// ANSI sequence codes and double-width characters:
		{
			3,
			"",
			"\x1B[38;2;249;38;114m你好\x1B[0m",
			"\x1B[38;2;249;38;114m你\x1B[0m",
		},
		// Reset styling sequence is added after truncate:
		{
			1,
			"",
			"\x1B[7m--",
			"\x1B[7m-\x1B[0m",
		},
		// Reset styling sequence not added if operation is a noop:
		{
			2,
			"",
			"\x1B[7m--",
			"\x1B[7m--",
		},
		// Tail is printed before reset sequence:
		{
			3,
			"…",
			"\x1B[38;5;219mHiya!",
			"\x1B[38;5;219mHi…\x1B[0m",
		},
	}

	for i, tc := range tt {
		f := NewWriter(tc.width, tc.tail)

		_, err := f.Write([]byte(tc.in))
		if err != nil {
			t.Error(err)
		}

		if f.String() != tc.expected {
			t.Errorf("Test %d, expected:\n\n`%s`\n\nActual Output:\n\n`%s`", i, tc.expected, f.String())
		}
	}
}

func TestTruncateString(t *testing.T) {
	t.Parallel()

	actual := String("foobar", 3)
	expected := "foo"
	if actual != expected {
		t.Errorf("expected:\n\n`%s`\n\nActual Output:\n\n`%s`", expected, actual)
	}
}

func BenchmarkTruncateString(b *testing.B) {
	b.RunParallel(func(pb *testing.PB) {
		b.ReportAllocs()
		b.ResetTimer()
		for pb.Next() {
			String("foo", 2)
		}
	})
}

func TestTruncateBytes(t *testing.T) {
	t.Parallel()

	actual := Bytes([]byte("foobar"), 3)
	expected := []byte("foo")
	if !bytes.Equal(actual, expected) {
		t.Errorf("expected:\n\n`%s`\n\nActual Output:\n\n`%s`", expected, actual)
	}
}

func TestNewWriterPipe(t *testing.T) {
	t.Parallel()

	b := &bytes.Buffer{}
	f := NewWriterPipe(b, 2, "")

	if _, err := f.Write([]byte("foo")); err != nil {
		t.Error(err)
	}

	actual := b.String()
	expected := "fo"

	if actual != expected {
		t.Errorf("expected:\n\n`%s`\n\nActual Output:\n\n`%s`", expected, actual)
	}
}

func TestWriter_Error(t *testing.T) {
	t.Parallel()

	f := &Writer{
		width:      2,
		ansiWriter: &ansi.Writer{Forward: fakeWriter{}},
	}

	if _, err := f.Write([]byte("foo")); err != fakeErr {
		t.Error(err)
	}
}

var fakeErr = errors.New("fake error")

type fakeWriter struct{}

func (fakeWriter) Write(_ []byte) (int, error) {
	return 0, fakeErr
}
