#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -eu
repo_dir=$(cd "$(dirname "$0")/.." && pwd)
app_dir="$repo_dir/dist/CloudShare Dispatch.app"
mkdir -p "$app_dir/Contents/MacOS"
swiftc -O "$repo_dir/deploy/quark_dispatch.swift" -o "$app_dir/Contents/MacOS/quark-dispatch"
cat > "$app_dir/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>org.moviepilot.cloudshare.dispatch</string>
<key>CFBundleName</key><string>CloudShare Dispatch</string>
<key>CFBundleExecutable</key><string>quark-dispatch</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>0.2.0</string>
<key>LSUIElement</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$app_dir"
printf 'Built %s\n' "$app_dir"
