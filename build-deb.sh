#!/usr/bin/env bash
# Build a self-contained .deb for the YouTube Downloader GUI.
#
# customtkinter is not packaged in Debian/Ubuntu, and the apt yt-dlp goes stale
# fast (YouTube breaks old extractors), so both are vendored into the package
# instead of being declared as Depends. Only python3-tk and ffmpeg come from apt.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

PKG=youtube-downloader
VERSION="${VERSION:-1.0.0}"
MAINTAINER="${MAINTAINER:-Isuru <adzceptsoftware@gmail.com>}"

BUILD="$HERE/build"
ROOT="$BUILD/$PKG-$VERSION"
APP_DIR="$ROOT/usr/lib/$PKG"
VENDOR="$APP_DIR/vendor"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# ---- clean staging ----------------------------------------------------------
say "Staging in $ROOT"
rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" \
         "$APP_DIR" \
         "$VENDOR/bin" \
         "$ROOT/usr/bin" \
         "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/256x256/apps" \
         "$ROOT/usr/share/doc/$PKG"

# ---- application ------------------------------------------------------------
install -m 0644 "$HERE/yt-mp3.py" "$APP_DIR/app.py"

# ---- vendored python deps ---------------------------------------------------
# --no-compile: .pyc files are regenerated per-interpreter anyway and would only
# bloat the package with paths from the build machine.
say "Vendoring customtkinter + yt-dlp into $VENDOR"
pip3 install --quiet --upgrade --no-compile --target "$VENDOR" customtkinter yt-dlp

# pip drops console scripts with a build-machine shebang into <target>/bin;
# replace them with a launcher that runs the module from the vendor dir, so the
# package never depends on where pip happened to run.
rm -f "$VENDOR/bin/yt-dlp"
cat > "$VENDOR/bin/yt-dlp" <<'EOF'
#!/bin/sh
# Vendored yt-dlp. PYTHONPATH is set by the youtube-downloader launcher.
exec python3 -m yt_dlp "$@"
EOF
chmod 0755 "$VENDOR/bin/yt-dlp"

# dist-info RECORD files reference the staging path and are useless at runtime.
find "$VENDOR" -name '*.dist-info' -prune -exec rm -rf {} + 2>/dev/null || true
find "$VENDOR" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

say "Vendored: $(ls "$VENDOR" | grep -v '^bin$' | tr '\n' ' ')"

# ---- launcher ---------------------------------------------------------------
cat > "$ROOT/usr/bin/$PKG" <<'EOF'
#!/bin/sh
# YouTube Downloader launcher.
set -eu

APP_DIR=/usr/lib/youtube-downloader
VENDOR="$APP_DIR/vendor"
USER_BIN="${XDG_DATA_HOME:-$HOME/.local/share}/youtube-downloader/bin"

# Vendored modules for both this process and the yt-dlp subprocess it spawns.
export PYTHONPATH="$VENDOR${PYTHONPATH:+:$PYTHONPATH}"

# A user-updated yt-dlp (see youtube-downloader-update) wins over the bundled
# copy, which in turn wins over any older system-wide one on PATH.
#
# ~/.local/bin is listed explicitly because YouTube now requires a JavaScript
# runtime (deno) to solve its player challenge, and a desktop-menu launch gets a
# minimal PATH that often omits it — which shows up as "No supported JavaScript
# runtime could be found" followed by 403s on every download.
export PATH="$USER_BIN:$VENDOR/bin:$HOME/.local/bin:$PATH"

exec python3 "$APP_DIR/app.py" "$@"
EOF
chmod 0755 "$ROOT/usr/bin/$PKG"

# Keep the old command working for muscle memory from the install.sh days.
ln -sf "$PKG" "$ROOT/usr/bin/yt-mp3"

# ---- yt-dlp updater ---------------------------------------------------------
# The bundled yt-dlp ages with the package; this refreshes it per-user without
# touching /usr, so no root and no package rebuild is needed when YouTube
# changes and extractors break.
cat > "$ROOT/usr/bin/$PKG-update" <<'EOF'
#!/bin/sh
# Update the yt-dlp used by YouTube Downloader to the latest release, per-user.
set -eu

