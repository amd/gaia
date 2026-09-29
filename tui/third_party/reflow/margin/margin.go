package margin

import (
	"bytes"
	"io"

	"github.com/muesli/reflow/indent"
	"github.com/muesli/reflow/padding"
)

type Writer struct {
	buf bytes.Buffer
	pw  *padding.Writer
	iw  *indent.Writer
}

func NewWriter(width uint, margin uint, marginFunc func(io.Writer)) *Writer {
	pw := padding.NewWriter(width, marginFunc)
	iw := indent.NewWriter(margin, marginFunc)

	return &Writer{
		pw: pw,
		iw: iw,
	}
}

// Bytes is shorthand for declaring a new default margin-writer instance,
// used to immediately apply a margin to a byte slice.
func Bytes(b []byte, width uint, margin uint) []byte {
	f := NewWriter(width, margin, nil)
	_, _ = f.Write(b)
	f.Close()

	return f.Bytes()
}

// String is shorthand for declaring a new default margin-writer instance,
// used to immediately apply a margin to a string.
func String(s string, width uint, margin uint) string {
	return string(Bytes([]byte(s), width, margin))
}

func (w *Writer) Write(b []byte) (int, error) {
	_, err := w.iw.Write(b)
	if err != nil {
		return 0, err
	}

	n, err := w.pw.Write(w.iw.Bytes())
	if err != nil {
		return n, err
	}

	return n, nil
}

// Close will finish the margin operation. Always call it before trying to
// retrieve the final result.
func (w *Writer) Close() error {
	err := w.pw.Close()
	if err != nil {
		return err
	}

	_, err = w.buf.Write(w.pw.Bytes())
	return err
}

// Bytes returns the result as a byte slice.
func (w *Writer) Bytes() []byte {
	return w.buf.Bytes()
}

// String returns the result as a string.
func (w *Writer) String() string {
	return w.buf.String()
}
