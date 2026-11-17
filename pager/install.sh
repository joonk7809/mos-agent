#!/usr/bin/env bash
# Build MOS Pager and put it where it can start at login.
#   macOS: builds "MOS Pager.app" into ~/Applications and opens it.
#   Linux/Windows (Git Bash): installs deps; run with `npm start`.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  echo "Node 20+ is needed. On macOS: brew install node" >&2
  exit 1
fi

npm ci --no-audit --no-fund

if [ "$(uname)" = "Darwin" ]; then
  npm run package:mac
  app_src=$(ls -d dist/MOS\ Pager-darwin-*/MOS\ Pager.app | head -1)
  # Ad-hoc sign: packaging edits Info.plist, and Apple silicon won't run an app with a broken signature.
  codesign --force --deep --sign - "$app_src"
  mkdir -p "$HOME/Applications"
  pkill -x "MOS Pager" 2>/dev/null || true
  rm -rf "$HOME/Applications/MOS Pager.app"
  cp -R "$app_src" "$HOME/Applications/"
  open "$HOME/Applications/MOS Pager.app"
  echo "MOS Pager is running (menu bar dot + floating pet). Toggle it with ⌥⌘P."
else
  echo "Installed. Start it with: (cd $(pwd) && npm start)"
fi

# The sender CLI, for local scripts and Claude Code sessions on this machine.
mkdir -p "$HOME/.local/bin"
ln -sf "$(pwd)/bin/mos-page" "$HOME/.local/bin/mos-page"
echo "CLI: ~/.local/bin/mos-page (add ~/.local/bin to PATH if needed)"

if ! command -v gh >/dev/null || ! gh auth status >/dev/null 2>&1; then
  echo
  echo "One more step: let the pager read your GitHub issues. Either"
  echo "  brew install gh && gh auth login"
  echo "or paste a token into the pager panel the first time it opens."
fi
