# Linux packages for the GAIA terminal hub

Builds a `.deb` and an `.rpm` for **x86-64 only**, each containing:

| Path | Mode |
| --- | --- |
| `/usr/bin/gaia-tui` | 0755 |
| `/usr/bin/gaia-agent` | 0755 |
| `/usr/share/applications/gaia-tui.desktop` | 0644 |
| `/usr/share/icons/hicolor/256x256/apps/gaia-tui.png` | 0644 |
| `/usr/share/doc/gaia-tui/LICENSE.md` | 0644 |

All files are owned `root:root`. The desktop entry sets `Terminal=true` — this is
a TUI, so the launcher has to open a terminal for it.

## Build it

```bash
installer/tui/linux/build-packages.sh \
  --version 1.4.0 \
  --payload ./stage/linux-x64 \
  --out ./dist
```

All three arguments are required. Output is always **both** of:

```
dist/gaia_1.4.0_amd64.deb
dist/gaia-1.4.0.x86_64.rpm
```

- `--payload` is a directory that already contains exactly two executables named
  `gaia-tui` and `gaia-agent`, no extension. The script copies them; it does not
  build them.
- `--out` is created if absent.
- `--version` must be `MAJOR.MINOR.PATCH`, digits only. RPM forbids `-` in a
  version, so a semver prerelease suffix is rejected up front with that reason
  rather than failing later inside `rpmbuild`.

There is no flag to build only one of the two. If `rpmbuild` is missing or the
rpm step fails, the whole run exits non-zero — a release with a `.deb` and no
`.rpm` should not be able to look like a success.

Requires `fpm` and `rpmbuild` on `PATH`.

## Container recipe

`Dockerfile.build` pins both tools in one image, so a container that can build
the `.deb` can always build the `.rpm` too:

```bash
docker build -f installer/tui/linux/Dockerfile.build \
  -t gaia-linux-pkg installer/tui/linux

docker run --rm \
  -v "$PWD:/src:ro" \
  -v "$PWD/stage/linux-x64:/payload:ro" \
  -v "$PWD/dist:/out" \
  gaia-linux-pkg \
  bash /src/installer/tui/linux/build-packages.sh \
    --version 1.4.0 --payload /payload --out /out
```

Mounting the checkout read-only is deliberate: the script only ever reads from
it, and `:ro` keeps a packaging run from touching the working tree.

Behind a TLS-intercepting corporate proxy, `gem install fpm` fails to verify
rubygems.org. Add your corporate root CA to the image
(`COPY ca.crt /usr/local/share/ca-certificates/` then `update-ca-certificates`)
rather than disabling verification. Do not commit the certificate.

### Verify the result

```bash
dpkg-deb -I dist/gaia_1.4.0_amd64.deb     # control metadata
dpkg-deb -c  dist/gaia_1.4.0_amd64.deb    # file list, modes, ownership
rpm -qip     dist/gaia-1.4.0.x86_64.rpm
rpm -qlvp    dist/gaia-1.4.0.x86_64.rpm
```

Then install in a container that did **not** build the packages:

```bash
docker run --rm -v "$PWD/dist:/out:ro" debian:bookworm \
  bash -c 'dpkg -i /out/gaia_1.4.0_amd64.deb && gaia-tui version && dpkg -P gaia'

docker run --rm -v "$PWD/dist:/out:ro" fedora:41 \
  bash -c 'rpm -i /out/gaia-1.4.0.x86_64.rpm && gaia-tui version && rpm -e gaia'
```

Use `debian:bookworm`, not `-slim`. The slim image ships
`path-exclude /usr/share/doc/*` in `/etc/dpkg/dpkg.cfg.d/docker`, so `LICENSE.md`
silently does not land and the package looks broken when it is not.

## Uninstall leaves user data alone

The packages ship **no maintainer scripts at all** — no `preinst`, `postinst`,
`prerm`, or `postrm`, and no rpm scriptlets. Removal therefore deletes only the
packaged files listed above. `~/.gaia` is never touched, because there is no
hook that could touch it.

Verify with `dpkg-deb --ctrl-tarfile <deb> | tar -t` (only `control` and
`md5sums`) and `rpm -qp --scripts <rpm>` (empty).

## Notes

- **Package name is `gaia`**, so the artifact names come out as required. The
  documentation directory is `/usr/share/doc/gaia-tui/` rather than the Debian
  convention `/usr/share/doc/gaia/`, matching the binary users actually invoke.
  fpm also auto-generates a `/usr/share/doc/gaia/changelog.gz` in the `.deb`.
- **The rpm's internal release is `1`** (`gaia-1.4.0-1.x86_64`), while the
  filename is `gaia-1.4.0.x86_64.rpm`. RPM requires a Release field; the
  filename is set explicitly to the name the release process expects.
- **`_build_id_links none`** is passed to rpmbuild. Otherwise it emits
  `/usr/lib/.build-id/**` symlinks that serve debuginfo we do not ship and that
  collide between any two packages sharing a build ID.
- **The icon** is generated from `src/gaia/img/gaia.png` (4096×4096) downscaled
  to 256×256, the hicolor size the desktop entry resolves via `Icon=gaia-tui`.
