#!/usr/bin/env python3
import customtkinter as ctk
import subprocess
import threading
import os
import re
import json
import time
import datetime

AUDIO_DIR = os.path.expanduser("~/Music/YouTube-MP3")
VIDEO_DIR = os.path.expanduser("~/Videos/YouTube")
DATA_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "youtube-downloader",
)
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

AUDIO_QUALITIES = ["320 kbps", "192 kbps", "128 kbps"]
VIDEO_QUALITIES = ["Best available", "1080p", "720p", "480p", "360p"]

# YouTube offers a 129 kbps stereo track and a 388 kbps 5.1 one for the same
# video. A plain "bestaudio" takes the 5.1 (29 MiB vs 9.8 MiB on a 10-minute
# track) and ffmpeg then downmixes it to stereo MP3 anyway — three times the
# bytes for identical output. On a slow line that dominates everything else:
# measured here, download was 255 s of a 272 s run, the MP3 encode only ~17 s.
AUDIO_FORMAT = "bestaudio[audio_channels<=2]/bestaudio"

# Fetches fragments in parallel, recovering most of the gap between what a
# single yt-dlp stream gets and the line's real capacity.
CONCURRENCY = ["-N", "4"]

# 320 kbps re-encoded from a 129 kbps source adds no quality — just a bigger
# file and a longer encode — so 192 is the sane default.
DEFAULT_AUDIO_QUALITY = "192 kbps"

# youtu.be/ID, watch?v=ID, /shorts/ID, /embed/ID, /live/ID
YT_ID_RE = re.compile(r"(?:youtu\.be/|/shorts/|/embed/|/live/|[?&]v=)([A-Za-z0-9_-]{11})")

# yt-dlp announces the file it is writing at each stage. Later stages overwrite
# earlier ones, so replaying these in order leaves the real final path: the
# raw stream first, then the converted MP3 / merged MP4, then any move.
DEST_RE    = re.compile(r"^\[(?:download|ExtractAudio|VideoConvertor)\] Destination: (.+)$")
ALREADY_RE = re.compile(r"^\[download\] (.+) has already been downloaded")
MERGER_RE  = re.compile(r'^\[Merger\] Merging formats into "(.+)"$')
MOVE_RE    = re.compile(r'^\[MoveFiles\] Moving file "(?:.+)" to "(.+)"$')

# Playlist modes that land exactly one file, so history can key on the video ID.
SINGLE_MODES = ("First item only", "Just the linked video")

# The shipped updater, which pulls the newest yt-dlp nightly.
UPDATE_CMD = "youtube-downloader-update"

# YouTube's anti-bot changes break older builds within weeks — on 2026-08-19 a
# six-week-old yt-dlp returned 403 on every music video while that day's nightly
# downloaded all of them. So say the build is stale before a download fails,
# rather than leaving it to be discovered as a mystery error.
STALE_DAYS = 7


def ytdlp_version():
    """Version of the yt-dlp this app will actually run, or None."""
    try:
        out = subprocess.run(
            ["yt-dlp", "--version"], capture_output=True, text=True, timeout=20
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def version_age_days(version):
    """yt-dlp versions are release dates — 2026.07.04 (stable) or
    2026.08.18.122307 (nightly). Returns days old, or None if unparseable."""
    if not version:
        return None
    parts = version.split(".")
    if len(parts) < 3:
        return None
    try:
        built = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, TypeError):
        return None
    return (datetime.date.today() - built).days


def video_id(url):
    m = YT_ID_RE.search(url)
    return m.group(1) if m else None


