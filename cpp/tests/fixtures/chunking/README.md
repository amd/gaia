# Chunking parity fixtures

`parity_expected.json` holds the chunks that `RAGSDK._split_text_into_chunks`
(`src/gaia/rag/sdk.py`) produces for the documents in this directory.
`ChunkingTest.ChunkBoundariesMatchPythonRagSdk` replays every case through the
C++ splitter and requires byte-identical output — if the two runtimes disagree,
an index built by one is no longer comparable to one built by the other.

The documents cover the branches that decide chunk boundaries:

| Fixture | Exercises |
|---|---|
| `parity_sections.md` | Markdown headers, horizontal rules, multi-byte text |
| `parity_prose.txt` | Paragraph fallback, sentence splitting, abbreviations |
| `parity_mixed.txt` | Title-line heuristic, the 100-character line limit |
| `parity_unicode.md` | Non-ASCII capitals, non-breaking space, CRLF |
| `parity_three_sections.txt` / `parity_four_sections.txt` | The `sections <= 3` branch, from either side |

## Regenerating

Only needed when a fixture changes or the Python splitter changes. Run against
the Python implementation and overwrite `parity_expected.json`:

```python
import json, logging
from types import SimpleNamespace
from gaia.rag.sdk import RAGSDK

def chunk(text, size, overlap):
    stub = SimpleNamespace(
        log=logging.getLogger("parity"),
        llm_client=None,
        config=SimpleNamespace(chunk_size=size, chunk_overlap=overlap,
                               use_llm_chunking=False, show_stats=False),
    )
    stub._split_into_sentences = lambda t: RAGSDK._split_into_sentences(stub, t)
    stub._get_last_n_tokens = lambda t, n: RAGSDK._get_last_n_tokens(stub, t, n)
    return RAGSDK._split_text_into_chunks(stub, text)

# For each {"file", "chunk_size", "chunk_overlap"} case in parity_expected.json.
# read_bytes().decode() — NOT read_text(), whose newline translation would hide
# a CR that the C++ extractor (binary read) keeps.
#   text = Path(case["file"]).read_bytes().decode("utf-8").strip()
#   case["chunks"] = chunk(text, case["chunk_size"], case["chunk_overlap"])
```

A regenerated file that changes existing chunks means the Python algorithm moved;
the C++ port must move with it in the same change.
