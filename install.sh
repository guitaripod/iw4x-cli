#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${HOME}/.local/bin"
DESKTOP="${HOME}/.local/share/applications"

mkdir -p "$BIN"
install -m 0755 "$ROOT/iw4x.py" "$BIN/iw4x"
echo "installed $BIN/iw4x"

if [ "${1:-}" = "--protocol" ]; then
  mkdir -p "$DESKTOP"
  cat > "$DESKTOP/iw4x-cli.desktop" <<'ENTRY'
[Desktop Entry]
Type=Application
Name=IW4x server link
Exec=iw4x %u
Terminal=true
NoDisplay=true
MimeType=x-scheme-handler/iw4x;
ENTRY
  xdg-mime default iw4x-cli.desktop x-scheme-handler/iw4x
  echo "registered x-scheme-handler/iw4x -> iw4x-cli.desktop"
fi
