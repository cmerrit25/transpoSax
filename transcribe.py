"""Transcribe a monophonic solo (audio) into a concert-pitch music21 score.

Pipeline: pYIN pitch tracking -> tuning correction -> note segmentation
(split on pitch changes and onsets) -> tempo/beat estimation -> rhythmic
quantization -> music21 Part with key signature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np
from music21 import key, meter, note, stream, tempo
from scipy.signal import medfilt

log = logging.getLogger(__name__)

SR = 22050
HOP = 256                       # ~11.6 ms per frame
FMIN = librosa.note_to_hz("A1")  # below low A on baritone (concert)
FMAX = librosa.note_to_hz("C7")  # above soprano altissimo

GRIDS = {"8": 2, "16": 4, "triplet": 3}  # subdivisions per quarter note


@dataclass
class NoteEvent:
    midi: int
    start: float  # seconds
    end: float


def _frames_to_time(f) -> float:
    return float(librosa.frames_to_time(f, sr=SR, hop_length=HOP))


def detect_notes(y: np.ndarray, min_note: float = 0.06) -> list[NoteEvent]:
    f0, voiced, _ = librosa.pyin(y, fmin=FMIN, fmax=FMAX, sr=SR,
                                 frame_length=2048, hop_length=HOP)
    voiced = voiced & ~np.isnan(f0)
    if not voiced.any():
        return []

    midi = librosa.hz_to_midi(np.where(voiced, f0, 1.0))
    # Recordings are rarely exactly A=440; remove the overall tuning offset.
    dev = midi[voiced] - np.round(midi[voiced])
    tuning = float(np.median(dev))
    log.info("Tuning offset: %+.0f cents", tuning * 100)

    pitch = np.where(voiced, np.round(midi - tuning), -1).astype(int)
    pitch = medfilt(pitch, kernel_size=5).astype(int)  # kill vibrato/scoop flicker

    onsets = set(librosa.onset.onset_detect(y=y, sr=SR, hop_length=HOP, units="frames").tolist())

    events: list[NoteEvent] = []
    min_frames = max(1, int(round(min_note * SR / HOP)))
    i, n = 0, len(pitch)
    while i < n:
        if pitch[i] < 0:
            i += 1
            continue
        j = i + 1
        # extend the run while pitch holds; break on a re-articulation (onset)
        while j < n and pitch[j] == pitch[i] and not (j in onsets and j - i >= min_frames):
            j += 1
        if j - i >= min_frames:
            events.append(NoteEvent(int(pitch[i]), _frames_to_time(i), _frames_to_time(j)))
        i = j

    # Merge same-pitch fragments separated by tiny gaps with no onset between them.
    merged: list[NoteEvent] = []
    for ev in events:
        if (merged and ev.midi == merged[-1].midi and ev.start - merged[-1].end < 0.04
                and not any(abs(_frames_to_time(o) - ev.start) < 0.03 for o in onsets)):
            merged[-1].end = ev.end
        else:
            merged.append(ev)

    # A short same-pitch blip right before silence is the release of the previous
    # note (a false onset as the tone decays), not a new note.
    cleaned: list[NoteEvent] = []
    for idx, ev in enumerate(merged):
        nxt = merged[idx + 1] if idx + 1 < len(merged) else None
        ends_phrase = nxt is None or nxt.start - ev.end > 0.05
        if (cleaned and ev.midi == cleaned[-1].midi and ev.start - cleaned[-1].end < 0.02
                and ev.end - ev.start < 0.1 and ends_phrase):
            cleaned[-1].end = ev.end
        else:
            cleaned.append(ev)
    return cleaned


def estimate_beat(y: np.ndarray) -> tuple[float, float]:
    """Return (bpm, time of first beat in seconds)."""
    bpm, beats = librosa.beat.beat_track(y=y, sr=SR, hop_length=HOP)
    bpm = float(np.atleast_1d(bpm)[0])
    first = _frames_to_time(beats[0]) if len(beats) else 0.0
    return bpm, first


def quantize(events: list[NoteEvent], bpm: float, first_beat: float, div: int):
    """Map note times onto a beat grid. Returns [(midi, offset_q, dur_q)]."""
    beat = 60.0 / bpm
    phase = first_beat % beat
    origin = phase - beat if phase > 1e-3 else 0.0  # grid point at/before t=0

    out = []
    for ev in events:
        s = round((ev.start - origin) / beat * div) / div
        e = round((ev.end - origin) / beat * div) / div
        if e <= s:
            e = s + 1 / div
        out.append([ev.midi, s, e])
    # resolve overlaps / duplicates created by snapping
    cleaned = []
    for m, s, e in out:
        if cleaned and s < cleaned[-1][2]:
            if s <= cleaned[-1][1]:
                continue  # same slot as previous note; keep the earlier one
            cleaned[-1][2] = s
        cleaned.append([m, s, e])
    return [(m, s, e - s) for m, s, e in cleaned]


def build_score(qnotes, bpm: float) -> stream.Score:
    part = stream.Part()
    part.insert(0, meter.TimeSignature("4/4"))
    part.insert(0, tempo.MetronomeMark(number=round(bpm)))
    for m, off, dur in qnotes:
        part.insert(off, note.Note(m, quarterLength=dur))

    if qnotes:
        k = part.analyze("key")
        part.insert(0, key.KeySignature(k.sharps))
        if k.sharps < 0:  # flat keys: spell black keys as flats (Bb, not A#)
            for n in part.recurse().notes:
                if n.pitch.accidental is not None and n.pitch.accidental.alter > 0:
                    n.pitch = n.pitch.getEnharmonic()

    score = stream.Score()
    score.insert(0, part.makeNotation())
    return score


def transcribe(
    audio_path: Path,
    bpm: float | None = None,
    grid: str = "16",
    beat_source: Path | None = None,
) -> tuple[stream.Score, float]:
    """Transcribe a monophonic recording to a concert-pitch Score.

    beat_source: audio to estimate tempo from (the full mix keeps the drums,
    which track tempo better than an isolated sax stem).
    """
    y, _ = librosa.load(str(audio_path), sr=SR, mono=True)
    events = detect_notes(y)
    log.info("Detected %d notes", len(events))

    if beat_source is not None and beat_source != audio_path:
        y_beat, _ = librosa.load(str(beat_source), sr=SR, mono=True)
    else:
        y_beat = y
    est_bpm, first_beat = estimate_beat(y_beat)
    if bpm is None:
        bpm = est_bpm
        log.info("Estimated tempo: %.0f BPM", bpm)
    else:
        first_beat = events[0].start if events else 0.0

    qnotes = quantize(events, bpm, first_beat, GRIDS[grid])
    return build_score(qnotes, bpm), bpm
