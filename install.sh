#!/usr/bin/env bash
# Installiert Agent Chat auf macOS: Python-Umgebung + "Agent Chat.app" in ~/Applications.
# Mehrfach ausführbar (aktualisiert Umgebung und App). Löscht keine Chats oder Einstellungen.
set -euo pipefail

APPDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_BUNDLE="$HOME/Applications/Agent Chat.app"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Dieses Skript ist für macOS. Unter Linux: python3 launcher.py" >&2
  exit 1
fi

# 1. Passendes Python finden (>= 3.10)
PY=""
for cand in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
            /usr/local/bin/python3.13 /usr/local/bin/python3.12 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$(command -v "$cand")"; break
    fi
  fi
done
if [[ -z "$PY" ]]; then
  echo "Kein Python >= 3.10 gefunden. Installieren mit: brew install python@3.12" >&2
  exit 1
fi
echo "→ Python: $PY ($("$PY" --version))"

# 2. Virtuelle Umgebung + Abhängigkeiten
if [[ ! -x "$APPDIR/.venv/bin/python" ]]; then
  echo "→ Lege virtuelle Umgebung an …"
  "$PY" -m venv "$APPDIR/.venv"
fi
echo "→ Installiere Abhängigkeiten …"
"$APPDIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$APPDIR/.venv/bin/python" -m pip install --quiet --upgrade -r "$APPDIR/requirements.txt"
"$APPDIR/.venv/bin/python" -c "import openai, fastapi, webview; print('   openai', openai.__version__)"

# 3. App-Bundle bauen
echo "→ Baue $APP_BUNDLE …"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"
cat > "$APP_BUNDLE/Contents/MacOS/AgentChat" <<EOF
#!/bin/bash
cd "$APPDIR"
exec "$APPDIR/.venv/bin/python" "$APPDIR/launcher.py" >> "\$HOME/Library/Logs/AgentChat.stdout.log" 2>&1
EOF
chmod +x "$APP_BUNDLE/Contents/MacOS/AgentChat"

cat > "$APP_BUNDLE/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Agent Chat</string>
  <key>CFBundleDisplayName</key><string>Agent Chat</string>
  <key>CFBundleIdentifier</key><string>io.activi.agentchat</string>
  <key>CFBundleVersion</key><string>1.0.0</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundleExecutable</key><string>AgentChat</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

# Icon aus der mitgelieferten PNG erzeugen (falls vorhanden)
if [[ -f "$APPDIR/assets/icon.png" ]] && command -v iconutil >/dev/null; then
  ICONSET="$(mktemp -d)/AppIcon.iconset"; mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$APPDIR/assets/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$APPDIR/assets/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
fi
touch "$APP_BUNDLE"

echo ""
echo "✓ Fertig. Starten: Finder → Programme (Benutzer) → Agent Chat"
echo "  oder: open \"$APP_BUNDLE\""
echo "  Log: ~/Library/Logs/AgentChat.log"
if [[ "${1:-}" == "--open" ]]; then open "$APP_BUNDLE"; fi
