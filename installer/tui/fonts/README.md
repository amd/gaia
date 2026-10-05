# Bundled font — IBM Plex Mono

The Windows setup installs IBM Plex Mono per-user so the **GAIA** Windows
Terminal profile it writes has the face it names. Nothing here is committed as a
binary: [`fetch_fonts.py`](../fetch_fonts.py) downloads the official release at
build time and verifies it against [`fonts.lock.json`](fonts.lock.json), the
same fetch-and-verify contract the installer already uses for `gaia-agent` and
the Lemonade MSI.

| | |
| --- | --- |
| Source | [IBM/plex release `@ibm/plex-mono@2.5.0`](https://github.com/IBM/plex/releases/tag/%40ibm%2Fplex-mono%402.5.0), asset `ibm-plex-mono.zip` |
| Zip SHA-256 | `6d23f01257663d8cc49a0d64c22ced630b79e0e2a0ac08a0da86e9a38bbc481c` (matches the digest GitHub publishes for the asset) |
| Zip size | 6,940,652 bytes |
| Faces bundled | Regular, Bold, Italic, Bold Italic (`fonts/complete/ttf/`, ~700 KB) |
| Licence | SIL Open Font License 1.1, shipped as `IBM-Plex-Mono-OFL.txt` next to `gaia-tui.exe` |

Four faces, not one: the TUI renders bold and italic text, and without the real
faces Windows Terminal synthesises them. Every face carries the family name
`IBM Plex Mono`, which is what the profile's `font.face` asks for.

## Re-pinning

1. Pick a release from <https://github.com/IBM/plex/releases> tagged
   `@ibm/plex-mono@<version>` (not `plex-mono-variable`).
2. Update `url`, `release`, `size` and `sha256` in `fonts.lock.json`, checking the
   zip's SHA-256 against the digest GitHub shows for the asset.
3. Update each member's `sha256`, and its `full_name` if the face was renamed —
   the setup registers each face under `<full_name> (TrueType)`.
4. Update the table above, then run
   `python -m pytest tests/unit/installer/test_terminal_profile.py`.