USER_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/youtube-downloader"
BIN="$USER_DIR/bin/yt-dlp"

mkdir -p "$USER_DIR/bin"

# Nightly, not stable. YouTube's anti-bot changes (PO tokens, SABR) break
# downloads faster than the stable channel ships: on 2026-08-19 stable
# (2026.07.04) returned 403 on every music video tried, while that day's
# nightly downloaded all of them. Stable is weeks behind a weekly-moving target.
URL=https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp

echo "==> Downloading the latest yt-dlp nightly…"
if command -v curl >/dev/null 2>&1; then
    curl -fL --progress-bar "$URL" -o "$BIN.tmp"
elif command -v wget >/dev/null 2>&1; then
    wget -q --show-progress "$URL" -O "$BIN.tmp"
else
    echo "Need curl or wget to update." >&2
    exit 1
fi

chmod 0755 "$BIN.tmp"
mv "$BIN.tmp" "$BIN"

echo "==> Installed $("$BIN" --version) at $BIN"
echo "    YouTube Downloader will now use this instead of the bundled copy."
EOF
chmod 0755 "$ROOT/usr/bin/$PKG-update"

# ---- icon -------------------------------------------------------------------
install -m 0644 "$HERE/icon.png" \
    "$ROOT/usr/share/icons/hicolor/256x256/apps/$PKG.png"

# ---- desktop entry ----------------------------------------------------------
cat > "$ROOT/usr/share/applications/$PKG.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=YouTube Downloader
GenericName=Video and Audio Downloader
Comment=Download YouTube videos as MP4 or audio as MP3
Exec=$PKG
Icon=$PKG
Terminal=false
Categories=AudioVideo;Audio;Video;
Keywords=youtube;download;mp3;mp4;video;audio;yt-dlp;
StartupNotify=true
EOF
chmod 0644 "$ROOT/usr/share/applications/$PKG.desktop"

# ---- docs -------------------------------------------------------------------
install -m 0644 "$HERE/README.md" "$ROOT/usr/share/doc/$PKG/README.md"

# ---- control ----------------------------------------------------------------
INSTALLED_KB=$(du -sk --exclude=DEBIAN "$ROOT" | cut -f1)

cat > "$ROOT/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: video
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-tk, ffmpeg, xdg-utils
Recommends: curl
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_KB
Homepage: https://github.com/Izu99/youtube-downloader
Description: YouTube Downloader - save YouTube video as MP4 or audio as MP3
 A small desktop front-end for yt-dlp. Paste a YouTube link, pick audio
 (MP3 at 128/192/320 kbps) or video (up to 1080p), and download it, with
 optional thumbnail and metadata embedding.
 .
 Playlists can be downloaded whole, as a single item, or over a custom
 range. Audio lands in ~/Music/YouTube-MP3 and video in ~/Videos/YouTube.
 .
 customtkinter and yt-dlp are bundled, so no pip install is required.
 Run youtube-downloader-update to refresh the bundled yt-dlp when
 YouTube changes.
EOF

cat > "$ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true
    fi
fi
EOF
chmod 0755 "$ROOT/DEBIAN/postinst"

cat > "$ROOT/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = purge ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true
    fi
fi
EOF
chmod 0755 "$ROOT/DEBIAN/postrm"

# ---- build ------------------------------------------------------------------
# pip and mkdir honour the build user's umask; Debian expects 0755 directories.
find "$ROOT" -type d -exec chmod 0755 {} +

DEB="$BUILD/${PKG}_${VERSION}_all.deb"
say "Building $DEB"
fakeroot dpkg-deb --build --root-owner-group "$ROOT" "$DEB" >/dev/null

say "Done: $DEB ($(du -h "$DEB" | cut -f1))"
echo
echo "Install with:  sudo apt install $DEB"
echo "Remove with:   sudo apt remove $PKG"
