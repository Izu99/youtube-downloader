# YouTube Downloader

A small desktop front-end for [yt-dlp](https://github.com/yt-dlp/yt-dlp).
Paste a YouTube link, pick audio or video, hit Download.

![App Screenshot](Screenshot%20from%202026-05-10%2009-33-57.png)

- **Audio** — MP3 at 128 / 192 / 320 kbps → `~/Music/YouTube-MP3`
- **Video** — MP4 up to 1080p → `~/Videos/YouTube`
- Optional thumbnail + metadata embedding
- Playlists: whole, first item only, just the linked video, or a custom range
- **History** — re-pasting a link reuses the file instead of downloading it twice
- The yt-dlp version sits in the header, with a one-click **Update**

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

**`deno` is also required**, on `PATH`. yt-dlp now needs a JavaScript runtime to
solve YouTube's player challenge, and Node is reported as `unsupported` — only
deno works. Without it you get `No supported JavaScript runtime could be found`
and then 403 on every download. The launcher looks in `~/.local/bin`.

To remove it:

```bash
sudo apt remove youtube-downloader
```

### Keeping yt-dlp fresh

**This is the first thing to try when downloads start failing** — before
suspecting the video, the network, or the app. Either press **Update** in the
header, or run:

```bash
youtube-downloader-update
```

That drops the newest yt-dlp in `~/.local/share/youtube-downloader/bin/`, which
the launcher prefers over the bundled one. No root needed.

It tracks the **nightly** channel rather than stable, deliberately. YouTube
ships anti-bot changes (PO tokens, SABR) faster than stable releases, so stable
runs weeks behind a target that moves weekly. Measured on 2026-08-19: stable
`2026.07.04` returned `HTTP Error 403` on every music video, Short and Mix
tried, while that same day's nightly `2026.08.18` downloaded all of them — same
machine, same settings, minutes apart.

The header shows the build's age and turns amber past a week, so a stale copy
announces itself instead of being discovered as a mystery 403.

### A note on speed

Audio is pinned to `bestaudio[audio_channels<=2]`. YouTube offers the same audio
as a ~129 kbps stereo track and a ~388 kbps 5.1 one; plain `bestaudio` takes the
5.1 (29.3 MiB vs 9.8 MiB on a 10-minute video) and ffmpeg downmixes it to stereo
MP3 anyway — three times the bytes for identical output. Pinning it took a
10-minute track from 272s to 82s on a 1.6 Mbps line.

The conversion was never the bottleneck: removing it entirely saved only 17s of
that 272s. On a slow line what matters is how many bytes you fetch.

## Run from source

```bash
./install.sh   # sets up a venv and a ~/.local/bin launcher
```
