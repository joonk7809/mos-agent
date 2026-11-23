#!/usr/bin/env bash
# Build Dot.app (macOS only). Usage: dot/mac/build.sh [python]   e.g. dot/mac/build.sh ~/.venvs/dot/bin/python
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
python="${1:-python3}"
app="$here/build/Dot.app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS"
swiftc -O -o "$app/Contents/MacOS/Dot" "$here/Dot.swift" -framework AppKit -framework Carbon
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>dev.mos.dot</string>
  <key>CFBundleName</key><string>Dot</string>
  <key>CFBundleExecutable</key><string>Dot</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>LSUIElement</key><true/>
  <key>NSAppleEventsUsageDescription</key><string>Dot controls apps on your behalf.</string>
  <key>DotRepo</key><string>$repo</string>
  <key>DotPython</key><string>$python</string>
  <key>DotHotkey</key><string>${DOT_HOTKEY:-ctrl+space}</string>
  <key>DotStopHotkey</key><string>${DOT_STOP_HOTKEY:-opt+esc}</string>
</dict></plist>
PLIST
codesign --force --sign - "$app"
echo "Built $app"
echo "Run: open \"$app\"   (grant Accessibility + Screen Recording to Dot in System Settings > Privacy & Security)"