def load_history():
    try:
        with open(HISTORY_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_history(entries):
    """Write through a temp file so a crash mid-write can't truncate history."""
    tmp = HISTORY_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(entries[-500:], f, indent=1)
        os.replace(tmp, HISTORY_FILE)
    except OSError:
        pass

# --no-playlist only strips the list from a watch?v=...&list=... URL; a bare
# playlist?list=... URL still grabs everything, so pin it with --playlist-items.
PLAYLIST_MODES = {
    # Works for both a bare playlist?list=... URL and a watch?v=...&list=... one.
    "First item only":        ["--yes-playlist", "--playlist-items", "1"],
    # Only meaningful on watch?v=...&list=... — grabs the linked video, not item 1.
    "Just the linked video":  ["--no-playlist"],
    "Whole playlist":         ["--yes-playlist"],
    "Custom range…":          None,  # filled from the items entry
}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ---- Design palette --------------------------------------------------------
# One place for every colour so the whole UI stays visually consistent.
BG       = "#0e0f13"   # window background
CARD     = "#171a21"   # main content card
FIELD    = "#20242e"   # inputs / dropdowns / segmented track
BORDER   = "#2b3038"   # hairline borders
TEXT     = "#e8eaed"   # primary text
MUTED    = "#8b929e"   # secondary / helper text
ACCENT   = "#4f9cff"   # blue accents (labels, focus, links)
BRAND    = "#ff3b30"   # YouTube red — primary action
BRAND_HI = "#d32f2f"   # red hover
NEUTRAL  = "#2b3038"   # secondary button
NEUTRAL_HI = "#363c46"
LOG_BG   = "#0b0c0f"
SUCCESS  = "#3ddc84"   # completion highlight (green)
SUCCESS_BG = "#12351f"
ERROR    = "#ff5a4d"   # failure highlight (red)
ERROR_BG = "#3a1512"
REUSE    = "#7cc4ff"   # "already downloaded" highlight (blue)
REUSE_BG = "#11283d"
WARN     = "#ffb020"   # stale yt-dlp build (amber)

PAD = 26  # shared horizontal padding inside the card

CORNER = 12


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Downloader")
        # 780px tall did not fit a 1366x768 laptop panel — the buttons fell off
        # the bottom of the screen. Wide-and-short clears it with room for the
        # title bar, and resizing is allowed now so other screens can adjust.
        # The left rail's controls stack to ~595px, so the minimum height is the
        # starting height: anything shorter clips the Download button off the
        # bottom, which is the exact failure this resize was meant to fix.
        self.geometry("900x600")
        self.minsize(820, 600)
        self.resizable(True, True)
        self.configure(fg_color=BG)

        self.proc = None
        self.cancelled = False
        self.history = load_history()
        self.history_win = None
        # Filled from yt-dlp's own output as a run progresses; whatever it holds
        # at the end is the file the run actually produced.
        self.last_path = None
        self.pending = None

        self._build_ui()
        self._refresh_version()

    # ---- UI helpers --------------------------------------------------------

    def _section_label(self, parent, text):
        """Small muted upper-case caption above a control."""
        return ctk.CTkLabel(
            parent, text=text.upper(),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED, anchor="w",
        )

    # ---- UI ----------------------------------------------------------------

    def _build_ui(self):
        # Everything lives inside one rounded card so the window edge breathes.
        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=18, border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True, padx=16, pady=16)

        # ---- Header -------------------------------------------------------
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=PAD, pady=(22, 2))

        # Red accent chip next to the title for a bit of brand identity.
        ctk.CTkLabel(
            header, text="  ▶  ", font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=BRAND, text_color="#ffffff", corner_radius=8,
        ).pack(side="left")
        ctk.CTkLabel(
            header, text="YouTube Downloader",
            font=ctk.CTkFont(size=21, weight="bold"), text_color=TEXT,
        ).pack(side="left", padx=(10, 0))

        # ---- yt-dlp version + update ---------------------------------------
        # Kept in the header because a stale yt-dlp is the single most likely
        # cause of downloads failing, and it is invisible until one does.
        self.update_btn = ctk.CTkButton(
            header, text="Update", width=72, height=28, corner_radius=8,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=NEUTRAL, hover_color=NEUTRAL_HI, command=self._run_update,
        )
        self.update_btn.pack(side="right")
        self.version_var = ctk.StringVar(value="checking yt-dlp…")
        self.version_label = ctk.CTkLabel(
            header, textvariable=self.version_var,
            font=ctk.CTkFont(size=11), text_color=MUTED,
        )
        self.version_label.pack(side="right", padx=(0, 10))

        ctk.CTkLabel(
            card, text="Paste a link, pick a format, hit Download.",
            font=ctk.CTkFont(size=13), text_color=MUTED,
        ).pack(anchor="w", padx=PAD + 2, pady=(0, 16))

        # ---- Mode switch --------------------------------------------------
        self.mode = ctk.StringVar(value="Audio (MP3)")
        ctk.CTkSegmentedButton(
            card, values=["Audio (MP3)", "Video (MP4)"],
            variable=self.mode, command=self._on_mode_change,
            height=40, corner_radius=CORNER,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=FIELD, unselected_color=FIELD,
            selected_color=BRAND, selected_hover_color=BRAND_HI,
            unselected_hover_color=NEUTRAL,
        ).pack(padx=PAD, fill="x")

        # ---- Two columns --------------------------------------------------
        # The window has to fit a 1366x768 laptop panel, so the controls sit in
        # a fixed-width left rail with the log filling the space beside them,
        # instead of stacking into one column taller than the screen.
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=PAD, pady=(14, PAD))

        left = ctk.CTkFrame(body, fg_color="transparent", width=356)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)   # hold the rail at its set width

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(20, 0))

        # ---- URL entry ----------------------------------------------------
        url_frame = ctk.CTkFrame(left, fg_color="transparent")
        url_frame.pack(fill="x")
        self._section_label(url_frame, "YouTube URL").pack(anchor="w")
        self.url_entry = ctk.CTkEntry(
            url_frame, placeholder_text="https://youtu.be/…",
            height=42, corner_radius=CORNER, font=ctk.CTkFont(size=13),
            fg_color=FIELD, border_color=BORDER, border_width=1,
        )
        self.url_entry.pack(fill="x", pady=(6, 0))
        self.url_entry.bind("<Return>", lambda _e: self._start_download())

        # ---- Quality picker ----------------------------------------------
        quality_frame = ctk.CTkFrame(left, fg_color="transparent")
        quality_frame.pack(fill="x", pady=(12, 0))
        self._section_label(quality_frame, "Quality").pack(anchor="w")
        self.quality = ctk.CTkOptionMenu(
            quality_frame, values=AUDIO_QUALITIES, height=38,
            corner_radius=CORNER, font=ctk.CTkFont(size=13),
            fg_color=FIELD, button_color=NEUTRAL, button_hover_color=NEUTRAL_HI,
        )
        self.quality.pack(fill="x", pady=(6, 0))
        self.quality.set(DEFAULT_AUDIO_QUALITY)

        # ---- Extras -------------------------------------------------------
        self.embed_meta = ctk.CTkCheckBox(
            left, text="Embed thumbnail + metadata",
            font=ctk.CTkFont(size=12), text_color=TEXT,
            fg_color=BRAND, hover_color=BRAND_HI,
            border_color=BORDER, corner_radius=6,
        )
        self.embed_meta.select()
        self.embed_meta.pack(anchor="w", pady=(14, 0))

        # ---- Playlist handling -------------------------------------------
        pl_frame = ctk.CTkFrame(left, fg_color="transparent")
        pl_frame.pack(fill="x", pady=(12, 0))
        self._section_label(pl_frame, "Playlist links").pack(anchor="w")

        pl_row = ctk.CTkFrame(pl_frame, fg_color="transparent")
        pl_row.pack(fill="x", pady=(6, 0))
        self.playlist_mode = ctk.CTkOptionMenu(
            pl_row, values=list(PLAYLIST_MODES), height=38,
            corner_radius=CORNER, font=ctk.CTkFont(size=13),
            fg_color=FIELD, button_color=NEUTRAL, button_hover_color=NEUTRAL_HI,
            command=self._on_playlist_mode_change,
        )
        self.playlist_mode.pack(side="left", fill="x", expand=True)
        self.items_entry = ctk.CTkEntry(
            pl_row, placeholder_text="1-5,8", width=92, height=38,
            corner_radius=CORNER, font=ctk.CTkFont(size=13), state="disabled",
            fg_color=FIELD, border_color=BORDER, border_width=1,
        )
        self.items_entry.pack(side="left", padx=(10, 0))

        # ---- Save folder row (own subtle strip) --------------------------
        self.folder_var = ctk.StringVar(value=AUDIO_DIR)
        self.folder_display = ctk.StringVar()

        folder_frame = ctk.CTkFrame(left, fg_color=FIELD, corner_radius=CORNER)
        folder_frame.pack(fill="x", pady=(16, 0))
        ctk.CTkLabel(
            folder_frame, text="Save to", font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED,
        ).pack(side="left", padx=(12, 6), pady=8)
        # Buttons are packed before the path so that on a narrow rail the path
        # gives up room to them rather than pushing them out of the strip.
        ctk.CTkButton(
            folder_frame, text="Open", width=52, height=26, corner_radius=8,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=NEUTRAL, hover_color=NEUTRAL_HI, command=self._open_folder,
        ).pack(side="right", padx=(4, 8), pady=6)
        ctk.CTkButton(
            folder_frame, text="History", width=64, height=26, corner_radius=8,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=NEUTRAL, hover_color=NEUTRAL_HI, command=self._open_history,
        ).pack(side="right", pady=6)
        ctk.CTkLabel(
            folder_frame, textvariable=self.folder_display, anchor="w",
            font=ctk.CTkFont(size=11), text_color=ACCENT,
        ).pack(side="left", fill="x", expand=True)
        self._set_folder(AUDIO_DIR)

        # ---- Action buttons ----------------------------------------------
        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.pack(fill="x", pady=(16, 0))
        self.btn = ctk.CTkButton(
            btn_row, text="Download MP3", height=46, corner_radius=CORNER,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=BRAND, hover_color=BRAND_HI, command=self._start_download,
        )
        self.btn.pack(side="left", fill="x", expand=True)
        self.cancel_btn = ctk.CTkButton(
            btn_row, text="Cancel", width=88, height=46, corner_radius=CORNER,
            font=ctk.CTkFont(size=13, weight="bold"), state="disabled",
            fg_color=NEUTRAL, hover_color=NEUTRAL_HI, command=self._cancel,
        )
        self.cancel_btn.pack(side="left", padx=(10, 0))

        # ---- Activity: title, log, then progress + status (right column) --
        self._section_label(right, "Activity").pack(anchor="w")

        # The name of what is being fetched, shown as soon as yt-dlp announces
        # its output file — early enough to hit Cancel on the wrong video.
        self.title_var = ctk.StringVar(value="")
        self.title_label = ctk.CTkLabel(
            right, textvariable=self.title_var, anchor="w", justify="left",
            wraplength=430, font=ctk.CTkFont(size=12, weight="bold"),
            text_color=TEXT,
        )
        self.title_label.pack(fill="x", pady=(6, 0))

        self.log = ctk.CTkTextbox(
            right, corner_radius=CORNER,
            font=ctk.CTkFont(family="DejaVu Sans Mono", size=10),
            fg_color=LOG_BG, text_color="#9aa4b2", border_color=BORDER,
            border_width=1, state="disabled",
        )
        self.log.pack(fill="both", expand=True, pady=(6, 0))

        self.progress = ctk.CTkProgressBar(
            right, height=8, corner_radius=6, progress_color=BRAND, fg_color=FIELD,
        )
        self.progress.pack(fill="x", pady=(14, 0))
        self.progress.set(0)

        self.status_var = ctk.StringVar(value="Ready")
        self.status_label = ctk.CTkLabel(
            right, textvariable=self.status_var, anchor="w",
            font=ctk.CTkFont(size=11), text_color=MUTED,
        )
        self.status_label.pack(fill="x", pady=(8, 0))

        # Colour tags for highlighted result banners in the log. CTkTextbox
        # wraps a plain tk.Text, so drive tags through the underlying widget.
        self.log._textbox.tag_config(
            "success", foreground=SUCCESS, background=SUCCESS_BG,
            selectbackground=SUCCESS_BG, spacing1=4, spacing3=4,
        )
        self.log._textbox.tag_config(
            "error", foreground=ERROR, background=ERROR_BG,
            selectbackground=ERROR_BG, spacing1=4, spacing3=4,
        )
        self.log._textbox.tag_config(
            "reuse", foreground=REUSE, background=REUSE_BG,
            selectbackground=REUSE_BG, spacing1=4, spacing3=4,
        )

    def _is_video(self):
        return self.mode.get().startswith("Video")

    def _set_folder(self, path):
        """folder_var keeps the real path because it builds the output template;
        the label shows a ~-shortened one so it fits the narrow left rail."""
        self.folder_var.set(path)
        home = os.path.expanduser("~")
        self.folder_display.set(
            "~" + path[len(home):] if path.startswith(home) else path
        )

    def _on_mode_change(self, _value=None):
        if self._is_video():
            self.quality.configure(values=VIDEO_QUALITIES)
            self.quality.set(VIDEO_QUALITIES[0])
            self._set_folder(VIDEO_DIR)
            self.btn.configure(text="Download Video")
        else:
            self.quality.configure(values=AUDIO_QUALITIES)
            self.quality.set(DEFAULT_AUDIO_QUALITY)
            self._set_folder(AUDIO_DIR)
            self.btn.configure(text="Download MP3")

    def _on_playlist_mode_change(self, value):
        if value == "Custom range…":
            self.items_entry.configure(state="normal")
            self.items_entry.focus()
        else:
            self.items_entry.configure(state="disabled")

    def _open_folder(self):
        subprocess.Popen(["xdg-open", self.folder_var.get()])

    # ---- history window ----------------------------------------------------

    def _open_history(self):
        if self.history_win is not None and self.history_win.winfo_exists():
            self.history_win.lift()
            self.history_win.focus()
            return

        win = ctk.CTkToplevel(self)
        self.history_win = win
        win.title("Download history")
        win.geometry("580x620")
        win.configure(fg_color=BG)
        # A CTkToplevel is drawn before customtkinter finishes restyling it, so
        # without the delayed lift it can flash behind the main window.
        win.after(180, win.lift)

        head = ctk.CTkFrame(win, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(18, 8))
        ctk.CTkLabel(
            head, text="Download history",
            font=ctk.CTkFont(size=17, weight="bold"), text_color=TEXT,
        ).pack(side="left")
        ctk.CTkButton(
            head, text="Clear all", width=80, height=28, corner_radius=8,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=NEUTRAL, hover_color=NEUTRAL_HI,
            command=self._clear_history,
        ).pack(side="right")

        ctk.CTkLabel(
            win, text="Pasting one of these links again reuses the file instead of downloading.",
            font=ctk.CTkFont(size=11), text_color=MUTED,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        body = ctk.CTkScrollableFrame(win, fg_color=CARD, corner_radius=CORNER)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        if not self.history:
            ctk.CTkLabel(
                body, text="Nothing downloaded yet.",
                font=ctk.CTkFont(size=13), text_color=MUTED,
            ).pack(pady=36)
            return

        for entry in reversed(self.history):
            self._history_row(body, entry)

    def _history_row(self, parent, entry):
        path = entry.get("path") or ""
        exists = bool(path) and os.path.exists(path)

        row = ctk.CTkFrame(parent, fg_color=FIELD, corner_radius=10)
        row.pack(fill="x", padx=8, pady=5)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=(12, 6), pady=9)

        title = entry.get("title") or "(untitled)"
        ctk.CTkLabel(
            left, text=title[:54] + ("…" if len(title) > 54 else ""),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=TEXT if exists else MUTED, anchor="w", justify="left",
        ).pack(anchor="w")

        meta = "%s · %s · %s" % (
            "MP4" if entry.get("kind") == "video" else "MP3",
            entry.get("quality", ""),
            self._fmt_date(entry.get("ts")),
        )
        if not exists:
            meta += "  ·  file deleted"
        ctk.CTkLabel(
            left, text=meta, font=ctk.CTkFont(size=10),
            text_color=MUTED, anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        ctk.CTkButton(
            row, text="Use link", width=64, height=26, corner_radius=8,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=NEUTRAL, hover_color=NEUTRAL_HI,
            command=lambda u=entry.get("url", ""): self._fill_url(u),
        ).pack(side="right", padx=(4, 10))
        ctk.CTkButton(
            row, text="Play", width=52, height=26, corner_radius=8,
            font=ctk.CTkFont(size=11, weight="bold"),
            state="normal" if exists else "disabled",
            fg_color=BRAND if exists else NEUTRAL, hover_color=BRAND_HI,
            command=lambda p=path: subprocess.Popen(["xdg-open", p]),
        ).pack(side="right")

    def _fill_url(self, url):
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, url)
        if self.history_win is not None and self.history_win.winfo_exists():
            self.history_win.destroy()
        self.history_win = None
        self.url_entry.focus()

    def _clear_history(self):
        self.history = []
        save_history(self.history)
        if self.history_win is not None and self.history_win.winfo_exists():
            self.history_win.destroy()
        self.history_win = None
        self._open_history()

    # ---- yt-dlp version + update -------------------------------------------

    def _refresh_version(self):
        """Runs yt-dlp, so keep it off the UI thread."""
        def work():
            v = ytdlp_version()
            self.after(0, self._show_version, v)
        threading.Thread(target=work, daemon=True).start()

    def _show_version(self, version):
        if not version:
            self.version_var.set("yt-dlp not found")
            self.version_label.configure(text_color=ERROR)
            self.update_btn.configure(fg_color=BRAND, hover_color=BRAND_HI)
            return
        short = ".".join(version.split(".")[:3])
        age = version_age_days(version)
        if age is None:
            self.version_var.set(f"yt-dlp {short}")
            self.version_label.configure(text_color=MUTED)
            self.update_btn.configure(fg_color=NEUTRAL, hover_color=NEUTRAL_HI)
        elif age >= STALE_DAYS:
            self.version_var.set(f"yt-dlp {short} · {age} days old")
            self.version_label.configure(text_color=WARN)
            self.update_btn.configure(fg_color=BRAND, hover_color=BRAND_HI)
        else:
            self.version_var.set(f"yt-dlp {short} · up to date")
            self.version_label.configure(text_color=MUTED)
            self.update_btn.configure(fg_color=NEUTRAL, hover_color=NEUTRAL_HI)

    def _run_update(self):
        if self.proc is not None and self.proc.poll() is None:
            self.status_var.set("Finish the current download first.")
            return
        self.update_btn.configure(state="disabled", text="Updating…")
        self.btn.configure(state="disabled")
        self._clear_log()
        self.title_var.set("")
        self.status_label.configure(text_color=MUTED)
        self.status_var.set("Updating yt-dlp — downloads about 30 MB…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        ok = False
        try:
            proc = subprocess.Popen(
                [UPDATE_CMD], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.after(0, self._append_log, line)
            proc.wait()
            ok = proc.returncode == 0
        except FileNotFoundError:
            self.after(0, self._append_log, f"ERROR: {UPDATE_CMD} not found.")
        except (OSError, subprocess.SubprocessError) as e:
            self.after(0, self._append_log, f"ERROR: {e}")
        self.after(0, self._update_done, ok)

    def _update_done(self, ok):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1 if ok else 0)
        self.update_btn.configure(state="normal", text="Update")
        self.btn.configure(state="normal")
        if ok:
            self.status_label.configure(text_color=SUCCESS)
            self.status_var.set("✓  yt-dlp updated")
            self._append_log("")
            self._append_log("  ✓  YT-DLP UPDATED  ", "success")
        else:
            self.status_label.configure(text_color=ERROR)
            self.status_var.set("✗  Update failed — check the log")
        self._refresh_version()

    # ---- download ----------------------------------------------------------

    def _build_cmd(self, url):
        save_dir = self.folder_var.get()
        mode = self.playlist_mode.get()

        if mode == "Custom range…":
            items = self.items_entry.get().strip()
            if not items:
                raise ValueError("Enter a range like 1-5,8 — or pick another playlist option.")
            playlist_args = ["--yes-playlist", "--playlist-items", items]
        else:
            playlist_args = PLAYLIST_MODES[mode]

        # Only fan out into a per-playlist subfolder when more than one item can
        # land; a single file shouldn't get its own directory.
        if mode in ("Whole playlist", "Custom range…"):
            out = os.path.join(save_dir, "%(playlist_title)s", "%(playlist_index)s - %(title)s.%(ext)s")
        else:
            out = os.path.join(save_dir, "%(title)s.%(ext)s")

        cmd = ["yt-dlp", "--newline", "-o", out] + CONCURRENCY + playlist_args

        if self._is_video():
            # Same stereo-over-5.1 preference as AUDIO_FORMAT: the surround track
            # is ~3x the bytes and most of these get watched on laptop speakers.
            ba = "(ba[audio_channels<=2]/ba)"
            height = re.match(r"(\d+)p", self.quality.get())
            if height:
                h = height.group(1)
                fmt = f"bv*[height<={h}]+{ba}/b[height<={h}]"
            else:
                fmt = f"bv*+{ba}/b"
            cmd += ["-f", fmt, "--merge-output-format", "mp4"]
            if self.embed_meta.get():
                cmd += ["--embed-thumbnail", "--embed-metadata"]
        else:
            # yt-dlp's --audio-quality takes a VBR level (0-9) or a bitrate like
            # "192K"; feed it the bitrate straight from the dropdown.
            kbps = self.quality.get().split()[0]
            cmd += ["-f", AUDIO_FORMAT,
                    "-x", "--audio-format", "mp3", "--audio-quality", f"{kbps}K"]
            if self.embed_meta.get():
                cmd += ["--embed-thumbnail", "--embed-metadata"]

        cmd.append(url)
        return cmd

    def _start_download(self):
        url = self.url_entry.get().strip()
        if not url:
            self.status_var.set("Please paste a YouTube URL first.")
            return

        # Built up front so a bad playlist range reports in the UI rather than
        # blowing up on the worker thread with the buttons stuck disabled.
        try:
            cmd = self._build_cmd(url)
        except ValueError as e:
            self.status_var.set(str(e))
            return

        # A link already fetched at these settings comes back out of history
        # instead of off the network. Playlist runs are exempt: the same list
        # can gain items later, so they always re-run.
        single = self.playlist_mode.get() in SINGLE_MODES
        if single:
            hit = self._history_hit(url)
            if hit:
                self._reuse(hit)
                return
        # Not an exact repeat, but the same video may already be on disk in
        # another format — say so while there is still time to hit Cancel.
        prior = self._history_any(url) if single else None

        self.last_path = None
        self.pending = {
            "url": url,
            "id": video_id(url),
            "kind": "video" if self._is_video() else "audio",
            "quality": self.quality.get(),
            "single": single,
        }

        self.cancelled = False
        self.btn.configure(state="disabled", text="Downloading...")
        self.cancel_btn.configure(state="normal")
        self.progress.set(0)
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.status_label.configure(text_color=MUTED)
        self.status_var.set("Starting...")
        self._clear_log()
        self.title_var.set("")

        if prior:
            kind = "MP4" if prior.get("kind") == "video" else "MP3"
            self._append_log(
                "  ⚠  You already downloaded this as %s %s on %s  "
                % (kind, prior.get("quality", ""), self._fmt_date(prior.get("ts"))),
                "reuse",
            )
            self._append_log(f"     {prior.get('title', '')}")
            self._append_log("     Press Cancel if you don't want it again.")
            self._append_log("")

        threading.Thread(target=self._run, args=(cmd,), daemon=True).start()

    # ---- history -----------------------------------------------------------

    @staticmethod
    def _fmt_date(ts):
        return time.strftime("%d %b %Y", time.localtime(ts)) if ts else "unknown date"

    def _history_hit(self, url):
        """Newest entry for this video at the current kind+quality, if its file
        is still on disk. A file the user deleted must download again."""
        vid = video_id(url)
        if not vid:
            return None
        kind = "video" if self._is_video() else "audio"
        quality = self.quality.get()
        for e in reversed(self.history):
            if (e.get("id") == vid and e.get("kind") == kind
                    and e.get("quality") == quality
                    and e.get("path") and os.path.exists(e["path"])):
                return e
        return None

    def _reuse(self, entry):
        path = entry["path"]
        self._clear_log()
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1)
        self.cancel_btn.configure(state="disabled")
        self.status_label.configure(text_color=REUSE)
        self.status_var.set("✓  Already downloaded — taken from history")
        self._append_log(f"Already in history: {entry.get('title') or os.path.basename(path)}")
        self._append_log(f"Saved at:   {path}")
        self._append_log(f"Downloaded: {self._fmt_date(entry.get('ts'))}")
        self._append_log("")
        self._append_log("  ✓  ALREADY DOWNLOADED  —  nothing re-downloaded  ", "reuse")
        self.url_entry.delete(0, "end")

    def _record_history(self):
        p = self.pending
        if not p or not p.get("id") or not p.get("single"):
            return
        path = self.last_path
        if not path or not os.path.exists(path):
            return
        entry = {
            "id": p["id"], "url": p["url"], "kind": p["kind"],
            "quality": p["quality"], "path": path,
            "title": os.path.splitext(os.path.basename(path))[0],
            "ts": int(time.time()),
        }
        # Drop any older row for the same video+kind+quality so the list stays
        # one row per thing rather than growing on every re-download.
        self.history = [
            e for e in self.history
            if not (e.get("id") == entry["id"] and e.get("kind") == entry["kind"]
                    and e.get("quality") == entry["quality"])
        ]
        self.history.append(entry)
        save_history(self.history)

    def _history_any(self, url):
        """Newest entry for this video in any format. _history_hit only matches
        an exact kind+quality repeat; this catches "same song, different
        settings", which is the case worth warning about rather than skipping."""
        vid = video_id(url)
        if not vid:
            return None
        for e in reversed(self.history):
            if e.get("id") == vid:
                return e
        return None

    def _note_path(self, line):
        """Track the file yt-dlp is writing and surface its name. Runs on the
        worker thread, so UI updates go through after()."""
        for rx in (MOVE_RE, MERGER_RE, DEST_RE, ALREADY_RE):
            m = rx.match(line)
            if m:
                self.last_path = m.group(1)
                name = os.path.splitext(os.path.basename(self.last_path))[0]
                self.after(0, self._show_title, name)
                return

    def _show_title(self, name):
        # Intermediate files carry a .f251-style format suffix; strip it so the
        # name doesn't visibly change when yt-dlp moves to the final file.
        self.title_var.set(re.sub(r"\.f\d+$", "", name))

    def _cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.status_var.set("Cancelling…")

    def _run(self, cmd):
        try:
            self.after(0, self._append_log, "$ " + " ".join(cmd) + "\n")

            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )

            for line in self.proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                self.after(0, self._append_log, line)
                self._note_path(line)

                # ffmpeg re-encodes Opus -> MP3 and muxes video+audio without
                # emitting progress, so the UI would otherwise sit frozen at
                # 100% for the whole conversion.
                if line.startswith("[ExtractAudio]"):
                    self.after(0, self._set_busy, "Converting to MP3 — this takes a while on long tracks…")
                    continue
                if line.startswith("[Merger]"):
                    self.after(0, self._set_busy, "Merging video + audio…")
                    continue
                if line.startswith(("[EmbedThumbnail]", "[Metadata]")):
                    self.after(0, self._set_busy, "Writing tags…")
                    continue

                pct_match = re.search(r"(\d+\.\d+)%", line)
                if pct_match:
                    pct = float(pct_match.group(1)) / 100
                    self.after(0, self._set_progress, pct, line.strip())

            self.proc.wait()
            self.after(0, self._done, self.proc.returncode == 0)

        except FileNotFoundError:
            self.after(0, self._append_log, "ERROR: yt-dlp not found. Run: pip install yt-dlp")
            self.after(0, self._done, False)

    def _set_busy(self, message):
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.status_var.set(message)

    def _set_progress(self, pct, label):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(pct)
        self.status_var.set(label)

    def _done(self, success):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1 if success else 0)
        self.cancel_btn.configure(state="disabled")
        self.btn.configure(
            state="normal",
            text="Download Video" if self._is_video() else "Download MP3",
        )
        if self.cancelled:
            self.status_label.configure(text_color=MUTED)
            self.status_var.set("Cancelled.")
        elif success:
            self._record_history()
            folder = self.folder_var.get()
            self.status_label.configure(text_color=SUCCESS)
            self.status_var.set(f"✓  Done — saved to {folder}")
            # Highlighted completion banner so a finished download is obvious
            # at a glance amid the yt-dlp log noise.
            self._append_log("", )
            self._append_log(f"  ✓  DOWNLOAD COMPLETE  —  saved to {folder}  ", "success")
            self.url_entry.delete(0, "end")
        else:
            self.status_label.configure(text_color=ERROR)
            self.status_var.set("✗  Download failed — check the log below.")
            self._append_log("", )
            self._append_log("  ✗  DOWNLOAD FAILED  —  see the log above  ", "error")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _append_log(self, line, tag=None):
        self.log.configure(state="normal")
        if tag:
            self.log._textbox.insert("end", line + "\n", tag)
        else:
            self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


if __name__ == "__main__":
    App().mainloop()
