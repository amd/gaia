package indent

import (
	"bytes"
	"errors"
	"io"
	"testing"

	"github.com/muesli/reflow/ansi"
)

func TestIndent(t *testing.T) {
	t.Parallel()

	tt := []struct {
		Input    string
		Expected string
		Indent   uint
	}{
		// No-op, should pass through:
		{
			"foobar",
			"foobar",
			0,
		},
		// Basic indentation:
		{
			"foobar",
			"    foobar",
			4,
		},
		// Multi-line indentation:
		{
			"foo\nbar",
			"    foo\n    bar",
			4,
		},
		// ANSI sequence codes:
		{
			"\x1B[38;2;249;38;114mfoo",
			"\x1B[38;2;249;38;114m\x1B[0m    \x1B[38;2;249;38;114mfoo",
			4,
		},
	}

	for i, tc := range tt {
		f := NewWriter(tc.Indent, nil)

		_, err := f.Write([]byte(tc.Input))
		if err != nil {
			t.Error(err)
		}

		if f.String() != tc.Expected {
			t.Errorf("Test %d, expected:\n\n`%s`\n\nActual Output:\n\n`%s`", i, tc.Expected, f.String())
		}
	}
}

func TestIndentWriter(t *testing.T) {
	t.Parallel()

	f := NewWriter(4, nil)

	_, err := f.Write([]byte("foo\n"))
	if err != nil {
		t.Error(err)
	}
	_, err = f.Write([]byte("bar"))
	if err != nil {
		t.Error(err)
	}

	exp := "    foo\n    bar"
	if f.String() != exp {
		t.Errorf("expected:\n\n`%s`\n\nActual Output:\n\n`%s`", exp, f.String())
	}
}

func TestIndentString(t *testing.T) {
	t.Parallel()

	actual := String("foobar", 3)
	expected := "   foobar"
	if actual != expected {
		t.Errorf("expected:\n\n`%s`\n\nActual Output:\n\n`%s`", expected, actual)
	}
}

func BenchmarkIndentString(b *testing.B) {
	b.RunParallel(func(pb *testing.PB) {
		b.ReportAllocs()
		b.ResetTimer()
		for pb.Next() {
			String("foo", 2)
		}
	})
}

func TestIndentWriterWithIndentFunc(t *testing.T) {
	t.Parallel()

	f := NewWriter(2, func(w io.Writer) {
		_, _ = w.Write([]byte("."))
	})

	_, err := f.Write([]byte("foo\n"))
	if err != nil {
		t.Error(err)
	}
	_, err = f.Write([]byte("bar"))
	if err != nil {
		t.Error(err)
	}

	exp := "..foo\n..bar"
	if f.String() != exp {
		t.Errorf("expected:\n\n`%s`\n\nActual Output:\n\n`%s`", exp, f.String())
	}
}

func TestNewWriterPipe(t *testing.T) {
	t.Parallel()

	b := &bytes.Buffer{}
	f := NewWriterPipe(b, 2, nil)

	if _, err := f.Write([]byte("foo")); err != nil {
		t.Error(err)
	}

	actual := b.String()
	expected := "  foo"

	if actual != expected {
		t.Errorf("expected:\n\n`%s`\n\nActual Output:\n\n`%s`", expected, actual)
	}
}

func TestWriter_Error(t *testing.T) {
	t.Parallel()

	f := &Writer{
		Indent:     2,
		ansiWriter: &ansi.Writer{Forward: fakeWriter{}},
	}

	if _, err := f.Write([]byte("foo")); err != fakeErr {
		t.Error(err)
	}

	f.skipIndent = true

	if _, err := f.Write([]byte("foo")); err != fakeErr {
		t.Error(err)
	}
}

var fakeErr = errors.New("fake error")

type fakeWriter struct{}

func (fakeWriter) Write(_ []byte) (int, error) {
	return 0, fakeErr
}
