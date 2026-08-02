# YouTube Downloader

A small desktop front-end for [yt-dlp](https://github.com/yt-dlp/yt-dlp).
Paste a YouTube link, pick audio or video, hit Download.

![App Screenshot](Screenshot%20from%202026-05-10%2009-33-57.png)

- **Audio** — MP3 at 128 / 192 / 320 kbps → `~/Music/YouTube-MP3`
- **Video** — MP4 up to 1080p → `~/Videos/YouTube`
- Optional thumbnail + metadata embedding
- Playlists: whole, first item only, just the linked video, or a custom range

## Install (.deb)

Grab the `.deb` from the releases page, or build it yourself:

```bash
./build-deb.sh
sudo apt install ./build/youtube-downloader_1.0.0_all.deb
```

Then launch **YouTube Downloader** from the menu, or run `youtube-downloader`.

`customtkinter` and `yt-dlp` are bundled inside the package, so there is no pip
step and no virtualenv — only `python3-tk` and `ffmpeg` come from apt. The old
`yt-mp3` command still works as an alias.

To remove it:

```bash
sudo apt remove youtube-downloader
```

### Keeping yt-dlp fresh

YouTube changes often and breaks old extractors, so the copy bundled at build
time will eventually go stale. Refresh it without rebuilding the package:

```bash
youtube-downloader-update
```

That drops the latest yt-dlp in `~/.local/share/youtube-downloader/bin/`, which
the launcher prefers over the bundled one. No root needed.

## Run from source

```bash
./install.sh   # sets up a venv and a ~/.local/bin launcher
```
