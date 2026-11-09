"""Transcription tests on a synthesized, slightly out-of-tune, vibrato-laden "sax" line."""

import numpy as np
import soundfile as sf
from music21 import converter, note

from audio_source import cut_clip, parse_time
from main import main
from transcribe import SR, transcribe

BPM = 120
# (concert pitch, beats) -- a Bb-major lick with a repeated note to test re-articulation
MELODY = [("B-3", 1), ("D4", 0.5), ("F4", 0.5), ("A4", 0.5), ("B-4", 0.5), ("B-4", 1),
          ("G4", 0.5), ("F4", 0.5), ("E-4", 1), ("D4", 2)]


def synth(path, melody=MELODY, bpm=BPM, detune_cents=20, lead_in=0.5):
    beat = 60 / bpm
    parts = [np.zeros(int(lead_in * SR))]
    for name, beats in melody:
        f = note.Note(name).pitch.frequency * 2 ** (detune_cents / 1200)
        n = int(beats * beat * SR)
        t = np.arange(n) / SR
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t)
        phase = 2 * np.pi * f * np.cumsum(vib) / SR
        tone = sum(a * np.sin(k * phase) for k, a in [(1, 1), (2, .5), (3, .35), (4, .2), (5, .1)])
        env = np.minimum(1, t / 0.015) * np.minimum(1, (n - np.arange(n)) / (0.04 * SR))
        parts.append(0.25 * tone * env)
    sf.write(path, np.concatenate(parts + [np.zeros(SR // 2)]), SR)


def pitches(score):
    return [n.pitch.nameWithOctave for n in score.recurse().getElementsByClass(note.Note)
            if not n.tie or n.tie.type == "start"]


def test_parse_time():
    assert parse_time("83") == 83
    assert parse_time("1:23") == 83
    assert parse_time("1:02:03") == 3723
    assert parse_time("1:23.5") == 83.5


def test_transcribes_pitches_and_rhythm(tmp_path):
    wav = tmp_path / "lick.wav"
    synth(wav)
    score, _ = transcribe(wav, bpm=BPM, grid="8")
    expected = [n for n, _ in MELODY]
    assert pitches(score) == expected
    durs = [n.quarterLength for n in score.recurse().getElementsByClass(note.Note)]
    assert sum(durs) == sum(b for _, b in MELODY)


def test_tempo_estimated(tmp_path):
    wav = tmp_path / "lick.wav"
    synth(wav, melody=MELODY * 3)
    _, bpm = transcribe(wav, grid="8")
    assert abs(bpm - BPM) < 6 or abs(bpm - 2 * BPM) < 12  # octave errors are normal


def test_cut_clip(tmp_path):
    wav = tmp_path / "lick.wav"
    synth(wav)
    clip = cut_clip(wav, tmp_path / "clip.wav", 0.5, 2.5)
    y, sr = sf.read(clip)
    assert abs(len(y) / sr - 2.0) < 0.05


def test_cli_audio_to_alto(tmp_path):
    wav = tmp_path / "lick.wav"
    synth(wav)
    out = tmp_path / "out.musicxml"
    assert main([str(wav), "--bpm", str(BPM), "--grid", "8", "-o", str(out), "--midi"]) == 0
    alto = converter.parse(str(out))
    # concert Bb3 -> alto G4 (up a major 6th)
    assert pitches(alto)[:3] == ["G4", "B4", "D5"]
    assert (tmp_path / "out_concert.mid").exists()
