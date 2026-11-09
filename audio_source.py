"""Fetch audio from YouTube / Spotify / a local file and cut out a time range."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from pathlib import Path

import imageio_ffmpeg

log = logging.getLogger(__name__)

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".webm", ".mp4"}


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def parse_time(t: str | float | None) -> float | None:
    """'83', '1:23', '1:02:03', '1:23.5' -> seconds."""
    if t is None or t == "":
        return None
    if isinstance(t, (int, float)):
        return float(t)
    parts = t.strip().split(":")
    if len(parts) > 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise ValueError(f"Bad time {t!r}; use seconds (83) or m:ss (1:23) or h:mm:ss")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def is_spotify(url: str) -> bool:
    return "open.spotify.com/" in url or url.startswith("spotify:")


def _retry(fn, what: str, attempts: int = 3):
    """Network calls flake; retry with exponential backoff and log every failure."""
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - re-raised after the last attempt
            log.warning("%s failed (attempt %d/%d): %s", what, i, attempts, e)
            if i == attempts:
                raise
            time.sleep(2 ** i)


def spotify_to_youtube(url: str) -> str:
    """Spotify audio is DRM-protected, so find the same song on YouTube via spotdl."""

    def lookup() -> str:
        proc = subprocess.run(
            [sys.executable, "-m", "spotdl", "url", url, "--ffmpeg", ffmpeg_exe()],
            capture_output=True, text=True, timeout=180,
        )
        urls = re.findall(r"https?://\S*youtube\.com/watch\?v=[\w-]+", proc.stdout)
        if not urls:
            raise RuntimeError(f"spotdl found no match (exit {proc.returncode}): "
                               f"{(proc.stderr or proc.stdout).strip()[-500:]}")
        return urls[-1]

    return _retry(lookup, "Spotify lookup")


def download_audio(url: str, workdir: Path) -> Path:
    """Download the audio track of a YouTube (or other yt-dlp supported) URL."""
    import yt_dlp

    workdir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "ffmpeg_location": ffmpeg_exe(),
        "overwrites": True,
    }

    def dl() -> Path:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            log.info("Downloaded: %s", info.get("title", url))
            return Path(ydl.prepare_filename(info))

    path = _retry(dl, "Download")
    if not path.exists():
        raise RuntimeError(f"Download reported success but {path} is missing")
    return path


def cut_clip(src: Path, dst: Path, start: float | None, end: float | None) -> Path:
    """Decode src to a mono 44.1 kHz WAV, keeping only [start, end]."""
    if start is not None and end is not None and end <= start:
        raise ValueError("--end must be after --start")
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if end is not None:
        cmd += ["-t", f"{end - (start or 0):.3f}"]
    cmd += ["-ac", "1", "-ar", "44100", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(f"ffmpeg failed to cut audio: {proc.stderr.strip()}")
    return dst


def get_clip(source: str, workdir: Path, start: float | None, end: float | None) -> Path:
    """Resolve a URL or local audio file to a trimmed mono WAV in workdir."""
    workdir.mkdir(parents=True, exist_ok=True)
    if is_url(source) or source.startswith("spotify:"):
        if is_spotify(source):
            yt = spotify_to_youtube(source)
            log.info("Spotify track matched on YouTube: %s", yt)
            source = yt
        audio = download_audio(source, workdir)
    else:
        audio = Path(source)
        if not audio.exists():
            raise FileNotFoundError(source)
    return cut_clip(audio, workdir / "clip.wav", start, end)


def isolate_sax(clip: Path, workdir: Path) -> Path:
    """Separate the clip with Demucs and return the stem the sax ends up in.

    Demucs has no dedicated sax stem; horns land in "other" (with keys/guitar
    split out by the 6-stem model), so we use htdemucs_6s's "other" stem.
    """
    import importlib.util
    if importlib.util.find_spec("demucs") is None:
        raise RuntimeError("--isolate needs Demucs: pip install demucs")
    out = workdir / "separated"
    model = "htdemucs_6s"
    proc = subprocess.run(
        [sys.executable, "-m", "demucs", "-n", model, "--two-stems", "other",
         "-o", str(out), str(clip)],
        capture_output=True, text=True,
    )
    stem = out / model / clip.stem / "other.wav"
    if proc.returncode != 0 or not stem.exists():
        raise RuntimeError(f"Demucs separation failed (exit {proc.returncode}): "
                           f"{proc.stderr.strip()[-800:]}")
    return stem
