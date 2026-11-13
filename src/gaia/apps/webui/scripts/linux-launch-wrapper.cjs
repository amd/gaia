// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The `gaia-desktop` shell script after-pack.cjs writes in front of the real
 * Linux binary.
 *
 * It passes --no-sandbox (issue #782), and first checks that the system
 * libraries the binary needs are installed. Without one the binary dies before
 * it can draw anything: launched from the app grid, GAIA just never opens.
 */

"use strict";

/**
 * Libraries Chromium opens by name when it draws through X11, and aborts
 * without. `ldd` lists only the ones linked into the binary, so these are
 * checked separately. Add a name only after seeing GAIA abort without it.
 */
const X11_OPENED_BY_NAME = ["libX11-xcb.so.1"];

const HELP_URL = "https://amd-gaia.ai/docs/reference/troubleshooting#appimage-on-linux";

/**
 * @param {string} realBinaryName The renamed Electron binary, next to the wrapper.
 * @returns {string} A POSIX sh script.
 */
function launchWrapper(realBinaryName) {
  return `#!/bin/sh
bin="$(dirname "$(readlink -f "$0")")/${realBinaryName}"

missing=""
if command -v ldd >/dev/null 2>&1; then
  missing="$(ldd "$bin" 2>/dev/null | awk '$2 == "=>" && $3 == "not" { print $1 }')"
fi
# Chromium draws through Wayland only in a Wayland session or when told to; the last flag wins.
uses_x11=yes
[ "$XDG_SESSION_TYPE" = wayland ] && uses_x11=""
for arg in "$@"; do
  case "$arg" in
    --ozone-platform=x11) uses_x11=yes ;;
    --ozone-platform=* | --ozone-platform-hint=*) uses_x11="" ;;
  esac
done
ldconfig="$(command -v ldconfig 2>/dev/null)"
[ -z "$ldconfig" ] && [ -x /sbin/ldconfig ] && ldconfig=/sbin/ldconfig
known=""
[ -n "$uses_x11" ] && [ -n "$ldconfig" ] && known="$("$ldconfig" -p 2>/dev/null)"
# With no library list to read, nothing can be said about these: start anyway.
if [ -n "$known" ]; then
  for lib in ${X11_OPENED_BY_NAME.join(" ")}; do
    case "$known" in *"$lib "*) continue ;; esac
    found=""
    old_ifs="$IFS"
    IFS=:
    for dir in $LD_LIBRARY_PATH; do
      [ -n "$dir" ] && [ -e "$dir/$lib" ] && found=yes
    done
    IFS="$old_ifs"
    [ -z "$found" ] && missing="$missing $lib"
  done
fi

if [ -n "$missing" ]; then
  names="$(echo $missing | sed 's/ /, /g')"
  title="GAIA can't start"
  text="This PC is missing something GAIA needs to start: $names. Install it with your software manager, then open GAIA again. What to install: ${HELP_URL}"
  echo "$title. $text" >&2
  # From the app grid nobody sees stderr; LD_LIBRARY_PATH is the AppImage's, not the tool's.
  if LD_LIBRARY_PATH="" zenity --error --no-markup --title "$title" --text "$text" 2>/dev/null; then :
  elif LD_LIBRARY_PATH="" kdialog --title "$title" --error "$text" 2>/dev/null; then :
  else LD_LIBRARY_PATH="" notify-send -a GAIA -i dialog-error "$title" "$text" 2>/dev/null
  fi
  exit 1
fi

exec "$bin" --no-sandbox "$@"
`;
}

module.exports = { launchWrapper, X11_OPENED_BY_NAME, HELP_URL };
